"""扫描探测优化测试（2026-08-25：Range 反代 + probe_mode 三策略 + 批量事务）。

Range 反代：ffprobe 经本地 HTTP Range 代理读远程文件——传输量从整文件降到
元数据字节；MP4 moov 在尾部时 ffprobe 会发尾部 Range 请求（本测试用真实
ffprobe + wsgidav 验证语义与结果一致）。
"""
import http.client
import subprocess
import time

import pytest

from conftest import FFPROBE, make_sample_video, requires_ffprobe


@requires_ffprobe
def test_probe_proxy_range_semantics(env):
    """反代 HTTP 语义：HEAD 尺寸 / Range 正确切片 / 一次性 token。"""
    from kindo.media.probe_proxy import get_probe_proxy
    from kindo.media.storage import LocalMountedDirectoryProvider

    make_sample_video(env.media_dir / "片.mp4", seconds=4)
    provider = LocalMountedDirectoryProvider("t", env.media_dir, True)
    size = provider.stat("片.mp4").size
    assert size > 1000

    proxy = get_probe_proxy()
    url = proxy.url_for(provider, "片.mp4", size)
    token = url.split("/p/")[1].split("/")[0]
    port = int(url.split(":")[2].split("/")[0])

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    # HEAD
    conn.request("HEAD", f"/p/{token}/x")
    r = conn.getresponse()
    r.read()
    assert r.status == 200 and int(r.headers["Content-Length"]) == size
    assert r.headers["Accept-Ranges"] == "bytes"
    # Range 头部 8 字节
    conn.request("GET", f"/p/{token}/x", headers={"Range": "bytes=0-7"})
    r = conn.getresponse()
    assert r.status == 206 and len(r.read()) == 8
    assert r.headers["Content-Range"] == f"bytes 0-7/{size}"
    # Range 尾部后缀
    conn.request("GET", f"/p/{token}/x", headers={"Range": "bytes=-16"})
    r = conn.getresponse()
    assert r.status == 206 and len(r.read()) == 16
    # 非法 Range → 416
    conn.request("GET", f"/p/{token}/x", headers={"Range": f"bytes={size + 10}-"})
    r = conn.getresponse()
    r.read()
    assert r.status == 416
    conn.close()
    # 撤销后 404
    proxy.revoke(token)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("HEAD", f"/p/{token}/x")
    assert conn.getresponse().status == 404


@requires_ffprobe
def test_probe_via_url_matches_direct(env, tmp_path):
    """ffprobe 经反代 URL 与直接探测本地文件结果一致（真实 ffprobe）。"""
    from kindo.media.probe import probe_media
    from kindo.media.probe_proxy import get_probe_proxy
    from kindo.media.storage import LocalMountedDirectoryProvider

    make_sample_video(env.media_dir / "片.mp4", seconds=4)
    provider = LocalMountedDirectoryProvider("t", env.media_dir, True)
    size = provider.stat("片.mp4").size

    direct = probe_media(env.media_dir / "片.mp4", FFPROBE)
    proxy = get_probe_proxy()
    url = proxy.url_for(provider, "片.mp4", size)
    try:
        via_url = probe_media(url, FFPROBE, timeout=45.0)
    finally:
        proxy.revoke(url.split("/p/")[1].split("/")[0])

    assert via_url.duration_ms == pytest.approx(direct.duration_ms, rel=0.02)
    assert via_url.container == direct.container
    assert via_url.video_codec == direct.video_codec
    assert len(via_url.audio_streams) == len(direct.audio_streams)


@requires_ffprobe
def test_webdav_probe_modes(env, webdav_server_from_purge):
    """三种探测策略经真实 wsgidav：range 拿到时长 / skip 可播时长 0 /
    payload 带 probe_mode 且可编辑。"""

    from conftest import FFPROBE  # noqa: F401

    dav_port = webdav_server_from_purge[1]
    env.bootstrap_admin()
    (webdav_server_from_purge[0] / "媒体库").mkdir(exist_ok=True)
    make_sample_video(webdav_server_from_purge[0] / "媒体库" / "集.mp4", seconds=4)

    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "webdav", "url": f"http://127.0.0.1:{dav_port}/",
        "path": "媒体库", "label": "探测测试", "probe_mode": "range",
    })
    assert r.status_code == 200, r.text
    mount = r.json()
    assert mount["probe_mode"] == "range"

    # 扫描（range 模式）
    r = env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job
    items = env.client.get("/api/v1/admin/media?limit=10",
                           headers=env.admin_headers()).json()["items"]
    m = items[0]
    assert m["duration_ms"] > 0, "range 探测应拿到时长"
    assert m["playable"] is True

    # 编辑为 skip → 重扫 → 时长未知仍可播
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mount['mount_id']}",
                         json={"probe_mode": "skip"}, headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["probe_mode"] == "skip"
    from kindo.models import Media

    with env.db.session() as s:
        row = s.query(Media).filter(Media.mount_id == mount["storage_mount_id"]).one()
        row.size_bytes += 1  # 破坏增量，强制走探测路径
        s.commit()
    # 目录未变化会被剪枝跳过（优化 D）——用 force_full 确保文件重新处理
    r = env.client.post(
        f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan?force_full=true",
        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done"
    with env.db.session() as s:
        row = s.query(Media).filter(Media.mount_id == mount["storage_mount_id"]).one()
        assert row.playable is True
        assert (row.probe_json or {}).get("skipped") == "probe_mode_skip"
    # 非法值 400
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mount['mount_id']}",
                         json={"probe_mode": "fast"}, headers=env.admin_headers())
    assert r.status_code in (400, 422)


# 复用 purge 测试的 wsgidav fixture（返回 (root, port)）
@pytest.fixture()
def webdav_server_from_purge(tmp_path_factory):

    # 动态起一个 wsgidav（不依赖 purge 测试文件内部 fixture 实例）
    import pathlib as _pl

    import httpx as _hx

    dav_root = tmp_path_factory.mktemp("dav-probe")
    wsgidav_exe = _pl.Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "wsgidav.exe"
    cmd = [str(wsgidav_exe)] if wsgidav_exe.exists() else ["wsgidav"]
    proc = subprocess.Popen(
        cmd + ["--host", "127.0.0.1", "--port", "18147",
               "--root", str(dav_root), "--auth", "anonymous", "--server", "cheroot"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            _hx.get("http://127.0.0.1:18147/", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("wsgidav 启动失败")
    yield (dav_root, 18147)
    proc.kill()

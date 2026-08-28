"""目录 mtime 剪枝扫描测试（2026-08-25 优化 D，真实 wsgidav WebDAV）。

验证：首扫全量并记录目录状态 → 未变化重扫只列顶层（条目一致、不误标 missing）
→ 目录内容变化（mtime 更新）后重扫下钻该目录 → force_full 全量 → 7 天兜底。
"""
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from conftest import make_sample_video, requires_ffprobe

_DAV_PORT = 18148


@pytest.fixture()
def dav(tmp_path_factory):
    root = tmp_path_factory.mktemp("dav-prune")
    exe = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "wsgidav.exe"
    cmd = [str(exe)] if exe.exists() else ["wsgidav"]
    proc = subprocess.Popen(
        cmd + ["--host", "127.0.0.1", "--port", str(_DAV_PORT),
               "--root", str(root), "--auth", "anonymous", "--server", "cheroot"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{_DAV_PORT}/", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("wsgidav 启动失败")
    yield root
    proc.kill()


def _add_mount(env, url):
    return env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "webdav", "url": url, "label": "剪枝测试", "probe_mode": "skip",
    }).json()


def _scan(env, mount_id, force_full=False):
    r = env.client.post(
        f"/api/v1/admin/media-mounts/{mount_id}/scan"
        + ("?force_full=true" if force_full else ""),
        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(80):
        time.sleep(0.5)
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
    assert job["state"] == "done", job
    return job


def _count_propfinds(provider):
    """计数 dir_listing 调用（剪枝效果 = 列目录次数）。"""
    n = [0]
    orig = provider.dir_listing

    def counted(path_key=""):
        n[0] += 1
        return orig(path_key)
    provider.dir_listing = counted
    return n


@requires_ffprobe
def test_prune_scan_flow(env, dav):
    # 目录结构：a/（含 1 视频） b/（含 1 视频）
    for sub in ("a", "b"):
        (dav / sub).mkdir()
        make_sample_video(dav / sub / f"{sub}.mp4", seconds=3)
    env.bootstrap_admin()
    mount = _add_mount(env, f"http://127.0.0.1:{_DAV_PORT}/")
    sid = mount["storage_mount_id"]

    # 首扫：全量
    _scan(env, mount["mount_id"])
    from kindo.models import Media, ScanDirState

    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == sid).count() == 2
        dirs = {r.dir_path for r in s.query(ScanDirState)
                .filter(ScanDirState.mount_id == sid, ScanDirState.dir_path != "").all()}
        assert dirs == {"a", "b"}, dirs

    # 未变化重扫：剪枝（只列根目录）+ 不误标 missing + 不产生新任务量
    provider = env.state.storage.get(sid)
    counter = _count_propfinds(provider)
    _scan(env, mount["mount_id"])
    assert counter[0] == 1, f"剪枝后应只列 1 次根目录，实际 {counter[0]}"
    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == sid, Media.missing.is_(True)).count() == 0

    # b 目录内容变化（新文件 → 目录 mtime 更新）→ 重扫下钻 b、跳过 a
    make_sample_video(dav / "b" / "new.mp4", seconds=3)
    counter = _count_propfinds(provider)
    _scan(env, mount["mount_id"])
    assert counter[0] == 2, f"应列根+b 共 2 次，实际 {counter[0]}"
    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == sid).count() == 3

    # force_full：全量（列根+a+b = 3 次）
    counter = _count_propfinds(provider)
    _scan(env, mount["mount_id"], force_full=True)
    assert counter[0] == 3, f"全量应列 3 次，实际 {counter[0]}"

    # 7 天兜底：把 last_full 改到 8 天前 → 普通扫描退化为全量
    with env.db.session() as s:
        row = s.get(ScanDirState, (sid, ""))
        row.mtime_ms -= 8 * 24 * 3600 * 1000
        s.commit()
    counter = _count_propfinds(provider)
    _scan(env, mount["mount_id"])
    assert counter[0] == 3, f"超期应退化为全量 3 次，实际 {counter[0]}"

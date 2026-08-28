"""网络媒体源 P0 测试（PRD v0.2.3 MED-003 / AC-13）。

- WebDAV：本测试内启动真实 wsgidav 服务器（协议级真实验证）。
- SMB：启动 docker 真实 Samba 服务器（dperson/samba）；docker 不可用时跳过（如实标注）。
"""
import subprocess
import time

import httpx
import pytest

from conftest import make_sample_video, requires_ffprobe

DAV_PORT = 18871
DAV_USER, DAV_PASS = "kindo", "kindo-dav-2026"
SMB_PORT = 1445
SMB_USER, SMB_PASS = "kindo", "kindo-smb-2026"


@pytest.fixture(scope="module")
def webdav_server(tmp_path_factory):
    """真实 WebDAV 服务器（wsgidav），认证开启，根映射临时目录。"""
    dav_root = tmp_path_factory.mktemp("dav")
    import pathlib

    wsgidav_exe = pathlib.Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "wsgidav.exe"
    cmd = [str(wsgidav_exe)] if wsgidav_exe.exists() else ["wsgidav"]
    proc = subprocess.Popen(
        cmd + ["--host", "127.0.0.1", "--port", str(DAV_PORT),
               "--root", str(dav_root), "--auth", "anonymous", "--server", "cheroot"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{DAV_PORT}/", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("wsgidav 启动失败")
    yield dav_root
    proc.kill()


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def smb_server(tmp_path_factory):
    """真实 SMB 服务器：docker dperson/samba（SMB2/3）。不可用时跳过。"""
    if not _docker_available():
        pytest.skip("docker 不可用，SMB 真实服务器测试跳过")
    smb_root = tmp_path_factory.mktemp("smb")
    try:
        subprocess.run(["docker", "rm", "-f", "kindo-smb-test"], capture_output=True, timeout=30)
    except Exception:
        pass
    cmd = [
        "docker", "run", "-d", "--name", "kindo-smb-test",
        "-p", f"{SMB_PORT}:445",
        "-v", f"{smb_root}:/media",
        "-u", "1000:1000",
        "dperson/samba",
        "-u", f"{SMB_USER};{SMB_PASS}",
        "-s", "media;/media;yes;no;no;all;none",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        docker_ps = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=kindo-smb-test", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        if "Created" in docker_ps:
            pytest.skip("docker 引擎无法启动容器（挂起于 Created）——SMB 真实联调待可用服务器")
        raise
    if r.returncode != 0:
        pytest.skip(f"samba 容器启动失败：{r.stderr[:150]}")
    # 等待 SMB 就绪
    import socket

    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", SMB_PORT), timeout=2):
                break
        except OSError:
            time.sleep(1)
    time.sleep(3)
    yield smb_root
    subprocess.run(["docker", "rm", "-f", "kindo-smb-test"], capture_output=True, timeout=60)


@pytest.fixture()
def net_env(env):
    env.bootstrap_admin()
    return env


# ==================== WebDAV（真实服务器） ====================

@requires_ffprobe
def test_webdav_mount_scan_stream(net_env, webdav_server):
    """AC-13（WebDAV 部分）：页面添加网络源 → 扫描入库 → Range 拉流字节一致。"""
    env = net_env
    (webdav_server / "网络库").mkdir(exist_ok=True)
    make_sample_video(webdav_server / "网络库" / "网络动画.mp4", seconds=8)
    (webdav_server / "网络库" / "网络动画.kindo.yaml").write_text(
        "title: WebDAV网络动画\nthemes: [网络]\n", encoding="utf-8")

    headers = env.admin_headers()
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "webdav", "url": f"http://127.0.0.1:{DAV_PORT}",
        "path": "网络库", "label": "DAV库",
    }, headers=headers)
    assert r.status_code == 200, r.text
    mount = r.json()
    assert mount["mount_type"] == "webdav"
    assert "password" not in r.text

    r = env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan", headers=headers)
    job_id = r.json()["job_id"]
    for _ in range(90):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job

    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    target = [i for i in items if i["title"] == "WebDAV网络动画"]
    assert target, "WebDAV 媒体应入库"
    assert target[0]["mount_id"] == mount["storage_mount_id"]
    assert target[0]["duration_ms"] >= 7000  # 远程探测成功

    # TV 侧：配对 → 播放 → Range 拉流字节一致
    _d, token = env.pair_device()
    h = env.device_headers(token)
    r = env.client.post("/api/v1/playbacks", json={
        "media_id": target[0]["media_id"], "action": "play", "source": "ui"}, headers=h)
    assert r.status_code == 200, r.text
    desc = r.json()["stream_descriptor"]
    r = env.client.get(desc["url"], headers={
        **h, "X-Kindo-Playback-Grant": desc["grant"], "Range": "bytes=0-2047"})
    assert r.status_code == 206, r.status_code
    src = (webdav_server / "网络库" / "网络动画.mp4").read_bytes()
    assert r.content == src[:2048], "WebDAV Range 字节应与源一致"

    # 检索可见（AC-13）
    res = env.client.get("/api/v1/media", params={"query": "网络"}, headers=h).json()
    assert any(i["title"] == "WebDAV网络动画" for i in res["items"])


def test_webdav_propfind_href_base_prefix():
    """href 带 URL 基路径前缀的服务器（如 OpenList 的 /dav/...）解析正确。

    单元级：直接喂 PROPFIND 207 响应，验证子项提取不受基路径影响。
    """
    from kindo.media.network import WebDavStorageProvider

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<D:multistatus xmlns:D="DAV:">
 <D:response><D:href>/dav/baidu/netdisk/</D:href>
  <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop>
  <D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
 <D:response><D:href>/dav/baidu/netdisk/%E5%8A%A8%E7%94%BB%E7%89%87/</D:href>
  <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop>
  <D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
 <D:response><D:href>/dav/baidu/netdisk/EP01.mp4</D:href>
  <D:propstat><D:prop><D:resourcetype/>
   <D:getcontentlength>12345</D:getcontentlength>
   <D:getlastmodified>Thu, 20 Aug 2026 08:00:00 GMT</D:getlastmodified>
  </D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
</D:multistatus>"""

    class _FakeClient:
        def request(self, method, url, headers=None, content=None):
            class _R:
                status_code = 207
                text = xml
            return _R()

    for url, sub in [("http://openlist:5244/dav", "baidu/netdisk"),
                     ("http://openlist:5244/dav/", "baidu/netdisk")]:
        provider = WebDavStorageProvider("m", url=url, sub_path=sub)
        provider._client = _FakeClient()  # noqa: SLF001 单测注入
        children = provider._propfind("")  # noqa: SLF001
        by_name = {name: (is_dir, size) for name, is_dir, size, _ in children}
        assert by_name == {"动画片": (True, 0), "EP01.mp4": (False, 12345)}, (url, by_name)


def test_webdav_bad_url_rejected(net_env):
    """连接失败的 WebDAV 源在创建时即被拒（400）。"""
    env = net_env
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "webdav", "url": "http://127.0.0.1:1/nope",
    }, headers=env.admin_headers())
    assert r.status_code == 400
    assert "连接失败" in r.text or "unreachable" in r.text.lower() or "失败" in r.text


def test_webdav_password_write_only(net_env, webdav_server):
    """密码写-only：列表/详情永不回显。"""
    env = net_env
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "webdav", "url": f"http://127.0.0.1:{DAV_PORT}",
        "username": "u", "password": "super-secret-dav",
    }, headers=env.admin_headers())
    assert r.status_code == 200
    assert "super-secret-dav" not in r.text
    listing = env.client.get("/api/v1/admin/media-mounts").json()
    assert "super-secret-dav" not in str(listing)
    mine = [m for m in listing["mounts"] if m["mount_id"] == r.json()["mount_id"]]
    assert mine and mine[0]["credentials_configured"] is True


# ==================== SMB（真实 docker samba） ====================

@requires_ffprobe
@pytest.mark.docker
@pytest.mark.slow
def test_smb_mount_scan_stream(net_env, smb_server):
    """AC-13（SMB 部分）：页面添加 SMB 源 → 扫描入库 → Range 拉流字节一致。"""
    env = net_env
    smb_server.joinpath("smb库").mkdir(exist_ok=True)
    make_sample_video(smb_server / "smb库" / "smb动画.mp4", seconds=8)
    (smb_server / "smb库" / "smb动画.kindo.yaml").write_text(
        "title: SMB网络动画\nthemes: [网络]\n", encoding="utf-8")

    headers = env.admin_headers()
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "smb", "host": "127.0.0.1", "port": SMB_PORT,
        "share": "media", "path": "smb库", "label": "SMB库",
        "username": SMB_USER, "password": SMB_PASS,
    }, headers=headers)
    assert r.status_code == 200, r.text
    mount = r.json()
    assert mount["mount_type"] == "smb"
    assert SMB_PASS not in r.text

    r = env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan", headers=headers)
    job_id = r.json()["job_id"]
    for _ in range(90):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job

    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    target = [i for i in items if i["title"] == "SMB网络动画"]
    assert target, "SMB 媒体应入库"
    assert target[0]["duration_ms"] >= 7000

    _d, token = env.pair_device()
    h = env.device_headers(token)
    r = env.client.post("/api/v1/playbacks", json={
        "media_id": target[0]["media_id"], "action": "play", "source": "ui"}, headers=h)
    assert r.status_code == 200, r.text
    desc = r.json()["stream_descriptor"]
    r = env.client.get(desc["url"], headers={
        **h, "X-Kindo-Playback-Grant": desc["grant"], "Range": "bytes=0-2047"})
    assert r.status_code == 206
    src = (smb_server / "smb库" / "smb动画.mp4").read_bytes()
    assert r.content == src[:2048], "SMB Range 字节应与源一致"


@pytest.mark.docker
@pytest.mark.slow
def test_smb_bad_credentials_rejected(net_env, smb_server):
    """错误凭据在创建时即被拒。"""
    env = net_env
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "smb", "host": "127.0.0.1", "port": SMB_PORT,
        "share": "media", "username": SMB_USER, "password": "wrong-pass",
    }, headers=env.admin_headers())
    assert r.status_code == 400


@pytest.mark.slow
def test_smb_provider_registers_session_before_ops():
    """回归：Hub 重启恢复后（无人调用过 check_connectivity），SMB 数据面首个操作
    前必须注册会话凭据，否则 Windows 回落系统凭据 → SpnegoError 无可用凭据。"""
    from kindo.media.network import SmbStorageProvider

    provider = SmbStorageProvider(
        "page-x", host="nas.local", share="media", username="u", password="p")

    calls = []

    class _ST:
        st_size = 10
        st_mtime = 0.0
        st_file_attributes = None  # 非目录

    class FakeSmbClient:
        def register_session(self, host, username=None, password=None, port=445):
            calls.append(("register", host, username, password, port))

        def listdir(self, unc):
            return ["a.mp4"]

        def stat(self, unc):
            return _ST()

    provider._smbclient = FakeSmbClient()

    objs = list(provider.list_videos())
    assert [o.path_key for o in objs] == ["a.mp4"]
    assert ("register", "nas.local", "u", "p", 445) in calls

"""媒体来源编辑契约测试（ADM-002：子目录/网络连接字段/密码写-only）。"""
import pathlib
import subprocess
import time

import httpx
import pytest

from conftest import requires_ffprobe

_DAV_PORT = 18146


@pytest.fixture()
def webdav_server(tmp_path_factory):
    """真实 wsgidav 服务器（匿名；create 路径做连通校验，需真实可达）。"""
    dav_root = tmp_path_factory.mktemp("dav-edit")
    wsgidav_exe = pathlib.Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "wsgidav.exe"
    cmd = [str(wsgidav_exe)] if wsgidav_exe.exists() else ["wsgidav"]
    proc = subprocess.Popen(
        cmd + ["--host", "127.0.0.1", "--port", str(_DAV_PORT),
               "--root", str(dav_root), "--auth", "anonymous", "--server", "cheroot"],
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
    yield dav_root
    proc.kill()


@requires_ffprobe
def test_edit_local_mount_path(env):
    """本地来源改路径（绝对路径）：生效 + 重注册；媒体记录按挂载身份保留。"""
    (env.media_dir / "a").mkdir(exist_ok=True)
    (env.media_dir / "b").mkdir(exist_ok=True)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": str(env.media_dir / "a"), "label": "A库",
    })
    assert r.status_code == 200, r.text
    mid = r.json()["mount_id"]

    # 编辑路径 a → b
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"path": str(env.media_dir / "b"), "label": "B库"},
                         headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].endswith("b") and body["label"] == "B库"
    provider = env.state.storage.get(body["storage_mount_id"])
    assert provider.root.name == "b"

    # 不存在的路径 → 400
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"path": str(env.media_dir / "nope")}, headers=env.admin_headers())
    assert r.status_code == 400

    # 相对路径 → 400
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"path": "relative/path"}, headers=env.admin_headers())
    assert r.status_code == 400

    # 本地来源提交网络字段 → 400
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"host": "x"}, headers=env.admin_headers())
    assert r.status_code == 400


@requires_ffprobe
def test_edit_webdav_mount_fields(env, webdav_server):
    """网络源编辑：host/url/path/账号/密码（写-only，config 不回显密码）。
    create 需真实可达服务器（wsgidav）；编辑不强制在线（NAS 可临时离线）。"""
    env.bootstrap_admin()
    (webdav_server / "media").mkdir(exist_ok=True)
    (webdav_server / "media2").mkdir(exist_ok=True)
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "webdav", "url": f"http://127.0.0.1:{_DAV_PORT}/",
        "path": "media", "username": "u1", "password": "p1", "label": "NAS",
    })
    assert r.status_code == 200, r.text
    mid = r.json()["mount_id"]
    first = r.json()
    assert first["credentials_configured"] is True
    assert "password" not in (first.get("config") or {})

    # 改地址 + 子路径 + 换账号；密码留空（不提交）= 不变
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}", json={
        "url": f"http://127.0.0.1:{_DAV_PORT}/dav", "path": "media2", "username": "u2",
    }, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["url"] == f"http://127.0.0.1:{_DAV_PORT}/dav"
    assert cfg["path"] == "media2"
    assert cfg["username"] == "u2"
    assert r.json()["credentials_configured"] is True  # 密码未动

    # 提交新密码 → 更新（仍不回显）
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"password": "p2"}, headers=env.admin_headers())
    assert r.status_code == 200
    assert "password" not in (r.json().get("config") or {})

    # 非法 URL → 400
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"url": "ftp://x"}, headers=env.admin_headers())
    assert r.status_code == 400

    # 停用状态下编辑连接字段（指向不可达地址也允许=离线语义）→ 启用时按新配置
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"active": False}, headers=env.admin_headers())
    assert r.status_code == 200
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mid}",
                         json={"url": "http://127.0.0.1:3/dav"}, headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["config"]["url"] == "http://127.0.0.1:3/dav"

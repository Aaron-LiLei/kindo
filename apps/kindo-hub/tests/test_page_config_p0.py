"""页面配置 P0 测试（PRD v0.2.2 ADM-010/011，AC-09/AC-12）。"""
import time

import pytest

from conftest import build_sample_library, make_sample_video, requires_ffprobe


@pytest.fixture()
def admin_env(env):
    env.bootstrap_admin()
    return env


# ==================== ADM-010 挂载管理 ====================

def test_mounts_listing_shows_adopted_root(admin_env):
    """全页面化：无 roots 段；配置声明的 family 根启动时收养为普通来源行。"""
    env = admin_env
    r = env.client.get("/api/v1/admin/media-mounts")
    assert r.status_code == 200
    body = r.json()
    assert not body.get("roots"), "不应再有外层根段"
    fam = [m for m in body["mounts"] if m["storage_mount_id"] == "family"]
    assert fam and fam[0]["mount_type"] == "local" and fam[0]["active"]


def test_mount_create_requires_csrf_and_auth(admin_env):
    env = admin_env
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": str(env.media_dir)})
    assert r.status_code in (401, 403)  # 无 CSRF


def test_mount_create_and_scan_flow(admin_env):
    """AC-12 核心：页面把根内子目录添加为媒体库 → 扫描入库，无需改部署配置。"""
    env = admin_env
    lib = env.media_dir / "动画库"
    lib.mkdir(exist_ok=True)
    make_sample_video(lib / "新动画.mp4", seconds=8)
    (lib / "新动画.kindo.yaml").write_text(
        "title: 页面添加的动画\ntags: [测试]\n", encoding="utf-8")

    headers = env.admin_headers()
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": str(lib), "label": "动画库"}, headers=headers)
    assert r.status_code == 200, r.text
    mount = r.json()
    assert mount["source"] == "page" and mount["active"] is True
    assert mount["path"] == str(lib)

    # 挂载列表出现新挂载
    listing = env.client.get("/api/v1/admin/media-mounts").json()
    assert any(m["mount_id"] == mount["mount_id"] for m in listing["mounts"])

    # 扫描新挂载
    r = env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan",
                        headers=headers)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job

    # 媒体入库且来自新挂载（media.mount_id 为存储 id：page-<行 id>）
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    target = [i for i in items if i["title"] == "页面添加的动画"]
    assert target and target[0]["mount_id"] == mount["storage_mount_id"]


def test_mount_path_validation(admin_env):
    """本地来源路径校验：必须绝对路径且目录存在（全页面化决策）。"""
    env = admin_env
    headers = env.admin_headers()
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": "relative/x"}, headers=headers)
    assert r.status_code == 400
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": str(env.media_dir / "不存在")}, headers=headers)
    assert r.status_code == 400
    r = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": ""}, headers=headers)
    assert r.status_code == 400


def test_mount_patch_disable_and_soft_delete(admin_env):
    """停用/软删后挂载注销，但已入库媒体记录保留（ADM-010）。"""
    env = admin_env
    lib = env.media_dir / "临时库"
    lib.mkdir(exist_ok=True)
    make_sample_video(lib / "临时.mp4", seconds=8)
    headers = env.admin_headers()
    mount = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": str(lib)}, headers=headers).json()
    env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan", headers=headers)
    for _ in range(40):
        time.sleep(0.5)
        storage_id = f"page-{mount['mount_id']}"
        media = [i for i in env.client.get("/api/v1/admin/media?limit=100").json()["items"]
                 if i["mount_id"] == storage_id]
        if media:
            break
    assert media, "扫描应入库"

    # 停用：挂载从扫描目标消失，媒体记录保留
    r = env.client.patch(f"/api/v1/admin/media-mounts/{mount['mount_id']}",
                         json={"active": False}, headers=headers)
    assert r.status_code == 200 and r.json()["active"] is False
    targets = env.client.get("/api/v1/admin/media-mounts").json()["scan_targets"]
    assert storage_id not in targets
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert any(i["mount_id"] == storage_id for i in items)

    # 删除：该来源入库资源一并清除（2026-08-25 产品决策）；文件与数据库备份保留
    r = env.client.delete(f"/api/v1/admin/media-mounts/{mount['mount_id']}", headers=headers)
    assert r.status_code == 200 and r.json()["deleted"] is True
    body = r.json()
    assert body["purged"]["media"] >= 1, "应清除该来源入库的媒体"
    assert body["backup"], "应自动备份数据库"
    items = env.client.get("/api/v1/admin/media?limit=100").json()["items"]
    assert not any(i["mount_id"] == storage_id for i in items), "删除后媒体记录应清除"
    # 已删除挂载不再出现在来源列表（幽灵行会让再次删除报"挂载不存在"）
    mounts2 = env.client.get("/api/v1/admin/media-mounts").json()["mounts"]
    assert not any(m["mount_id"] == mount["mount_id"] for m in mounts2), "已删挂载应从列表消失"


# ==================== ADM-011 Provider 管理 ====================

def test_provider_create_write_only_key(admin_env):
    """API Key 写-only：创建响应只含 configured/masked_hint，绝无明文。"""
    env = admin_env
    headers = env.admin_headers()
    r = env.client.post("/api/v1/admin/providers", json={
        "display_name": "家庭模型", "base_url": "http://127.0.0.1:19998/v1",
        "model": "gpt-test", "api_key": "sk-secret-1234567890",
    }, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key_configured"] is True
    assert body["api_key_hint"].startswith("sk-") and body["api_key_hint"].endswith("7890")
    assert "****" in body["api_key_hint"]
    assert "sk-secret-1234567890" not in r.text  # 明文绝不出现

    # 列表同样不回显
    listing = env.client.get("/api/v1/admin/providers").json()
    target = [p for p in listing["providers"] if p["provider_id"] == body["provider_id"]]
    assert target and target[0]["source"] == "page"
    assert "sk-secret-1234567890" not in str(listing)


def test_provider_duplicate_and_invalid_rejected(admin_env):
    env = admin_env
    headers = env.admin_headers()
    # id 与配置文件 Provider 冲突
    env.state.provider_registry.reload()
    config_ids = [v.id for v in env.state.provider_registry.all()]
    if config_ids:
        r = env.client.post("/api/v1/admin/providers", json={
            "id": config_ids[0], "display_name": "x",
            "base_url": "http://x/v1", "model": "m"}, headers=headers)
        assert r.status_code == 409
    # 非法协议 / 非法 base_url
    r = env.client.post("/api/v1/admin/providers", json={
        "display_name": "x", "protocol": "openai_responses",
        "base_url": "http://x/v1", "model": "m"}, headers=headers)
    assert r.status_code == 400
    r = env.client.post("/api/v1/admin/providers", json={
        "display_name": "x", "base_url": "ftp://x", "model": "m"}, headers=headers)
    assert r.status_code == 400


def test_provider_patch_key_untouched_when_blank(admin_env):
    """PATCH 不带 api_key（或空值）时密钥保持不变；换 key 不回显。"""
    env = admin_env
    headers = env.admin_headers()
    created = env.client.post("/api/v1/admin/providers", json={
        "display_name": "家庭模型", "base_url": "http://127.0.0.1:19998/v1",
        "model": "gpt-test", "api_key": "sk-old-key-abcdef"}, headers=headers).json()
    pid = created["provider_id"]

    r = env.client.patch(f"/api/v1/admin/providers/{pid}", json={
        "display_name": "家庭模型2", "base_url": "http://127.0.0.1:19997/v1",
        "model": "gpt-test2"}, headers=headers)  # 不提交 api_key
    assert r.status_code == 200
    assert r.json()["display_name"] == "家庭模型2"
    view = env.state.provider_registry.get(pid)
    assert view is not None and view.api_key == "sk-old-key-abcdef"  # 内部保留

    r = env.client.patch(f"/api/v1/admin/providers/{pid}", json={
        "display_name": "家庭模型2", "base_url": "http://127.0.0.1:19997/v1",
        "model": "gpt-test2", "api_key": "sk-new-key-123456"}, headers=headers)
    view = env.state.provider_registry.get(pid)
    assert view is not None and view.api_key == "sk-new-key-123456"
    assert "sk-new-key-123456" not in r.text


def test_provider_config_adopted_once_and_deletable(admin_env):
    """2026-08-25 全页面化：config 声明启动收养进库（一次性）；
    收养后可编辑、可删除，删除不复活（收养记录防重收养）。"""
    env = admin_env
    headers = env.admin_headers()
    from kindo.config import LLMProviderConfig

    env.state.config.llm_providers = [LLMProviderConfig({
        "id": "main", "display_name": "配置来源", "protocol": "openai_chat_completions",
        "base_url": "http://127.0.0.1:19998/v1", "model": "cfg-model"})]
    with env.db.session() as session:
        n = env.state.provider_registry.adopt_config_providers(session)
    assert n == 1
    env.state.provider_registry.reload()
    v = env.state.provider_registry.get("main")
    assert v is not None and v.model == "cfg-model"
    # 同 id 页面创建 → 409（收养行已存在，无“覆盖”语义）
    r = env.client.post("/api/v1/admin/providers", json={
        "id": "main", "display_name": "x", "base_url": "http://127.0.0.1:19999/v1",
        "model": "page-model"}, headers=headers)
    assert r.status_code == 409
    # 删除 → 重启（再收养）不复活
    r = env.client.delete("/api/v1/admin/providers/main", headers=headers)
    assert r.status_code == 200
    env.state.provider_registry.reload()
    assert env.state.provider_registry.get("main") is None
    with env.db.session() as session:
        assert env.state.provider_registry.adopt_config_providers(session) == 0
    env.state.provider_registry.reload()
    assert env.state.provider_registry.get("main") is None


def test_provider_test_endpoint(admin_env):
    env = admin_env
    headers = env.admin_headers()
    # 用本地脚本 LLM 服务器（test_llm_adapter 同款 MockTransport 不可用；起真实 stub）

    import threading

    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def ping():
        return {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1",
                                           port=18771, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(40):
        if server.started:
            break
        time.sleep(0.1)

    try:
        created = env.client.post("/api/v1/admin/providers", json={
            "display_name": "测试模型", "base_url": "http://127.0.0.1:18771/v1",
            "model": "m"}, headers=headers).json()
        r = env.client.post(f"/api/v1/admin/providers/{created['provider_id']}/test",
                            headers=headers)
        assert r.status_code == 200
        assert r.json()["result"] == "ok", r.text
    finally:
        server.should_exit = True

    # 不可达端点
    bad = env.client.post("/api/v1/admin/providers", json={
        "display_name": "坏模型", "base_url": "http://127.0.0.1:19998/v1",
        "model": "m"}, headers=headers).json()
    r = env.client.post(f"/api/v1/admin/providers/{bad['provider_id']}/test", headers=headers)
    assert r.json()["result"] in ("unreachable", "error")


def test_active_model_switch_to_page_provider(admin_env):
    """AC-09：页面添加 Provider → 设为 active → 新会话绑定它。"""
    env = admin_env
    headers = env.admin_headers()
    _d, token = env.pair_device()
    created = env.client.post("/api/v1/admin/providers", json={
        "display_name": "页面主模型", "base_url": "http://127.0.0.1:19996/v1",
        "model": "page-main"}, headers=headers).json()
    r = env.client.post("/api/v1/admin/active-model",
                        json={"provider_id": created["provider_id"]}, headers=headers)
    assert r.status_code == 200

    r = env.client.post("/api/v1/conversations", json={}, headers=env.device_headers(token))
    assert r.status_code == 200
    assert r.json()["provider_id"] == created["provider_id"]
    assert r.json()["model_id"] == "page-main"

    # 删除 active Provider → 允许并自动清空激活（2026-08-25 决策：删除不被卡死）
    r = env.client.delete(f"/api/v1/admin/providers/{created['provider_id']}",
                          headers=headers)
    assert r.status_code == 200
    from kindo.models import AppSetting

    with env.db.session() as s2:
        assert s2.get(AppSetting, "active_model") is None


@requires_ffprobe
def test_ac12_full_page_configuration_closed_loop(admin_env):
    """AC-12 端到端：页面添加挂载 + 页面添加 Provider → 扫描入库 → 检索可见。"""
    env = admin_env
    build_sample_library(env.media_dir)
    headers = env.admin_headers()

    lib = env.media_dir / "海洋库"
    lib.mkdir(exist_ok=True)
    make_sample_video(lib / "海龟.mp4", seconds=8)
    (lib / "海龟.kindo.yaml").write_text(
        "title: 海龟的秘密\nthemes: [海洋, 动物]\n", encoding="utf-8")

    mount = env.client.post("/api/v1/admin/media-mounts", json={
        "mount_type": "local", "path": str(lib), "label": "海洋库"}, headers=headers).json()
    r = env.client.post(f"/api/v1/admin/media-mounts/{mount['mount_id']}/scan",
                        headers=headers)
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done"

    _d, token = env.pair_device()
    h = env.device_headers(token)
    res = env.client.get("/api/v1/media", params={"query": "海龟"}, headers=h).json()
    assert any(i["title"] == "海龟的秘密" for i in res["items"])

    # 页面 Provider（指向脚本端点）→ 激活 → 会话可用
    provider = env.client.post("/api/v1/admin/providers", json={
        "display_name": "页面配置主模型", "base_url": "http://127.0.0.1:19995/v1",
        "model": "page-model", "api_key": "sk-page-0000"}, headers=headers).json()
    assert provider["api_key_hint"] and "sk-page-0000" not in str(provider)
    env.client.post("/api/v1/admin/active-model",
                    json={"provider_id": provider["provider_id"]}, headers=headers)
    r = env.client.post("/api/v1/conversations", json={}, headers=h)
    assert r.status_code == 200 and r.json()["provider_id"] == provider["provider_id"]


def test_restore_active_mounts_uses_page_prefix(admin_env):
    """回归：Hub 重启恢复 SMB/本地页面挂载时，注册键必须与创建路径一致（page-<id>）。

    旧 bug：app.py 启动恢复的 SMB/WebDAV 分支用 row.id（无前缀）注册，
    导致重启后扫描报"挂载不存在或未激活"、媒体播放 404。
    """
    from datetime import UTC, datetime

    from kindo.media.mounts import MountService
    from kindo.media.network import SmbStorageProvider
    from kindo.media.storage import StorageRegistry
    from kindo.models import MediaMount

    env = admin_env
    with env.db.session() as session:
        smb_row = MediaMount(
            id="smb-row-1", label="测试SMB库", read_only=True, active=True,
            source="page", mount_type="smb",
            config_json={"host": "nas.local", "share": "media"},
            secret_json={"password": "p"},
        )
        local_row = MediaMount(
            id="local-row-1", label="本地库",
            read_only=True, active=True, source="page", mount_type="local",
            config_json={"path": str(env.media_dir)},
        )
        inactive = MediaMount(
            id="dead-row-1", label="停用库",
            read_only=True, active=False, source="page", mount_type="local",
            config_json={"path": str(env.media_dir)},
        )
        soft_deleted = MediaMount(
            id="gone-row-1", label="已删库",
            read_only=True, active=True, source="page", mount_type="local",
            config_json={"path": str(env.media_dir)},
            deleted_at=datetime.now(UTC),
        )
        for row in (smb_row, local_row, inactive, soft_deleted):
            session.add(row)
        session.commit()

    # 模拟重启：空注册表 + 新 MountService（全页面化：无 config 根注册）
    fresh = StorageRegistry([])
    svc = MountService(fresh, env.state.db.session_factory)
    with env.db.session() as session:
        restored = svc.restore_active_mounts(session)

    # 收养的 family 行 + smb + 本地 = 3（全页面化：收养行也是普通来源）
    assert restored == 3
    assert "family" in fresh.mount_ids
    assert "page-smb-row-1" in fresh.mount_ids  # 回归点：SMB 必须带 page- 前缀
    assert "smb-row-1" not in fresh.mount_ids
    assert isinstance(fresh.get("page-smb-row-1"), SmbStorageProvider)
    assert "page-local-row-1" in fresh.mount_ids
    for skipped in ("page-dead-row-1", "page-gone-row-1"):
        assert skipped not in fresh.mount_ids

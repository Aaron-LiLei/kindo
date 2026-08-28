"""Pairing 流程与设备管理（技术方案 §3.2 / §14.2）。"""


def test_pairing_full_flow(env):
    env.bootstrap_admin()
    device_id, token = env.pair_device("客厅电视")
    assert device_id and token
    # 获得 token 后可访问业务接口
    r = env.client.get("/api/v1/bootstrap", headers=env.device_headers(token))
    assert r.status_code == 200
    assert r.json()["device"]["device_id"] == device_id


def test_unpaired_device_cannot_read_media(env):
    env.bootstrap_admin()
    r = env.client.get("/api/v1/home", headers={"Authorization": "Bearer invalid-token"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized_device"


def test_pairing_info_minimal(env):
    """pairing/info 不泄露设备列表/配置/媒体（§3.2）。"""
    env.bootstrap_admin()
    env.pair_device()
    r = env.client.get("/api/v1/pairing/info")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"instance_id", "display_name", "api_version", "pairing_enabled",
                         "server_time"}


def test_pairing_wrong_secret(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/pairing/requests", json={
        "device_name": "TV", "app_instance_id": "a1",
    })
    pr = r.json()
    r = env.client.get(
        f"/api/v1/pairing/requests/{pr['pairing_id']}",
        params={"pairing_secret": "wrong-secret"},
    )
    assert r.status_code == 401


def test_pairing_display_code_mismatch_rejected(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/pairing/requests", json={
        "device_name": "TV", "app_instance_id": "a2",
    })
    pr = r.json()
    r = env.client.post(
        f"/api/v1/admin/pairing/requests/{pr['pairing_id']}/approve",
        json={"confirm_code": "000000"},
        headers=env.admin_headers(),
    )
    assert r.status_code == 400


def test_revoke_device_blocks_access(env):
    env.bootstrap_admin()
    device_id, token = env.pair_device()
    headers = env.device_headers(token)
    assert env.client.get("/api/v1/bootstrap", headers=headers).status_code == 200
    r = env.client.post(f"/api/v1/admin/devices/{device_id}/revoke", headers=env.admin_headers())
    assert r.status_code == 200
    assert env.client.get("/api/v1/bootstrap", headers=headers).status_code == 401


def test_bootstrap_capabilities(env):
    env.bootstrap_admin()
    _d, token = env.pair_device()
    r = env.client.get("/api/v1/bootstrap", headers=env.device_headers(token))
    body = r.json()
    assert body["capabilities"]["tts_available"] is True
    # 无 LLM 配置的环境：ai_available=False（降级不隐藏）
    assert body["capabilities"]["ai_available"] is False
    assert body["capabilities"]["voice_available"] is False  # 未配置 kindo-asr

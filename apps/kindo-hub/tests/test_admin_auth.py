"""Admin 认证：bootstrap / 登录 / CSRF / 限速 / 权限分离（技术方案 §14.3）。"""


def test_bootstrap_requires_token(env):
    r = env.client.post("/api/v1/admin/auth/bootstrap", json={
        "username": "admin", "password": "password123", "bootstrap_token": "wrong",
    })
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_admin"


def test_bootstrap_and_double_init(env):
    result = env.bootstrap_admin()
    assert result["user"]["username"] == "admin"
    # 二次初始化被拒
    r = env.client.post("/api/v1/admin/auth/bootstrap", json={
        "username": "admin2", "password": "password123", "bootstrap_token": "test-bootstrap-token",
    })
    assert r.status_code == 400


def test_login_wrong_password(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/auth/login", json={
        "username": "admin", "password": "nope12345",
    })
    assert r.status_code == 401


def test_login_rate_limit(env):
    env.bootstrap_admin()
    # 成功登录不再占用失败窗口（2026-08-19 修正），需连续错 5 次触发上限
    for i in range(5):
        r = env.client.post("/api/v1/admin/auth/login", json={
            "username": "admin", "password": f"wrong-{i}",
        })
        assert r.status_code == 401
    r = env.client.post("/api/v1/admin/auth/login", json={
        "username": "admin", "password": "password123",
    })
    assert r.status_code == 403  # 限速生效，即使密码正确


def test_admin_write_requires_csrf(env):
    env.bootstrap_admin()
    r = env.client.put("/api/v1/admin/policy", json={"autoplay": True})
    assert r.status_code == 403
    assert "CSRF" in r.json()["error"]["message"]


def test_device_token_cannot_call_admin(env):
    env.bootstrap_admin()
    _device_id, token = env.pair_device()
    env.client.cookies.clear()  # 摘掉管理会话 Cookie，仅凭 Device Token 访问
    r = env.client.get("/api/v1/admin/devices", headers=env.device_headers(token))
    assert r.status_code == 401  # 权限完全分离（§14.4）


def test_unauthenticated_admin(env):
    r = env.client.get("/api/v1/admin/devices")
    assert r.status_code == 401


def test_logout_invalidates_session(env):
    env.bootstrap_admin()
    # logout 属写操作，统一要求 CSRF（2026-08-19 修正）
    r = env.client.post("/api/v1/admin/auth/logout")
    assert r.status_code == 403
    r = env.client.post("/api/v1/admin/auth/logout", headers=env.admin_headers())
    assert r.status_code == 200
    r = env.client.get("/api/v1/admin/devices")
    assert r.status_code == 401


def test_auth_state_lifecycle(env):
    """/auth/state 认证入口状态机：setup_required → ready(未登录) → ready(已登录)。"""
    # S0：未初始化（免认证可查，泄露面仅 1 bit）
    r = env.client.get("/api/v1/admin/auth/state")
    assert r.status_code == 200
    assert r.json() == {"phase": "setup_required", "authenticated": False}

    # S1：初始化后未登录
    env.client.post("/api/v1/admin/auth/bootstrap", json={
        "username": "admin", "password": "password123",
        "bootstrap_token": "test-bootstrap-token",
    })
    env.client.cookies.clear()
    r = env.client.get("/api/v1/admin/auth/state")
    assert r.status_code == 200
    assert r.json()["phase"] == "ready"
    assert r.json()["authenticated"] is False
    assert r.json()["username"] is None

    # S1 + 有效会话
    env.login_admin()
    r = env.client.get("/api/v1/admin/auth/state")
    assert r.status_code == 200
    assert r.json()["phase"] == "ready"
    assert r.json()["authenticated"] is True
    assert r.json()["username"] == "admin"

    # 会话失效（伪造 cookie）不得 500、不得误报 authenticated
    env.client.cookies.clear()
    env.client.cookies.set("kindo_admin_session", "forged-session-id")
    r = env.client.get("/api/v1/admin/auth/state")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False

"""设置页聚合轮（2026-08-26 晚）：管理员密码修改 + 活动库多样性扩充（迁移 0015）。"""
from kindo.models import AdminSession
from kindo.security import new_opaque_token, sha256_hex


def test_change_password_flow(env):
    """改密：当前密码校验/长度校验/新密码生效/其余会话撤销/当前会话保留。"""
    env.bootstrap_admin()  # admin / password123
    headers = env.admin_headers()

    # 错误的当前密码 → 400
    r = env.client.post("/api/v1/admin/auth/password", headers=headers, json={
        "current_password": "wrong-password", "new_password": "new-password-456"})
    assert r.status_code == 400
    assert "当前密码不正确" in r.text

    # 新密码过短 → 400/422 拒绝
    r = env.client.post("/api/v1/admin/auth/password", headers=headers, json={
        "current_password": "password123", "new_password": "short"})
    assert r.status_code in (400, 422)

    # 造第二个会话（模拟另一台浏览器）
    with env.db.session() as s:
        from kindo.models import AdminUser

        user = s.query(AdminUser).one()
        other_sid = new_opaque_token(32)
        s.add(AdminSession(id_hash=sha256_hex(other_sid), user_id=user.id,
                           csrf_token_hash=sha256_hex(new_opaque_token(24)),
                           expires_at=user.created_at.replace(
                               year=user.created_at.year + 1)))
        s.commit()

    # 改密成功
    r = env.client.post("/api/v1/admin/auth/password", headers=headers, json={
        "current_password": "password123", "new_password": "new-password-456"})
    assert r.status_code == 200, r.text

    # 其余会话被撤销，当前会话保留
    with env.db.session() as s:
        remaining = s.query(AdminSession).all()
        assert len(remaining) == 1, "改密后只应保留当前会话"

    # 当前会话仍可用（无需重新登录）
    r = env.client.get("/api/v1/admin/auth/status", headers=headers)
    assert r.status_code == 200

    # 旧密码登录失败 / 新密码登录成功
    r = env.client.post("/api/v1/admin/auth/login", json={
        "username": "admin", "password": "password123"})
    assert r.status_code == 401
    r = env.client.post("/api/v1/admin/auth/login", json={
        "username": "admin", "password": "new-password-456"})
    assert r.status_code == 200


def test_activity_diversity_seed(env):
    """迁移 0015：内置活动 ≥22 个，新增主题在场，既有内容已增补收尾句。"""
    env.bootstrap_admin()
    r = env.client.get("/api/v1/admin/activities", headers=env.admin_headers())
    items = r.json()["items"]
    builtin = [a for a in items if a["source"] == "builtin"]
    assert len(builtin) >= 22, f"内置活动应 ≥22（8 既有 + 14 新增），实际 {len(builtin)}"
    titles = {a["title"] for a in builtin}
    for t in ("影子小剧场", "太空小宇航员", "恐龙考古队", "节奏小乐队",
              "情绪猜猜看", "小小整理师", "种子观察员", "彩色大收集",
              "纸箱变形记", "小小厨师助手", "故事接龙", "字母跳格子",
              "天气播报员", "平衡木小体操"):
        assert t in titles, f"缺少新活动：{t}"
    # 既有活动内容增补（仅当仍为旧原文时更新——此处为全新库，必为旧原文）
    ocean = next(a for a in builtin if a["title"] == "小小海洋学家")
    assert "玩具海龟" in ocean["summary"], "既有活动应有增补的收尾步骤"
    # 新活动内容具体可执行（长度下限防止空壳条目）
    shadow = next(a for a in builtin if a["title"] == "影子小剧场")
    assert len(shadow["summary"]) >= 30 and shadow["status"] == "preset"

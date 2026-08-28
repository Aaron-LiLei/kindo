"""配置根收养与全页面化来源管理（2026-08-25 决策）契约测试。

- 启动收养：kindo.yaml 仍声明 media_mounts 时自动转为页面挂载（storage_id 保持
  原根 id，既有媒体记录无缝；幂等；目录缺失跳过不阻启动）。
- 新建本地来源：绝对路径校验（须存在）。
- 旧根内子目录挂载在收养后仍可恢复（_local_path 回退）。
"""
import time

from conftest import build_sample_library, requires_ffprobe


def _scan_done(env, mount_id):
    r = env.client.post(f"/api/v1/admin/media-mounts/{mount_id}/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job


@requires_ffprobe
def test_config_root_adopted_on_startup(env):
    """family 根被收养为页面挂载：媒体无缝 + 列表可管理 + 幂等。"""
    build_sample_library(env.media_dir)
    env.bootstrap_admin()

    # 启动收养已发生（create_app lifespan 内）
    from kindo.models import MediaMount

    with env.db.session() as s:
        rows = s.query(MediaMount).filter(MediaMount.storage_id == "family").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.mount_type == "local"
        assert (row.config_json or {}).get("path", "").endswith("media")

    data = env.client.get("/api/v1/admin/media-mounts",
                          headers=env.admin_headers()).json()
    fam = next(m for m in data["mounts"] if m["storage_mount_id"] == "family")
    assert fam["active"] is True
    assert "roots" not in data or data.get("roots") in (None, [])  # 全页面化：无根段

    # 扫描/媒体无缝（mount_id 仍为 family）
    _scan_done(env, "family")
    from kindo.models import Media

    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == "family").count() == 4

    # 幂等：模拟重启（再跑收养）不产生第二行
    with env.db.session() as s:
        assert env.state.mounts.adopt_config_roots(s) == 0
        assert s.query(MediaMount).filter(MediaMount.storage_id == "family").count() == 1


@requires_ffprobe
def test_local_source_requires_existing_absolute_path(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": str(env.media_dir), "label": "直接路径",
    })
    assert r.status_code == 200, r.text
    assert r.json()["path"] == str(env.media_dir)

    # 相对路径 → 400
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": "relative"})
    assert r.status_code == 400
    # 不存在 → 400
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": str(env.media_dir / "不存在")})
    assert r.status_code == 400
    # 空路径 → 400
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": ""})
    assert r.status_code == 400


@requires_ffprobe
def test_legacy_subdir_mount_survives_adoption(env):
    """旧式根内子目录挂载（root_id+sub_path）：收养根注册后仍可恢复使用。"""
    (env.media_dir / "旧子库").mkdir(exist_ok=True)
    env.bootstrap_admin()
    from kindo.models import MediaMount

    with env.db.session() as s:
        s.add(MediaMount(id="legacy01", root_id="family", sub_path="旧子库",
                         label="旧子库", active=True, source="page", mount_type="local"))
        s.commit()
    # 模拟重启恢复（收养先注册 family 根 → 旧挂载按 root+sub_path 解析）
    with env.db.session() as s:
        n = env.state.mounts.restore_active_mounts(s)
    assert n >= 1
    provider = env.state.storage.get("page-legacy01")
    assert provider.root.name == "旧子库"

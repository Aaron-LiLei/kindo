"""P2 轮（2026-08-26 下午）：Provider enabled / 首选版本 PLY-009 /
ASR 热词自动构建 ASR-005 / Analytics 自定义范围。"""
import time
from datetime import UTC, datetime, timedelta

from conftest import build_sample_library, requires_ffprobe


def _scan_done(env) -> None:
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}",
                             headers=env.admin_headers()).json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            return
        time.sleep(0.5)


def test_provider_enabled_toggle(env):
    """停用=不参与解析且密钥保留；停用当前激活项自动清空；TV ai_available 联动。"""
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/providers", headers=env.admin_headers(), json={
        "id": "p-test", "display_name": "测试模型", "base_url": "http://127.0.0.1:19999/v1",
        "model": "test-model", "api_key": "sk-test",
    })
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True

    _d, token = env.pair_device()
    dev = {"Authorization": f"Bearer {token}"}
    # 页面另有真实/其他 Provider 时 ai_available 已真；此处以 health 侧计数为准
    # 停用 → active 清空（它此前可能被自动选为激活）+ 列表保留 enabled=False
    env.client.post("/api/v1/admin/active-model", headers=env.admin_headers(),
                    json={"provider_id": "p-test"})
    r = env.client.patch("/api/v1/admin/providers/p-test", headers=env.admin_headers(), json={
        "display_name": "测试模型", "base_url": "http://127.0.0.1:19999/v1",
        "model": "test-model", "enabled": False,
    })
    assert r.status_code == 200 and r.json()["enabled"] is False

    providers = env.client.get("/api/v1/admin/providers",
                               headers=env.admin_headers()).json()
    row = next(p for p in providers["providers"] if p["provider_id"] == "p-test")
    assert row["enabled"] is False
    assert row["api_key_configured"] is True  # 密钥保留（区别于删除）
    assert providers["active_provider_id"] != "p-test"

    # TV bootstrap：全部 Provider 停用 → ai_available=False（capabilities 联动）
    others = [p for p in providers["providers"] if p["enabled"]]
    for p in others:  # 构造"唯一 Provider 停用"场景
        env.client.patch(f"/api/v1/admin/providers/{p['provider_id']}",
                         headers=env.admin_headers(), json={
                             "display_name": p["display_name"],
                             "base_url": p["base_url"], "model": p["model"],
                             "enabled": False})
    boot = env.client.get("/api/v1/bootstrap", headers=dev).json()
    assert boot["capabilities"]["ai_available"] is False

    # 恢复启用 → ai_available 回到 True
    env.client.patch("/api/v1/admin/providers/p-test", headers=env.admin_headers(), json={
        "display_name": "测试模型", "base_url": "http://127.0.0.1:19999/v1",
        "model": "test-model", "enabled": True})
    boot = env.client.get("/api/v1/bootstrap", headers=dev).json()
    assert boot["capabilities"]["ai_available"] is True


@requires_ffprobe
def test_preferred_asset_hides_alternate(env):
    """PLY-009：同实体多版本默认只出 PRIMARY；家长换首选后跟随切换。"""
    from kindo.models import ContentEntity, EntityAsset, Media, MediaAsset

    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    _d, token = env.pair_device()
    dev = {"Authorization": f"Bearer {token}"}

    with env.db.session() as s:
        ep = s.query(Media).filter(Media.media_type == "episode").first()
        entity = (s.query(ContentEntity)
                  .filter(ContentEntity.source_media_id == ep.id).one())
        # 造第二版本：media + media_asset（兼容期同 id）+ ALTERNATE 关联
        alt = Media(id="alt-version-1", mount_id=ep.mount_id,
                    path_key=ep.path_key + ".720p.mkv",
                    title=ep.title + "（720p 版本）", media_type="episode",
                    duration_ms=ep.duration_ms, size_bytes=1, mtime_ms=1,
                    playable=True)
        s.add(alt)
        s.add(MediaAsset(id="alt-version-1", mount_id=ep.mount_id,
                         path_key=alt.path_key, file_kind="video"))
        s.flush()  # 跨表 FK 无 ORM 关系声明：先落 media/media_asset 再挂链接
        s.add(EntityAsset(id="ea-alt-1", entity_id=entity.id, asset_id=alt.id,
                          role="ALTERNATE_VIDEO", sequence=2))
        s.commit()
        entity_id, primary_id, alt_id = entity.id, ep.id, alt.id

    items = env.client.get("/api/v1/media", headers=dev,
                           params={"limit": 100}).json()["items"]
    ids = {i["media_id"] for i in items}
    assert primary_id in ids and alt_id not in ids, "默认只出 PRIMARY 版本"

    # 家长把 720p 设为首选 → 互换可见性
    r = env.client.put(f"/api/v1/admin/content/{entity_id}/preferred-asset",
                       headers=env.admin_headers(), json={"asset_id": alt_id})
    assert r.status_code == 200, r.text
    items = env.client.get("/api/v1/media", headers=dev,
                           params={"limit": 100}).json()["items"]
    ids = {i["media_id"] for i in items}
    assert alt_id in ids and primary_id not in ids, "换首选后版本可见性跟随"

    assets = env.client.get(f"/api/v1/admin/content/{entity_id}/assets",
                            headers=env.admin_headers()).json()["assets"]
    roles = {a["asset_id"]: a["role"] for a in assets}
    assert roles[alt_id] == "PRIMARY_VIDEO" and roles[primary_id] == "ALTERNATE_VIDEO"

    # 检索路径同样隐藏非首选版本
    found = env.client.get("/api/v1/media", headers=dev,
                           params={"query": "720p", "limit": 50}).json()["items"]
    found_ids = {i["media_id"] for i in found}
    assert alt_id in found_ids and primary_id not in found_ids

    # 非本实体的 asset 不可设为首选
    r = env.client.put(f"/api/v1/admin/content/{entity_id}/preferred-asset",
                       headers=env.admin_headers(), json={"asset_id": "not-mine"})
    assert r.status_code == 400


@requires_ffprobe
def test_hotwords_build_and_manual_keep(env):
    """ASR-005：从库生成热词；手工补写行重建保留。"""
    from pathlib import Path

    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)

    r = env.client.post("/api/v1/admin/asr/hotwords/rebuild",
                        headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] > 0
    path = Path(body["path"])
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "汪汪队" in text  # 系列名进入词表

    status = env.client.get("/api/v1/admin/asr/hotwords",
                            headers=env.admin_headers()).json()
    assert status["exists"] and status["count"] == body["count"]

    # 手工补写：重建后保留
    path.write_text(path.read_text(encoding="utf-8")
                    + "## manual（手工补写，重建保留）\n奥特曼\n",
                    encoding="utf-8")
    r = env.client.post("/api/v1/admin/asr/hotwords/rebuild",
                        headers=env.admin_headers())
    assert r.json()["manual_count"] == 1
    assert "奥特曼" in path.read_text(encoding="utf-8")
    assert "汪汪队" in path.read_text(encoding="utf-8")


def test_analytics_custom_range(env):
    """period=custom：窗口过滤生效；参数校验 400。"""
    from kindo.models import Media, Playback, ViewingInterval

    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)

    with env.db.session() as s:
        m = s.query(Media).filter(Media.media_type == "episode").first()
        prow = s.query(Playback).first()
        profile = "default"
        pb = Playback(id="pb-custom-1", device_id=prow.device_id if prow else "d1",
                      profile_id=profile, media_id=m.id, action="play", source="ui",
                      state="ended", started_at=datetime.now(UTC))
        s.add(pb)
        s.add(ViewingInterval(id="vi-custom-1", playback_id=pb.id,
                              started_at=datetime.now(UTC),
                              ended_at=datetime.now(UTC) + timedelta(seconds=5),
                              duration_ms=5000))
        s.commit()

    today = datetime.now().astimezone().date()
    yesterday = today - timedelta(days=1)
    r = env.client.get("/api/v1/admin/analytics", headers=env.admin_headers(), params={
        "period": "custom", "start": str(today), "end": str(today)})
    assert r.status_code == 200, r.text
    assert r.json()["total_watched_seconds"] >= 5
    assert r.json()["period"] == "custom"

    r = env.client.get("/api/v1/admin/analytics", headers=env.admin_headers(), params={
        "period": "custom", "start": str(yesterday), "end": str(yesterday)})
    assert r.status_code == 200
    assert r.json()["total_watched_seconds"] == 0

    # 校验：缺参数 / 次序颠倒 / 非法格式
    for params in (
        {"period": "custom"},
        {"period": "custom", "start": str(today), "end": str(yesterday)},
        {"period": "custom", "start": "2026/08/26", "end": str(today)},
    ):
        r = env.client.get("/api/v1/admin/analytics", headers=env.admin_headers(),
                           params=params)
        assert r.status_code == 400, params

    # interest 自定义范围：窗口内计数
    from kindo.models import InterestSignal

    with env.db.session() as s:
        s.add(InterestSignal(id="is-custom-1", profile_id="default",
                             signal_type="watched", source="browse"))
        s.commit()
    r = env.client.get("/api/v1/admin/analytics/interest", headers=env.admin_headers(),
                       params={"period": "custom", "start": str(today), "end": str(today)})
    assert r.status_code == 200
    assert r.json()["signal_counts_by_type"].get("watched", 0) >= 1
    r = env.client.get("/api/v1/admin/analytics/interest", headers=env.admin_headers(),
                       params={"period": "custom", "start": str(yesterday),
                               "end": str(yesterday)})
    assert r.status_code == 200
    assert r.json()["signal_counts_by_type"].get("watched", 0) == 0

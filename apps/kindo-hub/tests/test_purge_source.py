"""删除来源=清除入库资源（2026-08-25 产品决策）契约测试。

停用只断开（资源保留）；删除清除该 storage_mount_id 的媒体/实体树/观看播放域/
课程进度/扫描任务/海报缓存，共享系列等祖先仅在空巢时剪除；文件与 DB 备份保留。
2026-08-25 深夜全页面化：本地来源=绝对路径，配置根由启动收养（无 media-roots 端点）。
"""
import time
from datetime import UTC, datetime

from conftest import build_sample_library, make_sample_video, requires_ffprobe


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
def test_mount_delete_purges_all_library_resources(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env, "family")

    from kindo.models import ContentEntity, Media, ScanJob, WatchHistory

    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == "family").count() == 4
        before_entities = s.query(ContentEntity).count()
        assert before_entities > 0
        # 制造观看历史与断点
        mid = s.query(Media.id).filter(Media.mount_id == "family").first()[0]
        s.add(WatchHistory(profile_id="p1", media_id=mid,
                           last_position_ms=1000, watched_seconds=1,
                           completed=False,
                           last_watched_at=datetime.now(UTC)))
        s.commit()

    # 删除该来源（清除资源）
    mounts = env.client.get("/api/v1/admin/media-mounts",
                            headers=env.admin_headers()).json()["mounts"]
    fam = next(m for m in mounts if m["storage_mount_id"] == "family")
    r = env.client.delete(f"/api/v1/admin/media-mounts/{fam['mount_id']}",
                          headers=env.admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purged"]["media"] == 4
    assert body["backup"] and "backups" in body["backup"]

    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == "family").count() == 0
        # 实体树整体剪除（本库全部来自 family）
        assert s.query(ContentEntity).count() == 0
        assert s.query(WatchHistory).count() == 0
        assert s.query(ScanJob).filter(ScanJob.mount_id == "family").count() == 0
    # TV 端列表不再返回
    _d, token = env.pair_device()
    items = env.client.get("/api/v1/media?limit=50",
                           headers=env.device_headers(token)).json()["items"]
    assert items == []

    # 重新添加同路径来源 + 重扫 → 资源重建
    r = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
        "mount_type": "local", "path": str(env.media_dir), "label": "重建库",
    })
    assert r.status_code == 200
    new_sid = r.json()["storage_mount_id"]
    _scan_done(env, r.json()["mount_id"])
    with env.db.session() as s:
        assert s.query(Media).filter(Media.mount_id == new_sid).count() == 4


@requires_ffprobe
def test_delete_mount_keeps_shared_series(env):
    """两个来源喂同一系列：删除其一，系列实体与另一来源的集保留。"""
    for sub in ("甲源", "乙源"):
        d = env.media_dir / sub
        d.mkdir(exist_ok=True)
        make_sample_video(d / f"{sub}S01E01.mp4", seconds=4)
        (d / f"{sub}S01E01.kindo.yaml").write_text(
            f"title: {sub} 第1集\nseries: {{name: 共享系列, season_no: 1, episode_no: 1}}\n",
            encoding="utf-8")
    env.bootstrap_admin()
    for name in ("甲源", "乙源"):
        m = env.client.post("/api/v1/admin/media-mounts", headers=env.admin_headers(), json={
            "mount_type": "local", "path": str(env.media_dir / name), "label": name,
        }).json()
        _scan_done(env, m["mount_id"])

    from kindo.models import ContentEntity, Media

    with env.db.session() as s:
        series = (s.query(ContentEntity)
                  .filter(ContentEntity.entity_type == "series",
                          ContentEntity.title == "共享系列").all())
        assert len(series) == 1
        sid = series[0].id
        assert s.query(Media).count() == 2

    # 删除甲源
    mounts = env.client.get("/api/v1/admin/media-mounts",
                            headers=env.admin_headers()).json()["mounts"]
    jia = next(m for m in mounts if m["label"] == "甲源")
    r = env.client.delete(f"/api/v1/admin/media-mounts/{jia['mount_id']}",
                          headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["purged"]["media"] == 1

    with env.db.session() as s:
        assert s.query(Media).count() == 1  # 乙源保留
        series = (s.query(ContentEntity)
                  .filter(ContentEntity.entity_type == "series",
                          ContentEntity.title == "共享系列").all())
        assert len(series) == 1 and series[0].id == sid, "共享系列实体应保留"
        eps = (s.query(ContentEntity)
               .filter(ContentEntity.entity_type == "episode").count())
        assert eps == 1  # 只剩乙源那集

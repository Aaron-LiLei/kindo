"""Admin 内容目录管理契约测试（v0.3 ADM-003/013/014 + Policy 预览 + Analytics 扩展）。"""
import io
import time

from conftest import build_sample_library, requires_ffprobe


def _scan_done(env) -> None:
    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    assert job["state"] == "done", job


def _first_media(env):
    items = env.client.get("/api/v1/admin/media?limit=10",
                           headers=env.admin_headers()).json()["items"]
    assert items, "媒体库为空"
    return items[0]


@requires_ffprobe
def test_canonical_get_and_patch(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    media = _first_media(env)

    # by-media 解析到实体，来源字段齐全
    r = env.client.get(f"/api/v1/admin/content/by-media/{media['media_id']}",
                       headers=env.admin_headers())
    assert r.status_code == 200, r.text
    entity = r.json()["entity"]
    assert entity is not None
    eid = entity["entity_id"]
    fields = entity["fields"]
    assert "content_class" in fields and "locked" in fields["content_class"]

    # 家长编辑 + 锁定（ADM-003）；content_class 变更为分类事实来源（约束 12）
    r = env.client.patch(f"/api/v1/admin/content/{eid}", json={
        "fields": {
            "content_class": {"value": "LEARNING", "locked": True, "has_value": True},
            "age_min": {"value": 3, "has_value": True},
            "topics": {"value": ["海洋", "动物"], "has_value": True},
        },
    }, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    got = r.json()["fields"]
    assert got["content_class"]["value"] == "LEARNING"
    assert got["content_class"]["locked"] is True
    assert got["content_class"]["source"] == "parent"
    assert got["age_min"]["value"] == 3
    assert sorted(got["topics"]["value"]) == ["动物", "海洋"]

    # 锁定字段不可被 Provider 级写入覆盖（约束 15 语义）
    from kindo.media.metadata import apply_with_provenance

    with env.db.session() as session:
        from kindo.models import ContentEntity

        ent = session.get(ContentEntity, eid)
        assert not apply_with_provenance(ent, "content_class", "ENTERTAINMENT",
                                         "provider")
        session.rollback()

    # 越权字段名 400
    r = env.client.patch(f"/api/v1/admin/content/{eid}", json={
        "fields": {"hacker_field": {"value": 1, "has_value": True}},
    }, headers=env.admin_headers())
    assert r.status_code == 400


@requires_ffprobe
def test_artwork_management(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    media = _first_media(env)
    eid = env.client.get(
        f"/api/v1/admin/content/by-media/{media['media_id']}",
        headers=env.admin_headers()).json()["entity"]["entity_id"]

    img = io.BytesIO()
    make_sample_image_to(img)
    r = env.client.post(
        f"/api/v1/admin/content/{eid}/artwork",
        data={"kind": "poster", "locked": "true"},
        files={"file": ("p.jpg", img.getvalue(), "image/jpeg")},
        headers=env.admin_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "parent" and r.json()["locked"] is True

    # 列表 + 图片预览 + 锁切换 + 删除
    items = env.client.get(f"/api/v1/admin/content/{eid}/artwork",
                           headers=env.admin_headers()).json()["items"]
    poster = next(i for i in items if i["kind"] == "poster")
    assert poster["exists"] and poster["locked"]
    r = env.client.get(f"/api/v1/admin/content/{eid}/artwork/poster/image",
                       headers=env.admin_headers())
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    r = env.client.patch(f"/api/v1/admin/content/{eid}/artwork/poster",
                         json={"locked": False}, headers=env.admin_headers())
    assert r.status_code == 200 and r.json()["locked"] is False
    r = env.client.delete(f"/api/v1/admin/content/{eid}/artwork/poster",
                          headers=env.admin_headers())
    assert r.status_code == 200
    r = env.client.get(f"/api/v1/admin/content/{eid}/artwork/poster/image",
                       headers=env.admin_headers())
    assert r.status_code == 404


def make_sample_image_to(buf) -> None:
    """生成一张小 JPEG 进内存（上传用；无 PIL 时退回 ffmpeg 临时文件）。"""
    try:
        from PIL import Image

        img = Image.new("RGB", (64, 96), (255, 122, 61))
        img.save(buf, format="JPEG")
    except ImportError:
        import subprocess
        import tempfile
        from pathlib import Path

        from conftest import FFPROBE

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp = Path(f.name)
        ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=orange:s=64x96:d=1", "-frames:v", "1",
                        str(tmp)], check=True, capture_output=True, timeout=30)
        buf.write(tmp.read_bytes())
        tmp.unlink(missing_ok=True)
    buf.seek(0)


def test_activities_crud(env):
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/activities", json={
        "title": "小小海洋学家", "summary": "找一个圆的东西当贝壳",
        "topics": ["海洋"], "age_min": 3, "age_max": 6,
    }, headers=env.admin_headers())
    assert r.status_code == 200, r.text
    aid = r.json()["id"]

    # 编辑 + 删除（自建可改可删）
    r = env.client.patch(f"/api/v1/admin/activities/{aid}", json={
        "title": "小小海洋学家（改）", "topics": ["海洋", "动物"],
    }, headers=env.admin_headers())
    assert r.status_code == 200
    items = env.client.get("/api/v1/admin/activities",
                           headers=env.admin_headers()).json()["items"]
    row = next(i for i in items if i["id"] == aid)
    assert row["title"] == "小小海洋学家（改）"
    assert sorted(row["topics_json"]) == ["动物", "海洋"]
    r = env.client.delete(f"/api/v1/admin/activities/{aid}",
                          headers=env.admin_headers())
    assert r.status_code == 200

    # builtin 模板不可编辑/删除
    items = env.client.get("/api/v1/admin/activities",
                           headers=env.admin_headers()).json()["items"]
    builtin = next((i for i in items if i["source"] == "builtin"), None)
    if builtin is not None:
        r = env.client.patch(f"/api/v1/admin/activities/{builtin['id']}",
                             json={"title": "x"}, headers=env.admin_headers())
        assert r.status_code == 400
        r = env.client.delete(f"/api/v1/admin/activities/{builtin['id']}",
                              headers=env.admin_headers())
        assert r.status_code == 400


@requires_ffprobe
def test_policy_usage_preview(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    r = env.client.get("/api/v1/admin/policy/usage", headers=env.admin_headers())
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("video_entertainment", "video_learning", "audio", "ai_voice"):
        assert key in data
    assert "transition_offered_today" in data


@requires_ffprobe
def test_analytics_modality_and_recent(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    items = env.client.get("/api/v1/media", headers=headers).json()["items"]
    target = next(i for i in items if "第1集" in i["title"])
    body = env.client.post("/api/v1/playbacks", json={
        "media_id": target["media_id"], "action": "play", "source": "ui",
    }, headers=headers).json()
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "mx1",
                      "playback_id": body["playback_id"], "position_ms": 0})
        from conftest import wait_ack

        wait_ack(ws, "mx1")
        time.sleep(1.0)
        ws.send_json({"type": "playback.ended", "event_id": "mx2",
                      "playback_id": body["playback_id"],
                      "position_ms": body["stream_descriptor"]["duration_ms"]})
        wait_ack(ws, "mx2")

    data = env.client.get("/api/v1/admin/analytics?period=day",
                          headers=env.admin_headers()).json()
    assert data["by_modality"], "按媒介维度不应为空"
    assert data["by_modality"].get("VIDEO", 0) > 0
    assert data["recent_records"], "观看记录明细不应为空"
    rec = data["recent_records"][0]
    assert rec["modality"] == "VIDEO" and rec["watched_seconds"] >= 1


@requires_ffprobe
def test_tv_entity_poster_and_detail_dims(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    _scan_done(env)
    _d, token = env.pair_device()
    headers = env.device_headers(token)

    # collections 带实体锚点（系列卡优先 Series poster 数据基础）
    cols = env.client.get("/api/v1/collections", headers=headers).json()
    assert cols["series"], "样本库应至少有一个系列"
    s = cols["series"][0]
    assert "entity_id" in s and "match_status" in s and "entity_poster" in s
    r = env.client.get(f"/api/v1/entities/{s['entity_id']}/poster", headers=headers)
    assert r.status_code == 200  # 无实体图时回退默认海报

    # 详情带 Canonical 维度与简介（交互 §4.3）；预检带 constraints（维度化文案）
    items = env.client.get("/api/v1/media", headers=headers).json()["items"]
    detail = env.client.get(
        f"/api/v1/media/{items[0]['media_id']}", headers=headers).json()
    assert "overview" in detail and "modality" in detail
    assert "constraints" in detail["actions"]["play"]

    # regrant：未拥有/不存在的 playback 应 404/403
    r = env.client.post("/api/v1/playbacks/not-exists/regrant", headers=headers)
    assert r.status_code == 404

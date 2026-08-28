"""v0.3 统一内容目录同步层测试（阶段 1c，P10）：扫描建目录、播放维度快照、
历史挂 entity、目录改名历史保留（决策二验收）。"""
import shutil
import time

import pytest

from conftest import build_sample_library, requires_ffprobe, wait_ack


@pytest.fixture()
def library_env(env):
    build_sample_library(env.media_dir)
    env.bootstrap_admin()
    r = env.client.post("/api/v1/admin/media-mounts/family/scan", headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}").json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            break
        time.sleep(0.5)
    return env


def _entity_counts(env):
    from kindo.models import ContentEntity

    with env.db.session() as s:
        rows = s.query(ContentEntity.entity_type, ContentEntity.sequence_no).all()
    out: dict[str, int] = {}
    for etype, _seq in rows:
        out[etype] = out.get(etype, 0) + 1
    return out


@requires_ffprobe
def test_scan_builds_unified_catalog(library_env):
    env = library_env
    counts = _entity_counts(env)
    assert counts == {"series": 1, "season": 1, "episode": 2,
                      "course": 1, "lesson": 1, "movie": 1}, counts

    from kindo.models import ContentEntity, EntityAsset, EntityCharacter, EntityTopic, MediaAsset

    with env.db.session() as s:
        # 树结构：episode → season → series
        ep = s.query(ContentEntity).filter_by(entity_type="episode").order_by(
            ContentEntity.sequence_no).first()
        season = s.get(ContentEntity, ep.parent_id)
        assert season.entity_type == "season"
        series = s.get(ContentEntity, season.parent_id)
        assert series.entity_type == "series" and series.title == "汪汪队立大功"
        # 维度默认映射
        assert ep.content_class == "ENTERTAINMENT" and ep.modality == "VIDEO"
        lesson = s.query(ContentEntity).filter_by(entity_type="lesson").one()
        assert lesson.content_class == "LEARNING"
        # asset 镜像与链接（全部 PRIMARY_VIDEO）
        assert s.query(MediaAsset).count() == 4
        assert s.query(EntityAsset).filter_by(role="PRIMARY_VIDEO").count() == 4
        link = (s.query(EntityAsset).join(ContentEntity, ContentEntity.id == EntityAsset.entity_id)
                .filter(ContentEntity.id == ep.id).one())
        assert link.asset_id == ep.source_media_id
        # tags → topic/character
        from kindo.models import ContentTopic

        ep_topics = [
            t.name for t in (
                s.query(ContentTopic)
                .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
                .filter(EntityTopic.entity_id == ep.id)
            )
        ]
        assert "救援" in ep_topics
        from kindo.models import ContentCharacter

        ep_chars = [
            c.name for c in (
                s.query(ContentCharacter)
                .join(EntityCharacter, EntityCharacter.character_id == ContentCharacter.id)
                .filter(EntityCharacter.entity_id == ep.id)
            )
        ]
        assert "天天" in ep_chars


@requires_ffprobe
def test_playback_snapshot_and_history_entity(library_env):
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    r = env.client.get("/api/v1/media", headers=headers).json()
    media = next(i for i in r["items"] if "第1集" in i["title"])
    media_id = media["media_id"]

    resp = env.client.post("/api/v1/playbacks", headers=headers, json={
        "media_id": media_id, "action": "play", "start_position_ms": 0, "source": "ui"})
    assert resp.status_code == 200, resp.text
    playback_id = resp.json()["playback_id"]

    from kindo.models import ContentEntity, Playback, ViewingInterval, WatchHistory

    with env.db.session() as s:
        pb = s.get(Playback, playback_id)
        ent = (s.query(ContentEntity)
               .filter(ContentEntity.source_media_id == media_id).one())
        assert pb.entity_id == ent.id
        assert pb.asset_id == media_id
        assert pb.content_class == "ENTERTAINMENT" and pb.modality == "VIDEO"

    # started + paused → interval 维度 + 断点挂 entity
    with env.client.websocket_connect(f"/api/v1/realtime?token={token}") as ws:
        ws.send_json({"type": "playback.started", "event_id": "ev-s1",
                      "playback_id": playback_id, "position_ms": 0})
        wait_ack(ws, "ev-s1")
        ws.send_json({"type": "playback.paused", "event_id": "ev-p1",
                      "playback_id": playback_id, "position_ms": 5000})
        wait_ack(ws, "ev-p1")

    with env.db.session() as s:
        vi = (s.query(ViewingInterval)
              .filter(ViewingInterval.playback_id == playback_id)
              .order_by(ViewingInterval.started_at.desc()).first())
        assert vi.content_class == "ENTERTAINMENT" and vi.modality == "VIDEO"
        ent = (s.query(ContentEntity)
               .filter(ContentEntity.source_media_id == media_id).one())
        wh = s.get(WatchHistory, (ent.id[:0] or pb.profile_id, media_id))
        assert wh is not None and wh.entity_id == ent.id


@requires_ffprobe
def test_directory_rename_preserves_history(library_env):
    """决策二验收：文件移动（目录改名）→ entity 身份与观看断点保留。"""
    env = library_env
    _d, token = env.pair_device()
    headers = env.device_headers(token)
    r = env.client.get("/api/v1/media", headers=headers).json()
    media = next(i for i in r["items"] if "第1集" in i["title"])
    media_id = media["media_id"]

    from kindo.models import ContentEntity, Media, WatchHistory

    with env.db.session() as s:
        ent_before = (s.query(ContentEntity)
                      .filter(ContentEntity.source_media_id == media_id).one())
        # 直接落一条带断点的观看历史（样例视频 8s，低于断点阈值，不走播放路径）
        profile = env.state.playback.default_profile_id(s)
        s.add(WatchHistory(profile_id=profile, media_id=media_id,
                           entity_id=ent_before.id, last_position_ms=6000,
                           watched_seconds=6, completed=False))
        s.commit()

    # 目录改名（文件字节不变：copy 保 mtime 由 shutil.copy2 保证）
    old_dir = env.media_dir / "series" / "汪汪队"
    new_dir = env.media_dir / "series" / "汪汪队立大功 第一季"
    shutil.copytree(old_dir, new_dir, copy_function=shutil.copy2)
    shutil.rmtree(old_dir)

    r = env.client.post("/api/v1/admin/media-mounts/family/scan",
                        headers=env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = env.client.get(f"/api/v1/admin/scan-jobs/{job_id}",
                             headers=env.admin_headers()).json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            break
        time.sleep(0.5)

    with env.db.session() as s:
        # 同实体、新锚点：watch_history 断点原样保留
        ent_after = (s.query(ContentEntity)
                     .filter(ContentEntity.id == ent_before.id).one())
        assert ent_after.id == ent_before.id
        assert ent_after.source_media_id != media_id  # 已迁到新 media 行
        wh_after = s.query(WatchHistory).filter(
            WatchHistory.entity_id == ent_after.id).one()
        assert wh_after.last_position_ms == 6000
        assert wh_after.media_id == ent_after.source_media_id
        # 旧 media 行已被迁移删除
        assert s.get(Media, media_id) is None


@requires_ffprobe
def test_audio_files_ingested_as_story_song(library_env):
    """v0.3 MED-005：音频文件进入内容目录（story/song，modality=AUDIO）。"""
    import subprocess

    from conftest import FFPROBE

    ffmpeg = FFPROBE.replace("ffprobe", "ffmpeg")
    target = library_env.media_dir / "songs"
    target.mkdir(parents=True, exist_ok=True)
    mp3 = target / "小星星.mp3"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=6",
                    "-c:a", "libmp3lame", str(mp3)],
                   check=True, capture_output=True, timeout=30)
    story_dir = library_env.media_dir / "stories"
    story_dir.mkdir(parents=True, exist_ok=True)
    m4a = story_dir / "睡前故事.m4a"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=5",
                    "-c:a", "aac", str(m4a)],
                   check=True, capture_output=True, timeout=30)
    (story_dir / "睡前故事.kindo.yaml").write_text(
        "entity_type: story\ntitle: 小海龟的睡前故事\n", encoding="utf-8")

    r = library_env.client.post("/api/v1/admin/media-mounts/family/scan",
                                headers=library_env.admin_headers())
    job_id = r.json()["job_id"]
    for _ in range(60):
        job = library_env.client.get(
            f"/api/v1/admin/scan-jobs/{job_id}",
            headers=library_env.admin_headers()).json()
        if job["state"] in ("done", "failed"):
            assert job["state"] == "done", job
            break
        time.sleep(0.5)

    from kindo.models import ContentEntity, Media

    with library_env.db.session() as s:
        song = (s.query(ContentEntity)
                .filter_by(entity_type="song", title="小星星").one())
        assert song.modality == "AUDIO" and song.content_class == "MUSIC"
        assert song.duration_ms >= 5000
        story = (s.query(ContentEntity)
                 .filter_by(entity_type="story").one())
        assert story.title == "小海龟的睡前故事"
        assert story.modality == "AUDIO" and story.content_class == "STORY"
        m_song = s.query(Media).filter_by(media_type="song").one()
        assert m_song.mime_type == "audio/mpeg"

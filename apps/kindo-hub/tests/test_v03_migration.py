"""v0.3 迁移回归：存量 v0.2 库 → 统一内容目录搬迁（P10，技术方案 §7.6）。

流程：空库 upgrade 0007 → 注入 v0.2 形状数据（media/series/episode/course/lesson/
watch_history/playback/policy v1/scraped_json）→ upgrade head → 断言 entity 树、
asset 映射、历史回填、身份搬迁、Policy 升维、活动 seed。
"""
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

HUB_ROOT = Path(__file__).resolve().parents[1]


def _upgrade(tmp_path, rev, db_url):
    cfg = Config(str(HUB_ROOT / "alembic.ini"))
    cfg.attributes["db_url"] = db_url
    command.upgrade(cfg, rev)


@pytest.fixture()
def v02_db(tmp_path):
    db_file = tmp_path / "kindo.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    _upgrade(tmp_path, "0007", db_url)
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA foreign_keys=ON")
    now = "2026-08-20 10:00:00+00:00"
    # 先 media（episode/lesson 的 FK 目标），再系列/课程结构
    for mid, mtype, title in [
        ("m_eps1", "episode", "汪汪队立大功 第1集"),
        ("m_eps2", "episode", "海底小纵队 第2集"),
        ("m_l1", "lesson", "第 2 课"),
        ("m_mv", "movie", "海底小纵队大电影"),
    ]:
        con.execute(
            "INSERT INTO media (id, mount_id, path_key, title, media_type, duration_ms,"
            " language, age_band, tags_json, parent_edited_json, metadata_version,"
            " size_bytes, mtime_ms, playable, probe_json, missing, has_poster,"
            " created_at, updated_at)"
            " VALUES (?, 'family', ?, ?, ?, 1200000, 'zh-CN', '3-6', ?, '{}', 1,"
            " 100, 100, 1, '{}', 0, 1, ?, ?)",
            (mid, f"x/{mid}.mp4", title, mtype,
             json.dumps({"characters": ["天天"], "themes": ["海洋"], "tags": []}), now, now))
    for i, (sid, stitle) in enumerate([("s1", "汪汪队立大功"), ("s2", "海底小纵队")]):
        con.execute("INSERT INTO series (id, title) VALUES (?, ?)", (sid, stitle))
        con.execute(
            "INSERT INTO episode (id, series_id, season_no, episode_no, media_id, title)"
            " VALUES (?, ?, 1, ?, ?, ?)",
            (f"ep{sid}", sid, i + 1, f"m_ep{sid}", f"{stitle} 第{i + 1}集"))
    con.execute("INSERT INTO course (id, title) VALUES ('c1', '英语启蒙')")
    con.execute(
        "INSERT INTO lesson (id, course_id, chapter_no, lesson_no, media_id, title)"
        " VALUES ('l1', 'c1', 1, 2, 'm_l1', '第 2 课')")
    con.execute("UPDATE media SET scraped_json = ? WHERE id = 'm_eps1'", (json.dumps({
        "source": "tmdb", "ref_id": 101, "matched_title": "汪汪队立大功",
        "poster_url": "u", "scraped_at": now}),))
    con.execute("UPDATE media SET scraped_json = ? WHERE id = 'm_mv'", (json.dumps({
        "source": "tmdb", "ref_id": 202, "matched_title": "海底小纵队大电影",
        "poster_url": "u", "scraped_at": now}),))
    con.execute("INSERT INTO profile (id, display_name) VALUES ('p1', 'default')")
    con.execute(
        "INSERT INTO watch_history (profile_id, media_id, last_position_ms,"
        " watched_seconds, completed, last_watched_at)"
        " VALUES ('p1', 'm_eps1', 740000, 700, 0, ?)", (now,))
    con.execute(
        "INSERT INTO playback (id, device_id, profile_id, media_id, action, source,"
        " state, position_ms, started_at, last_seen_at, watched_ms, created_at)"
        " VALUES ('pb1', 'd1', 'p1', 'm_eps1', 'play', 'ui', 'ended', 740000, ?, ?,"
        " 700000, ?)", (now, now, now))
    con.execute(
        "INSERT INTO viewing_interval (id, playback_id, started_at, ended_at,"
        " duration_ms, close_reason) VALUES ('v1', 'pb1', ?, ?, 700000, 'ended')",
        (now, now))
    con.execute(
        "INSERT INTO course_progress (profile_id, lesson_id, course_id, position_ms,"
        " completed, updated_at) VALUES ('p1', 'l1', 'c1', 60000, 0, ?)", (now,))
    con.execute(
        "INSERT INTO policy_config (version, rules_json, updated_at)"
        " VALUES (1, ?, ?)",
        (json.dumps({"daily_limit_minutes": 60, "session_limit_minutes": None,
                     "daily_episode_limit": None, "allowed_windows": [],
                     "content_scope": {}, "autoplay": True,
                     "course_counts_as_entertainment": True}), now))
    con.commit()
    con.close()
    return db_file, db_url


def test_v02_to_v03_migration(tmp_path, v02_db):
    db_file, db_url = v02_db
    _upgrade(tmp_path, "head", db_url)
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row

    # entity 树：2 series + 2 season + 2 episode + 1 course + 1 lesson + 1 movie
    def one(sql, *args):
        return con.execute(sql, args).fetchone()

    counts = {r["entity_type"]: r["n"] for r in con.execute(
        "SELECT entity_type, count(*) AS n FROM content_entity GROUP BY entity_type")}
    assert counts == {"series": 2, "season": 2, "episode": 2,
                      "course": 1, "lesson": 1, "movie": 1}, counts

    # episode.parent 必为 season，season.parent 必为 series（决策二树结构）
    for r in con.execute("SELECT id, parent_id FROM content_entity WHERE entity_type='episode'"):
        season = one("SELECT id, parent_id FROM content_entity WHERE id = ?", r["parent_id"])
        assert season and season["parent_id"] is not None, "episode 必须挂在 season 下"
        series = one("SELECT id FROM content_entity WHERE id = ?", season["parent_id"])
        assert series is not None

    # media_asset 1:1 同 id；entity_asset 全 PRIMARY_VIDEO
    assert one("SELECT count(*) FROM media_asset")[0] == 4
    assert one("SELECT count(*) FROM entity_asset WHERE role='PRIMARY_VIDEO'")[0] == 4
    # 一集 ↔ 一 asset 双向映射
    ep = one("SELECT id FROM content_entity WHERE source_media_id = 'm_eps1'")
    assert ep and one("SELECT count(*) FROM entity_asset WHERE entity_id=?", ep["id"])[0] == 1

    # 维度默认映射：episode/movie→ENTERTAINMENT，lesson→LEARNING；age_band 3-6 解析
    ent = one("SELECT * FROM content_entity WHERE source_media_id = 'm_eps1'")
    assert ent["content_class"] == "ENTERTAINMENT" and ent["modality"] == "VIDEO"
    assert ent["age_min"] == 3 and ent["age_max"] == 6
    lesson_ent = one("SELECT content_class FROM content_entity WHERE source_media_id='m_l1'")
    assert lesson_ent["content_class"] == "LEARNING"

    # tags → topic/character 关联
    topics = [r[0] for r in con.execute(
        "SELECT t.name FROM entity_topic et JOIN content_topic t ON t.id = et.topic_id"
        " WHERE et.entity_id = ?", (ent["id"],))]
    chars = [r[0] for r in con.execute(
        "SELECT c.name FROM entity_character ec JOIN content_character c ON c.id = ec.character_id"
        " WHERE ec.entity_id = ?", (ent["id"],))]
    assert topics == ["海洋"] and chars == ["天天"]

    # scraped_json → external_identity：集的身份挂 series；电影挂自身；match_status=auto
    series1 = one(
        "SELECT ce.id FROM content_entity ce WHERE ce.entity_type='series' AND ce.title=?",
        "汪汪队立大功")
    ident = one("SELECT * FROM external_identity WHERE entity_id = ?", series1["id"])
    assert ident and ident["provider"] == "tmdb" and ident["ref_id"] == "101"
    assert one("SELECT match_status FROM content_entity WHERE id=?", series1["id"])["match_status"] == "auto"
    movie_ent = one("SELECT id FROM content_entity WHERE source_media_id='m_mv'")
    assert one("SELECT ref_id FROM external_identity WHERE entity_id=?",
               movie_ent["id"])["ref_id"] == "202"

    # 历史回填：watch_history/playback/viewing_interval 挂 entity + 维度
    wh = one("SELECT * FROM watch_history WHERE media_id='m_eps1'")
    assert wh["entity_id"] == ent["id"]
    pb = one("SELECT entity_id, asset_id, content_class, modality FROM playback WHERE id='pb1'")
    assert (pb["entity_id"], pb["asset_id"], pb["content_class"], pb["modality"]) == (
        ent["id"], "m_eps1", "ENTERTAINMENT", "VIDEO")
    vi = one("SELECT content_class, modality FROM viewing_interval WHERE id='v1'")
    assert (vi["content_class"], vi["modality"]) == ("ENTERTAINMENT", "VIDEO")
    cp = one("SELECT lesson_entity_id FROM course_progress WHERE lesson_id='l1'")
    assert cp["lesson_entity_id"] == one(
        "SELECT id FROM content_entity WHERE source_media_id='m_l1'")["id"]

    # Policy v1 → v2 升维
    rules = json.loads(one(
        "SELECT rules_json FROM policy_config ORDER BY version DESC LIMIT 1")["rules_json"])
    assert rules["budgets"]["screen_total_minutes"] == 60
    assert rules["budgets"]["video_by_class"]["ENTERTAINMENT"] == 60
    assert rules["transition_policy"]["max_minutes"] == 4

    # 预置活动
    assert one("SELECT count(*) FROM transition_activity WHERE status='preset'")[0] >= 8

    # 幂等重跑
    con.close()
    _upgrade(tmp_path, "head", db_url)
    con = sqlite3.connect(db_file)
    assert one("SELECT count(*) FROM content_entity")[0] == 9
    con.close()


def test_migration_idempotent_on_fresh_v03(tmp_path):
    """全新库（app 启动路径）head 直达 + 二次 upgrade 幂等。"""
    db_file = tmp_path / "fresh.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    _upgrade(tmp_path, "head", db_url)
    _upgrade(tmp_path, "head", db_url)
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0017"
    con.close()

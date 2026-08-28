"""v0.3 数据搬迁：media/series/episode/course/lesson → 统一内容目录（技术方案 §7.6 步骤 2）

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-24

media → media_asset（同 id 1:1）；series → series entity + 默认"第 1 季" season entity；
episode → episode entity（parent=season）；course/lesson → course/lesson entity；
散 movie → movie entity；entity_asset 全部 PRIMARY_VIDEO；
watch_history.entity_id / course_progress.lesson_entity_id / playback 维度列回填；
scraped_json.ref_id → external_identity（match_status=auto）；
tags_json → entity_topic / entity_character。
幂等：已存在映射（source_media_id / media_asset 行）即跳过。
"""
import json
import re
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _as_json(v):
    """SQLite 经 sa.text 读 JSON 列得到字符串；统一转 dict/list。"""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _parse_age_band(band):
    """'3-6'→(3,6)；'3+'→(3,None)；'ALL'/空/解析失败→(None,None)。"""
    if not band:
        return None, None
    m = re.match(r"^\s*(\d{1,2})\s*[-–~至]\s*(\d{1,2})\s*$", str(band))
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*(\d{1,2})\s*\+?\s*$", str(band))
    if m:
        return int(m.group(1)), None
    return None, None


def upgrade() -> None:
    conn = op.get_bind()

    # ---------- 1. media → media_asset（同 id） ----------
    media_rows = conn.execute(sa.text(
        "SELECT id, mount_id, path_key, duration_ms, mime_type, language, age_band,"
        " title, media_type, size_bytes, mtime_ms, playable, probe_json, missing,"
        " has_poster, tags_json, scraped_json FROM media"
    )).mappings().all()
    for m in media_rows:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM media_asset WHERE id = :id"), {"id": m["id"]}).scalar()
        if exists:
            continue
        conn.execute(sa.text(
            "INSERT INTO media_asset (id, mount_id, path_key, file_kind, size_bytes,"
            " mtime_ms, duration_ms, mime_type, probe_json, playable, missing,"
            " has_poster, created_at, updated_at)"
            " VALUES (:id, :mount, :path, 'video', :size, :mtime, :dur, :mime,"
            " :probe, :playable, :missing, :poster, :now, :now)"), {
            "id": m["id"], "mount": m["mount_id"], "path": m["path_key"],
            "size": m["size_bytes"] or 0, "mtime": m["mtime_ms"] or 0,
            "dur": m["duration_ms"] or 0, "mime": m["mime_type"],
            "probe": json.dumps(_as_json(m["probe_json"]) or {}, ensure_ascii=False),
            "playable": 1 if m["playable"] else 0,
            "missing": 1 if m["missing"] else 0,
            "poster": 1 if m["has_poster"] else 0, "now": _now(),
        })

    # entity 映射缓存：media_id → entity_id（episode/lesson/movie 内容实体）
    media_entity: dict[str, str] = {}

    def _link(entity_id, asset_id):
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO entity_asset (id, entity_id, asset_id, role, sequence)"
            " VALUES (:id, :eid, :aid, 'PRIMARY_VIDEO', 1)"), {
            "id": str(uuid.uuid4()), "eid": entity_id, "aid": asset_id})

    media_by_id = {m["id"]: m for m in media_rows}

    # ---------- 2. series → series entity + 默认第 1 季 season ----------
    series_rows = conn.execute(sa.text(
        "SELECT id, title, language FROM series")).mappings().all()
    series_entity: dict[str, str] = {}
    season_entity: dict[str, str] = {}
    for s in series_rows:
        existing = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='series' AND title = :t"
            " ORDER BY created_at LIMIT 1"), {"t": s["title"]}).scalar()
        sid = existing or str(uuid.uuid4())
        series_entity[s["id"]] = sid
        if not existing:
            conn.execute(sa.text(
                "INSERT INTO content_entity (id, entity_type, parent_id, title,"
                " language, modality, duration_ms, sequence_no, meta_provenance_json,"
                " match_status, ordering, created_at, updated_at)"
                " VALUES (:id, 'series', NULL, :title, :lang, 'VIDEO', 0, 1, '{}',"
                " 'none', 'STANDARD', :now, :now)"), {
                "id": sid, "title": s["title"], "lang": s["language"], "now": _now()})
        # 默认"第 1 季"：单季系列 UI 折叠（决策二），显式 season 行保证 episode.parent 必为 season
        existing_season = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='season' AND parent_id=:sid"
            " ORDER BY sequence_no LIMIT 1"), {"sid": sid}).scalar()
        seid = existing_season or str(uuid.uuid4())
        season_entity[s["id"]] = seid
        if not existing_season:
            conn.execute(sa.text(
                "INSERT INTO content_entity (id, entity_type, parent_id, title,"
                " duration_ms, sequence_no, meta_provenance_json, match_status,"
                " created_at, updated_at)"
                " VALUES (:id, 'season', :sid, '第 1 季', 0, 1, '{}', 'none', :now, :now)"),
                {"id": seid, "sid": sid, "now": _now()})

    # ---------- 3. episode → episode entity ----------
    ep_rows = conn.execute(sa.text(
        "SELECT e.id, e.series_id, e.season_no, e.episode_no, e.media_id, e.title"
        " FROM episode e")).mappings().all()
    for e in ep_rows:
        m = media_by_id.get(e["media_id"])
        src_mid = e["media_id"]
        existing = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='episode'"
            " AND source_media_id = :mid"), {"mid": src_mid}).scalar()
        if existing:
            media_entity[src_mid] = existing
            continue
        eid = str(uuid.uuid4())
        a_min, a_max = (None, None)
        cc = "ENTERTAINMENT"
        dur = (m["duration_ms"] or 0) if m else 0
        lang = m["language"] if m else None
        title = e["title"] or (m["title"] if m else "")
        if m:
            a_min, a_max = _parse_age_band(m["age_band"])
        conn.execute(sa.text(
            "INSERT INTO content_entity (id, entity_type, parent_id, title, language,"
            " content_class, modality, age_min, age_max, duration_ms, sequence_no,"
            " meta_provenance_json, match_status, source_media_id, created_at, updated_at)"
            " VALUES (:id, 'episode', :parent, :title, :lang, :cc, 'VIDEO', :amin, :amax,"
            " :dur, :seq, '{}', 'none', :srcmid, :now, :now)"), {
            "id": eid, "parent": season_entity.get(e["series_id"]),
            "title": title, "lang": lang, "cc": cc, "amin": a_min, "amax": a_max,
            "dur": dur, "seq": e["episode_no"] or 1,
            "srcmid": src_mid, "now": _now()})
        if m is not None:
            _link(eid, m["id"])
        media_entity[src_mid] = eid

    # ---------- 4. course / lesson ----------
    course_rows = conn.execute(sa.text(
        "SELECT id, title, language FROM course")).mappings().all()
    course_entity: dict[str, str] = {}
    for c in course_rows:
        existing = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='course' AND title=:t"
            " ORDER BY created_at LIMIT 1"), {"t": c["title"]}).scalar()
        cid = existing or str(uuid.uuid4())
        course_entity[c["id"]] = cid
        if not existing:
            conn.execute(sa.text(
                "INSERT INTO content_entity (id, entity_type, parent_id, title,"
                " language, content_class, modality, duration_ms, sequence_no,"
                " meta_provenance_json, match_status, created_at, updated_at)"
                " VALUES (:id, 'course', NULL, :title, :lang, 'LEARNING', 'VIDEO', 0, 1,"
                " '{}', 'none', :now, :now)"),
                {"id": cid, "title": c["title"], "lang": c["language"], "now": _now()})
    lesson_rows = conn.execute(sa.text(
        "SELECT l.id, l.course_id, l.chapter_no, l.lesson_no, l.media_id, l.title"
        " FROM lesson l")).mappings().all()
    lesson_entity_map: dict[str, str] = {}
    for l in lesson_rows:
        m = media_by_id.get(l["media_id"])
        existing = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='lesson'"
            " AND source_media_id = :mid"), {"mid": l["media_id"]}).scalar()
        if existing:
            lid = existing
        else:
            lid = str(uuid.uuid4())
            a_min, a_max = _parse_age_band(m["age_band"]) if m else (None, None)
            conn.execute(sa.text(
                "INSERT INTO content_entity (id, entity_type, parent_id, title,"
                " language, content_class, modality, age_min, age_max, duration_ms,"
                " sequence_no, meta_provenance_json, match_status, source_media_id,"
                " created_at, updated_at)"
                " VALUES (:id, 'lesson', :parent, :title, :lang, 'LEARNING', 'VIDEO',"
                " :amin, :amax, :dur, :seq, '{}', 'none', :srcmid, :now, :now)"), {
                "id": lid, "parent": course_entity.get(l["course_id"]),
                "title": l["title"] or (m["title"] if m else ""),
                "lang": m["language"] if m else None,
                "amin": a_min, "amax": a_max,
                "dur": (m["duration_ms"] or 0) if m else 0,
                "seq": l["lesson_no"] or 1, "srcmid": l["media_id"], "now": _now()})
            if m is not None:
                _link(lid, m["id"])
        lesson_entity_map[l["id"]] = lid
        media_entity[l["media_id"]] = lid

    # ---------- 5. 散 movie ----------
    for m in media_rows:
        if m["media_type"] != "movie" or m["id"] in media_entity:
            continue
        existing = conn.execute(sa.text(
            "SELECT id FROM content_entity WHERE entity_type='movie'"
            " AND source_media_id = :mid"), {"mid": m["id"]}).scalar()
        if existing:
            media_entity[m["id"]] = existing
            continue
        eid = str(uuid.uuid4())
        a_min, a_max = _parse_age_band(m["age_band"])
        conn.execute(sa.text(
            "INSERT INTO content_entity (id, entity_type, parent_id, title, language,"
            " content_class, modality, age_min, age_max, duration_ms, sequence_no,"
            " meta_provenance_json, match_status, source_media_id, created_at, updated_at)"
            " VALUES (:id, 'movie', NULL, :title, :lang, 'ENTERTAINMENT', 'VIDEO', :amin,"
            " :amax, :dur, 1, '{}', 'none', :srcmid, :now, :now)"), {
            "id": eid, "title": m["title"], "lang": m["language"],
            "amin": a_min, "amax": a_max, "dur": m["duration_ms"] or 0,
            "srcmid": m["id"], "now": _now()})
        _link(eid, m["id"])
        media_entity[m["id"]] = eid

    # ---------- 6. tags_json → topic / character 关联 ----------
    def _get_or_create(kind: str, name: str) -> str:
        table = "content_topic" if kind == "topic" else "content_character"
        existing = conn.execute(sa.text(
            f"SELECT id FROM {table} WHERE name = :n"), {"n": name}).scalar()
        if existing:
            return existing
        new_id = str(uuid.uuid4())
        conn.execute(sa.text(
            f"INSERT INTO {table} (id, name, aliases_json) VALUES (:id, :n, '[]')"),
            {"id": new_id, "n": name})
        return new_id

    for m in media_rows:
        eid = media_entity.get(m["id"])
        tags = _as_json(m["tags_json"])
        if not eid or not isinstance(tags, dict):
            continue
        for theme in (tags.get("themes") or []) + (tags.get("tags") or []):
            tid = _get_or_create("topic", str(theme))
            conn.execute(sa.text(
                "INSERT OR IGNORE INTO entity_topic (entity_id, topic_id)"
                " VALUES (:e, :t)"), {"e": eid, "t": tid})
        for ch in tags.get("characters") or []:
            cid = _get_or_create("character", str(ch))
            conn.execute(sa.text(
                "INSERT OR IGNORE INTO entity_character (entity_id, character_id)"
                " VALUES (:e, :c)"), {"e": eid, "c": cid})

    # ---------- 7. scraped_json → external_identity ----------
    for m in media_rows:
        sj = _as_json(m["scraped_json"])
        if not isinstance(sj, dict) or not sj.get("ref_id"):
            continue
        target = media_entity.get(m["id"])
        if m["media_type"] == "episode":
            # 系列级身份写到 series entity（决策三：身份在系列）
            ep = conn.execute(sa.text(
                "SELECT series_id FROM episode WHERE media_id = :mid"),
                {"mid": m["id"]}).scalar()
            target = series_entity.get(ep) or target
        if not target:
            continue
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO external_identity (id, entity_id, provider,"
            " ref_id, matched_title, created_at)"
            " VALUES (:id, :eid, 'tmdb', :ref, :title, :now)"), {
            "id": str(uuid.uuid4()), "eid": target,
            "ref": str(sj["ref_id"]), "title": sj.get("matched_title"), "now": _now()})
        conn.execute(sa.text(
            "UPDATE content_entity SET match_status = 'auto' WHERE id = :eid"),
            {"eid": target})

    # ---------- 8. 历史回填 ----------
    for media_id, eid in media_entity.items():
        conn.execute(sa.text(
            "UPDATE watch_history SET entity_id = :eid WHERE media_id = :mid"
            " AND entity_id IS NULL"), {"eid": eid, "mid": media_id})
        conn.execute(sa.text(
            "UPDATE playback SET entity_id = :eid, asset_id = :mid,"
            " content_class = (SELECT content_class FROM content_entity WHERE id = :eid),"
            " modality = 'VIDEO'"
            " WHERE media_id = :mid AND entity_id IS NULL"), {"eid": eid, "mid": media_id})
    for lrow in lesson_rows:
        conn.execute(sa.text(
            "UPDATE course_progress SET lesson_entity_id = :leid WHERE lesson_id = :lid"
            " AND lesson_entity_id IS NULL"),
            {"leid": lesson_entity_map.get(lrow["id"]), "lid": lrow["id"]})
    # viewing_interval 维度随 playback 快照回填
    conn.execute(sa.text(
        "UPDATE viewing_interval SET"
        " content_class = (SELECT content_class FROM playback"
        "   WHERE playback.id = viewing_interval.playback_id),"
        " modality = (SELECT modality FROM playback"
        "   WHERE playback.id = viewing_interval.playback_id)"
        " WHERE content_class IS NULL"))

    # 兼容索引（旧库 ALTER 增列不自动建）
    _index_safe("watch_history", "ix_watch_history_entity_id", ["entity_id"])
    _index_safe("course_progress", "ix_course_progress_lesson_entity_id", ["lesson_entity_id"])
    _index_safe("playback", "ix_playback_entity_id", ["entity_id"])


def _index_safe(table: str, name: str, cols: list) -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return
    idx = {i["name"] for i in insp.get_indexes(table)}
    if name not in idx:
        op.create_index(name, table, cols)


def downgrade() -> None:
    # 搬迁不可逆（新表数据保留供回退重放）；回退走 pre_migration_backup
    pass

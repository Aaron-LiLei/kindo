"""Media Catalog：面向 UI 与 Tool 的结构化检索（架构 §8，V0.1 不依赖向量库）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_ as sa_and
from sqlalchemy import exists as sa_exists
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Course, Episode, Lesson, Media, MediaMount, Series, SubtitleTrack, WatchHistory


def _tag_match_expr(term: str):
    """检索匹配域（2026-08-26 ContentEntity 融合）：media 标题、tags_json 元素、
    所属系列/课程标题（v0.2 行）、以及经实体树锚定的系列实体标题（TMDB 确认后
    的中文系列名可检索到其剧集——episode entity→season→series root）。
    SQLite json_each 表值函数。"""
    like = f"%{term}%"
    tags_tv = func.json_each(Media.tags_json).table_valued("value")
    from sqlalchemy.orm import aliased

    from ..models import ContentEntity

    season_ent = aliased(ContentEntity)
    series_ent = aliased(ContentEntity)
    return or_(
        Media.title.ilike(like),
        sa_exists(select(1).select_from(tags_tv).where(tags_tv.c.value.ilike(like))),
        # 所属系列行标题（Episode → Series）
        sa_exists(
            select(1).select_from(Episode, Series)
            .where(Episode.media_id == Media.id,
                   Series.id == Episode.series_id,
                   Series.title.ilike(like))),
        # 所属课程行标题（Lesson → Course）
        sa_exists(
            select(1).select_from(Lesson, Course)
            .where(Lesson.media_id == Media.id,
                   Course.id == Lesson.course_id,
                   Course.title.ilike(like))),
        # 本媒体实体标题（movie/episode 实体经家长编辑/确认后的标题）
        sa_exists(
            select(1).select_from(ContentEntity)
            .where(ContentEntity.source_media_id == Media.id,
                   ContentEntity.title.ilike(like))),
        # 系列实体标题（TMDB 中文系列名检索到集；episode→season→series）
        sa_exists(
            select(1)
            .select_from(ContentEntity)
            .join(season_ent, season_ent.id == ContentEntity.parent_id)
            .join(series_ent, series_ent.id == season_ent.parent_id)
            .where(ContentEntity.source_media_id == Media.id,
                   series_ent.title.ilike(like))),
    )


def _hide_alternate_versions(session: Session):
    """PLY-009：同一实体存在 PRIMARY_VIDEO 版本时，ALTERNATE_VIDEO 版本
    不进浏览/检索（家长可设首选版本；单版本内容不受影响）。"""
    from ..models import EntityAsset

    primary_entities = (
        session.query(EntityAsset.entity_id)
        .filter(EntityAsset.role == "PRIMARY_VIDEO")
        .subquery())
    alt_ids = (
        session.query(EntityAsset.asset_id)
        .filter(EntityAsset.role == "ALTERNATE_VIDEO",
                EntityAsset.entity_id.in_(select(primary_entities)))
        .subquery())
    return ~Media.id.in_(select(alt_ids))


def search_media(
    session: Session,
    query: str,
    media_types: list[str] | None = None,
    language: str | None = None,
    tags: list[str] | None = None,
    limit: int = 4,
    cursor: tuple[str, str] | None = None,
) -> tuple[list[Media], tuple[str, str] | None]:
    """检索（title 或 tags 匹配）。排序：标题短优先（更接近检索意图，避免
    "汪汪队立大功 第3季 4K_H265 第31集" 挤掉 "…第一季 第1集"）、标题升序、
    id 决胜；游标 = (title, id)，长度可由 title 推导，支持翻页（§3.1）。"""
    terms = [t for t in query.replace("，", " ").split() if t.strip()]
    q = session.query(Media).filter(Media.missing.is_(False), Media.playable.is_(True))
    q = q.filter(_hide_alternate_versions(session))
    if media_types:
        q = q.filter(Media.media_type.in_(media_types))
    if language:
        q = q.filter(Media.language == language)
    if terms:
        for term in terms:
            q = q.filter(_tag_match_expr(term))
    if tags:
        for tag in tags:
            q = q.filter(_tag_match_expr(tag))
    if cursor:
        cur_title, cur_id = cursor
        cur_len = func.length(cur_title)
        q = q.filter(
            or_(
                func.length(Media.title) > cur_len,
                sa_and(
                    func.length(Media.title) == cur_len,
                    or_(
                        Media.title > cur_title,
                        sa_and(Media.title == cur_title, Media.id > cur_id),
                    ),
                ),
            ),
        )
    q = q.order_by(func.length(Media.title).asc(), Media.title.asc(), Media.id.asc())
    items = q.limit(limit + 1).all()
    next_cursor = (
        (items[-1].title, items[-1].id) if len(items) > limit else None
    )
    return items[:limit], next_cursor


def list_media(
    session: Session,
    *,
    media_type: str | None = None,
    language: str | None = None,
    tag: str | None = None,
    series_id: str | None = None,
    course_id: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
    include_missing: bool = False,
    sort: str = "added",
) -> tuple[list[Media], str | None]:
    """sort: added=最新添加（created_at desc，默认；此前按随机 uuid 序无意义）
    / title=标题 A-Z。cursor 为排序键 \x1f id 的组合串（翻页稳定）。"""
    q = session.query(Media)
    if not include_missing:
        q = q.filter(Media.missing.is_(False))
    q = q.filter(_hide_alternate_versions(session))
    if media_type:
        q = q.filter(Media.media_type == media_type)
    if language:
        q = q.filter(Media.language == language)
    if tag:
        q = q.filter(_tag_match_expr(tag))
    if series_id:
        q = q.join(Episode, Episode.media_id == Media.id).filter(Episode.series_id == series_id)
    if course_id:
        q = q.join(Lesson, Lesson.media_id == Media.id).filter(Lesson.course_id == course_id)

    if sort == "title":
        order_cols = (Media.title.asc(), Media.id.asc())
    else:
        order_cols = (Media.created_at.desc(), Media.id.desc())  # type: ignore[assignment]
    # 复合游标：排序键 \x1f id（与 TV 搜索同机制）
    if cursor:
        key, _, cur_id = cursor.rpartition("\x1f")
        try:
            if sort == "title":
                q = q.filter((Media.title > key) |
                             ((Media.title == key) & (Media.id > cur_id)))
            else:
                created = datetime.fromisoformat(key)
                q = q.filter((Media.created_at < created) |
                             ((Media.created_at == created) & (Media.id < cur_id)))
        except (ValueError, TypeError):
            pass  # 非法游标按首页处理

    q = q.order_by(*order_cols)
    items = q.limit(limit + 1).all()
    if len(items) > limit:
        last = items[limit - 1]
        if sort == "title":
            next_cursor = f"{last.title}\x1f{last.id}"
        else:
            next_cursor = f"{last.created_at.isoformat()}\x1f{last.id}"
    else:
        next_cursor = None
    return items[:limit], next_cursor


def media_series_map(session: Session, media_ids: list[str]) -> dict[str, dict]:
    """批量取 media → 所属系列信息（admin 列表序列化用，避免逐行查询）。"""
    if not media_ids:
        return {}
    rows = (
        session.query(Episode, Series)
        .join(Series, Series.id == Episode.series_id)
        .filter(Episode.media_id.in_(media_ids))
        .all()
    )
    return {
        ep.media_id: {
            "series_id": series.id,
            "title": series.title,
            "season_no": ep.season_no,
            "episode_no": ep.episode_no,
        }
        for ep, series in rows
    }


def media_course_map(session: Session, media_ids: list[str]) -> dict[str, dict]:
    """批量取 media → 所属课程信息。"""
    if not media_ids:
        return {}
    rows = (
        session.query(Lesson, Course)
        .join(Course, Course.id == Lesson.course_id)
        .filter(Lesson.media_id.in_(media_ids))
        .all()
    )
    return {
        lesson.media_id: {
            "course_id": course.id,
            "title": course.title,
            "chapter_no": lesson.chapter_no,
            "lesson_no": lesson.lesson_no,
        }
        for lesson, course in rows
    }


def admin_collections(session: Session) -> dict:
    """系列/课程聚合（admin 媒体库“按合集浏览”）。封面取合集内首个有海报的媒体，
    否则按集号排序的第一条；单家庭库规模（千级）一次返回可接受。
    聚合补充（2026-08-20）：age_band（成员众数）、tags（角色/主题出现频次 top4）、
    size_bytes 合计、来源挂载列表——网络源跳过探测时 duration 全 0，卡片需要
    其他信息维度。"""

    def _collect(child_table, order_cols, parent_table, parent_fk):
        # missing 媒体（源端已删除）不计入合集——否则幽灵条目翻倍集数、
        # 空壳系列常驻合集视图（2026-08-21 媒体库治理修复）
        rows = (
            session.query(parent_table, Media)
            .join(child_table, child_table.media_id == Media.id)
            .join(parent_table, parent_table.id == parent_fk)
            .filter(Media.missing.is_(False))
            .order_by(parent_table.title, *order_cols)
            .all()
        )
        grouped: dict[str, dict] = {}
        for parent, media in rows:
            g = grouped.setdefault(parent.id, {
                "title": parent.title,
                "language": parent.language,
                "count": 0,
                "duration_ms": 0,
                "size_bytes": 0,
                "cover_media_id": None,
                "cover_has_poster": False,
                "age_bands": {},
                "tag_counts": {},
                "mount_ids": {},
            })
            g["count"] += 1
            g["duration_ms"] += media.duration_ms or 0
            g["size_bytes"] += media.size_bytes or 0
            if not g["cover_has_poster"]:
                g["cover_media_id"] = media.id
                g["cover_has_poster"] = media.has_poster
            if media.age_band:
                g["age_bands"][media.age_band] = g["age_bands"].get(media.age_band, 0) + 1
            tags = media.tags_json or {}
            for group in ("characters", "themes"):
                for t in tags.get(group) or []:
                    g["tag_counts"][t] = g["tag_counts"].get(t, 0) + 1
            g["mount_ids"][media.mount_id] = g["mount_ids"].get(media.mount_id, 0) + 1

        mount_labels = {
            mm.id: mm.label for mm in session.query(MediaMount).all()
        }
        for g in grouped.values():
            age = max(g.pop("age_bands").items(), key=lambda kv: kv[1], default=None)
            tags = sorted(g.pop("tag_counts").items(), key=lambda kv: (-kv[1], kv[0]))[:4]
            mounts = sorted(g.pop("mount_ids").items(), key=lambda kv: -kv[1])
            g["age_band"] = age[0] if age else None
            g["tags"] = [t for t, _ in tags]
            g["mounts"] = [
                {"mount_id": mid, "label": mount_labels.get(mid, mid)} for mid, _ in mounts
            ]
        return grouped

    series = _collect(Episode, (Episode.season_no, Episode.episode_no), Series, Episode.series_id)
    courses = _collect(Lesson, (Lesson.chapter_no, Lesson.lesson_no), Course, Lesson.course_id)
    # v0.3：系列卡优先 Series poster（MED-013），并携带身份匹配状态与 TMDB 标题参照
    # 2026-08-26：系列↔实体改结构锚点关联（episode entity→season→series root），
    # 改系列名不断链；title 匹配仅作 legacy 回退（content_catalog.series_entities_by_series）
    from ..models import ExternalIdentity
    from .content_catalog import series_entities_by_series

    series_rows = session.query(Series).filter(Series.id.in_(series.keys())).all()
    entity_by_sid = series_entities_by_series(session, series_rows)
    series_entities = {e.id: e for e in entity_by_sid.values()}
    if series_entities:
        from ..models import ArtworkAsset

        ident_rows = {
            i.entity_id: i for i in (
                session.query(ExternalIdentity)
                .filter(ExternalIdentity.entity_id.in_(series_entities.keys()))
                .all())
        }
        artwork_entities = {
            r[0] for r in (
                session.query(ArtworkAsset.entity_id)
                .filter(ArtworkAsset.entity_id.in_(series_entities.keys()),
                        ArtworkAsset.kind == "poster").all())
        }
    else:
        ident_rows = {}
        artwork_entities = set()
    for _sid, g in series.items():
        ent = entity_by_sid.get(_sid)
        if ent is None:
            continue
        g["entity_id"] = ent.id
        g["match_status"] = ent.match_status
        g["entity_poster"] = ent.id in artwork_entities
        ident = ident_rows.get(ent.id)
        g["matched_title"] = ident.matched_title if ident else None

    # 库内实际类型分布（TV 筛选 chips 按内容派生，2026-08-25 与 Admin 同口径）
    type_counts = {
        r[0]: r[1] for r in (
            session.query(Media.media_type, __import__("sqlalchemy").func.count(Media.id))
            .filter(Media.missing.is_(False))
            .group_by(Media.media_type).all())
    }

    # 系列墙排序：有真实海报的系列优先（首屏不给一排中性占位块，2026-08-24
    # TV 端审计 P3；Admin 按合集视图同步受益）
    return {
        "type_counts": type_counts,
        "series": [
            {"series_id": sid, **g} for sid, g in sorted(
                series.items(),
                key=lambda kv: (not (kv[1].get("entity_poster")
                                     or kv[1]["cover_has_poster"]),
                                kv[1]["title"]))
        ],
        "courses": [
            {"course_id": cid, **g} for cid, g in sorted(courses.items(), key=lambda kv: kv[1]["title"])
        ],
    }


def get_media(session: Session, media_id: str) -> Media | None:
    return session.get(Media, media_id)


def media_detail(session: Session, media: Media, profile_id: str) -> dict:
    """详情 + 系列/课程结构 + 轨道 + 断点（TV §3.1 GET /media/{id}）。"""
    episode = session.query(Episode).filter(Episode.media_id == media.id).one_or_none()
    series = None
    series_episodes: list[dict] = []
    if episode:
        series = session.get(Series, episode.series_id)
        eps = (
            session.query(Episode)
            .filter(Episode.series_id == episode.series_id)
            .order_by(Episode.season_no, Episode.episode_no)
            .all()
        )
        media_ids = [e.media_id for e in eps]
        medias = {m.id: m for m in session.query(Media).filter(Media.id.in_(media_ids)).all()}
        histories = {
            h.media_id: h
            for h in session.query(WatchHistory)
            .filter(WatchHistory.profile_id == profile_id, WatchHistory.media_id.in_(media_ids))
            .all()
        }
        for e in eps:
            m = medias.get(e.media_id)
            if m is None or m.missing:
                continue
            h = histories.get(e.media_id)
            series_episodes.append({
                "media_id": m.id,
                "title": e.title or m.title,
                "season_no": e.season_no,
                "episode_no": e.episode_no,
                "duration_ms": m.duration_ms,
                "has_poster": m.has_poster,
                "last_position_ms": h.last_position_ms if h else 0,
                "completed": h.completed if h else False,
            })

    lesson = session.query(Lesson).filter(Lesson.media_id == media.id).one_or_none()
    course = None
    course_lessons: list[dict] = []
    if lesson:
        course = session.get(Course, lesson.course_id)
        lss = (
            session.query(Lesson)
            .filter(Lesson.course_id == lesson.course_id)
            .order_by(Lesson.chapter_no, Lesson.lesson_no)
            .all()
        )
        media_ids = [x.media_id for x in lss]
        medias = {m.id: m for m in session.query(Media).filter(Media.id.in_(media_ids)).all()}
        from ..models import CourseProgress  # 局部导入避免循环

        progresses = {
            p.lesson_id: p
            for p in session.query(CourseProgress)
            .filter(CourseProgress.profile_id == profile_id,
                        CourseProgress.lesson_id.in_([x.id for x in lss]))
            .all()
        }
        for lesson in lss:
            m = medias.get(lesson.media_id)
            if m is None or m.missing:
                continue
            p = progresses.get(lesson.id)
            course_lessons.append({
                "media_id": m.id,
                "lesson_id": lesson.id,
                "title": lesson.title or m.title,
                "chapter_no": lesson.chapter_no,
                "lesson_no": lesson.lesson_no,
                "duration_ms": m.duration_ms,
                "position_ms": p.position_ms if p else 0,
                "completed": p.completed if p else False,
            })

    tracks = session.query(SubtitleTrack).filter(SubtitleTrack.media_id == media.id).all()
    history = session.get(WatchHistory, (profile_id, media.id))

    # v0.3 Canonical 维度与简介（交互 §4.3：儿童端只展示，不显示来源与锁定）
    overview = None
    modality = None
    content_class = None
    from ..models import ContentEntity

    entity = (session.query(ContentEntity)
              .filter(ContentEntity.source_media_id == media.id).first())
    if entity is not None:
        overview = entity.overview
        modality = entity.modality
        content_class = entity.content_class

    # 系列实体海报（详情页左侧大图回退——集级无海报时用系列 TMDB 海报）；
    # 2026-08-26 结构锚点关联（改名不断链），见 content_catalog.series_entities_by_series
    series_entity_poster = None
    series_entity_id = None
    if series is not None:
        from ..models import ArtworkAsset
        from .content_catalog import series_entities_by_series

        sent = series_entities_by_series(session, [series]).get(series.id)
        if sent is not None:
            series_entity_id = sent.id
            series_entity_poster = (
                session.query(ArtworkAsset)
                .filter(ArtworkAsset.entity_id == sent.id,
                        ArtworkAsset.kind == "poster")
                .first() is not None)

    return {
        "media_id": media.id,
        "title": media.title,
        "media_type": media.media_type,
        "duration_ms": media.duration_ms,
        "language": media.language,
        "age_band": media.age_band,
        "tags": media.tags_json or {},
        "playable": media.playable,
        # §1.2 兼容信息（2026-08-26 direct play 兜底）：探测明细出口，
        # probe_mode=skip 的网络源无编码数据（probed=False）
        "compatibility": {
            "playable": media.playable,
            "probed": bool(media.probe_json) and "skipped" not in (media.probe_json or {}),
            "container": (media.probe_json or {}).get("container"),
            "video_codec": (media.probe_json or {}).get("video_codec"),
            "notes": (media.probe_json or {}).get("notes") or [],
        },
        "overview": overview,
        "modality": modality,
        "content_class": content_class,
        "series_entity_id": series_entity_id,
        "series_entity_poster": series_entity_poster,
        "series": (
            {
                "series_id": series.id,
                "title": series.title,
                "season_no": episode.season_no if episode else None,
                "episode_no": episode.episode_no if episode else None,
                "episodes": series_episodes,
            }
            if series and episode
            else None
        ),
        "course": (
            {
                "course_id": course.id,
                "title": course.title,
                "chapter_no": lesson.chapter_no if lesson else None,
                "lesson_no": lesson.lesson_no if lesson else None,
                "lessons": course_lessons,
            }
            if course and lesson
            else None
        ),
        "subtitle_tracks": [
            {
                "track_id": t.id,
                "language": t.language,
                "source_type": t.source_type,
                "label": t.label,
                "grounding_available": t.grounding_available,
            }
            for t in tracks
        ],
        "audio_tracks": (media.probe_json or {}).get("audio", []),
        "watch": (
            {
                "last_position_ms": history.last_position_ms,
                "watched_seconds": history.watched_seconds,
                "completed": history.completed,
            }
            if history
            else None
        ),
    }


def public_media_fields(media: Media) -> dict:
    """Tool/LLM 用的字段白名单（§8.4：内部 path、token、secret 全部剥离）。"""
    return {
        "media_id": media.id,
        "title": media.title,
        "media_type": media.media_type,
        "duration_ms": media.duration_ms,
        "language": media.language,
        "age_band": media.age_band,
        "tags": media.tags_json or {},
    }

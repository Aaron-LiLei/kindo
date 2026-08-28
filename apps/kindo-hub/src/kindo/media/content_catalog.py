"""统一内容目录同步层（v0.3 阶段 1c，产品基线 v0.3 决策二）。

职责：把 v0.2 写入路径（scanner → media/series/episode/course/lesson）的每次变更
幂等同步到 v0.3 统一目录（media_asset / content_entity 树 / entity_asset /
topic 关联）。v0.3 元数据管线（阶段 2 Normalizer）接入前，v0.2 行仍是这些
结构字段的写入源；Canonical 值字段（content_class 等）只做缺省映射，不覆盖
既有值——六级优先级合并在阶段 2 落地。

同步语义：
- media 行 → media_asset（同 id 镜像）；
- episode/lesson/movie 绑定 → 内容实体（source_media_id 锚定幂等）；
- series → series entity + 按 season_no 的 season entity（episode.parent 必为 season）；
- course → course entity；
- tags_json → entity_topic / entity_character；
- missing 传导到 media_asset。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models import (
    ContentCharacter,
    ContentEntity,
    ContentTopic,
    Course,
    EntityAsset,
    EntityCharacter,
    EntityTopic,
    Episode,
    Lesson,
    Media,
    MediaAsset,
    Series,
)
from ..util import new_id

logger = logging.getLogger("kindo.content_catalog")


def parse_age_band(band: str | None) -> tuple[int | None, int | None]:
    """'3-6'→(3,6)；'3+'→(3,None)；其余→(None,None)。与 0009 迁移一致。"""
    if not band:
        return None, None
    m = re.match(r"^\s*(\d{1,2})\s*[-–~至]\s*(\d{1,2})\s*$", str(band))
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*(\d{1,2})\s*\+?\s*$", str(band))
    if m:
        return int(m.group(1)), None
    return None, None


def default_content_class(entity_type: str, media_type: str | None = None) -> str:
    """结构缺省映射（决策二）；家长/Provider 级值永不在此覆盖。"""
    if entity_type in ("course", "lesson"):
        return "LEARNING"
    return "ENTERTAINMENT"


# ---------- media_asset 镜像 ----------

def sync_asset(session: Session, media: Media) -> MediaAsset:
    asset = session.get(MediaAsset, media.id)
    if asset is None:
        asset = MediaAsset(id=media.id)
        session.add(asset)
    asset.mount_id = media.mount_id
    asset.path_key = media.path_key
    asset.file_kind = "video"
    asset.size_bytes = media.size_bytes or 0
    asset.mtime_ms = media.mtime_ms or 0
    asset.duration_ms = media.duration_ms or 0
    asset.mime_type = media.mime_type
    asset.probe_json = media.probe_json or {}
    asset.playable = media.playable
    asset.missing = media.missing
    asset.has_poster = media.has_poster
    return asset


def _link_asset(session: Session, entity_id: str, asset_id: str) -> None:
    row = (
        session.query(EntityAsset)
        .filter(EntityAsset.entity_id == entity_id, EntityAsset.asset_id == asset_id)
        .first()
    )
    if row is None:
        session.add(EntityAsset(
            id=new_id(), entity_id=entity_id, asset_id=asset_id,
            role="PRIMARY_VIDEO", sequence=1))


# ---------- entity 树 ----------

def _entity_apply_v02_fields(entity: ContentEntity, media: Media | None,
                             *, title: str | None = None) -> None:
    """把 v0.2 行的结构字段镜像到 entity（同值幂等；不改 Canonical 高优先级值）。

    阶段 1 只写缺省值：已有非空 content_class/年龄/语言视为更高来源，不覆盖。
    """
    if media is not None:
        if not entity.language:
            entity.language = media.language
        a_min, a_max = parse_age_band(media.age_band)
        if entity.age_min is None and a_min is not None:
            entity.age_min = a_min
        if entity.age_max is None and a_max is not None:
            entity.age_max = a_max
        if not entity.duration_ms:
            entity.duration_ms = media.duration_ms or 0
    if title and not entity.title:
        entity.title = title


def series_entities_by_series(session: Session, series_rows: list[Series]) -> dict[str, ContentEntity]:
    """series 行 → 系列实体（2026-08-26 稳定关联，替代 title 字符串匹配）。

    结构锚点：episode entity(source_media_id) → season → series root（改系列名
    不断链、TMDB 海报/匹配保留）；无结构锚点的 legacy 行回退 title 匹配。
    """
    if not series_rows:
        return {}
    from ..models import Episode as EpisodeRow

    series_ids = [s.id for s in series_rows]
    ep_rows = (
        session.query(EpisodeRow.media_id, EpisodeRow.series_id)
        .filter(EpisodeRow.series_id.in_(series_ids))
        .all()
    )
    media_ids = [r.media_id for r in ep_rows]
    media_to_series = {r.media_id: r.series_id for r in ep_rows}

    out: dict[str, ContentEntity] = {}
    if media_ids:
        season_ids = [
            r[0] for r in (
                session.query(ContentEntity.parent_id)
                .filter(ContentEntity.entity_type == "episode",
                        ContentEntity.source_media_id.in_(media_ids))
                .distinct().all())
        ]
        if season_ids:
            season_parent = {
                r[0]: r[1] for r in (
                    session.query(ContentEntity.id, ContentEntity.parent_id)
                    .filter(ContentEntity.id.in_(season_ids)).all())
            }
            ep_parent = {
                r[0]: r[1] for r in (
                    session.query(ContentEntity.source_media_id, ContentEntity.parent_id)
                    .filter(ContentEntity.entity_type == "episode",
                            ContentEntity.source_media_id.in_(media_ids)).all())
            }
            root_ids = {season_parent.get(p) for p in ep_parent.values()
                        if season_parent.get(p)}
            roots = {
                e.id: e for e in (
                    session.query(ContentEntity)
                    .filter(ContentEntity.entity_type == "series",
                            ContentEntity.id.in_(root_ids)).all())
            } if root_ids else {}
            for media_id, season_id in ep_parent.items():
                sid = media_to_series.get(media_id)
                root = roots.get(season_parent.get(season_id, ""))
                if sid and root is not None and sid not in out:
                    out[sid] = root

    # legacy 回退：无结构锚点的系列按 title（保持旧行为）
    missing = [s for s in series_rows if s.id not in out]
    if missing:
        title_map = {
            e.title: e for e in (
                session.query(ContentEntity)
                .filter(ContentEntity.entity_type == "series",
                        ContentEntity.title.in_([s.title for s in missing]))
                .order_by(ContentEntity.created_at).all())
        }
        for s in missing:
            ent = title_map.get(s.title)
            if ent is not None:
                out[s.id] = ent
    return out


def get_or_create_series_entity(session: Session, series_id: str, title: str,
                                language: str | None = None) -> ContentEntity:
    """v0.2 series 行 → series entity（幂等）。

    优先结构锚点（本系列任一集的 episode entity 祖先根——改名后复用既有实体，
    保留 TMDB 匹配与海报）；无锚点时回退 title 匹配（legacy），再无则新建。
    """
    from ..models import Series as SeriesRow

    series_row = session.get(SeriesRow, series_id)
    if series_row is not None:
        anchored = series_entities_by_series(session, [series_row]).get(series_id)
        if anchored is not None:
            if language and anchored.language is None:
                anchored.language = language
            return anchored
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "series", ContentEntity.title == title)
        .order_by(ContentEntity.created_at)
        .first()
    )
    if ent is None:
        ent = ContentEntity(
            id=new_id(), entity_type="series", title=title, language=language,
            modality="VIDEO", duration_ms=0, sequence_no=1, ordering="STANDARD")
        session.add(ent)
        session.flush()
    return ent


def get_or_create_season_entity(session: Session, series_entity_id: str,
                                season_no: int) -> ContentEntity:
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "season",
                ContentEntity.parent_id == series_entity_id,
                ContentEntity.sequence_no == season_no)
        .first()
    )
    if ent is None:
        ent = ContentEntity(
            id=new_id(), entity_type="season", parent_id=series_entity_id,
            title=f"第 {season_no} 季", sequence_no=season_no,
            duration_ms=0, modality="VIDEO")
        session.add(ent)
        session.flush()
    return ent


def _sync_episode(session: Session, media: Media, episode: Episode,
                  series: Series) -> ContentEntity:
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "episode",
                ContentEntity.source_media_id == media.id)
        .first()
    )
    if ent is None:
        series_ent = get_or_create_series_entity(session, series.id, series.title)
        season_ent = get_or_create_season_entity(session, series_ent.id,
                                                 episode.season_no or 1)
        ent = ContentEntity(
            id=new_id(), entity_type="episode", parent_id=season_ent.id,
            source_media_id=media.id,
            title=episode.title or media.title,
            content_class=default_content_class("episode"),
            modality="VIDEO", sequence_no=episode.episode_no or 1)
        session.add(ent)
        session.flush()
    else:
        ent.sequence_no = episode.episode_no or ent.sequence_no
    _entity_apply_v02_fields(ent, media, title=episode.title or media.title)
    return ent


def _sync_lesson(session: Session, media: Media, lesson: Lesson,
                 course: Course) -> ContentEntity:
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "lesson",
                ContentEntity.source_media_id == media.id)
        .first()
    )
    course_ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "course", ContentEntity.title == course.title)
        .order_by(ContentEntity.created_at)
        .first()
    )
    if course_ent is None:
        course_ent = ContentEntity(
            id=new_id(), entity_type="course", title=course.title,
            language=course.language, content_class="LEARNING", modality="VIDEO",
            duration_ms=0, sequence_no=1)
        session.add(course_ent)
        session.flush()
    if ent is None:
        ent = ContentEntity(
            id=new_id(), entity_type="lesson", parent_id=course_ent.id,
            source_media_id=media.id, title=lesson.title or media.title,
            content_class="LEARNING", modality="VIDEO",
            sequence_no=lesson.lesson_no or 1)
        session.add(ent)
        session.flush()
    else:
        ent.sequence_no = lesson.lesson_no or ent.sequence_no
    _entity_apply_v02_fields(ent, media, title=lesson.title or media.title)
    return ent


def _sync_movie(session: Session, media: Media) -> ContentEntity:
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type == "movie",
                ContentEntity.source_media_id == media.id)
        .first()
    )
    if ent is None:
        ent = ContentEntity(
            id=new_id(), entity_type="movie", source_media_id=media.id,
            title=media.title, content_class=default_content_class("movie"),
            modality="VIDEO", sequence_no=1)
        session.add(ent)
        session.flush()
    _entity_apply_v02_fields(ent, media)
    return ent


def series_poster_file(session: Session, data_dir: str, media_id: str):
    """媒体所属系列的实体海报文件路径（2026-08-27 海报来源一致的 URL 级落实）。

    集级无自有海报时回退系列实体海报（MED-013：系列海报一律实体 poster
    优先，集级仅作回退）——系列墙/详情页 2026-08-25 已按此取图，本次把
    /media/{id}/poster 端点补齐同一语义，集网格等所有消费方统一受益。
    无系列/无实体图返回 None（调用方继续走默认图兜底）。
    has_poster 语义不变：仍=自有真实海报，刮削照常跳过有真图的。
    """
    from pathlib import Path

    from ..models import ArtworkAsset, Series
    from ..models import Episode as EpisodeRow

    ep = session.query(EpisodeRow).filter(EpisodeRow.media_id == media_id).one_or_none()
    if ep is None:
        return None
    series = session.get(Series, ep.series_id)
    if series is None:
        return None
    sent = series_entities_by_series(session, [series]).get(series.id)
    if sent is None:
        return None
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == sent.id, ArtworkAsset.kind == "poster")
           .one_or_none())
    if row is None:
        return None
    path = Path(data_dir) / row.file_path
    return path if path.is_file() else None


# ---------- tags → topic/character ----------

def _get_or_create_topic(session: Session, name: str) -> ContentTopic:
    t = session.query(ContentTopic).filter(ContentTopic.name == name).first()
    if t is None:
        t = ContentTopic(id=new_id(), name=name)
        session.add(t)
        session.flush()
    return t


def _get_or_create_character(session: Session, name: str) -> ContentCharacter:
    c = session.query(ContentCharacter).filter(ContentCharacter.name == name).first()
    if c is None:
        c = ContentCharacter(id=new_id(), name=name)
        session.add(c)
        session.flush()
    return c


def sync_tags(session: Session, entity_id: str, tags: dict) -> None:
    tags = tags or {}
    existing_topics = {
        t.name for t in (
            session.query(ContentTopic)
            .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
            .filter(EntityTopic.entity_id == entity_id)
            .all())
    }
    for name in (tags.get("themes") or []) + (tags.get("tags") or []):
        if name not in existing_topics:
            session.add(EntityTopic(
                entity_id=entity_id, topic_id=_get_or_create_topic(session, str(name)).id))
    existing_chars = {
        c.name for c in (
            session.query(ContentCharacter)
            .join(EntityCharacter, EntityCharacter.character_id == ContentCharacter.id)
            .filter(EntityCharacter.entity_id == entity_id)
            .all())
    }
    for name in tags.get("characters") or []:
        if name not in existing_chars:
            session.add(EntityCharacter(
                entity_id=entity_id,
                character_id=_get_or_create_character(session, str(name)).id))


# ---------- 入口：media 行变更后调用 ----------

def try_relocate_moved_media(session: Session, old: Media, new: Media) -> bool:
    """目录改名/文件移动的内容指纹迁移（决策二验收：历史不丢）。

    由扫描尾部调用（此时 old.path_key 已确认不在本次遍历中）：
    把 entity 锚点、entity_asset、episode/lesson 绑定、watch_history 主键
    从 old 迁到 new，删除 old 行。指纹 = size+mtime+文件名。
    """
    from ..models import Episode, Lesson, WatchHistory

    if (old.size_bytes != new.size_bytes or old.mtime_ms != new.mtime_ms
            or old.path_key.rsplit("/", 1)[-1] != new.path_key.rsplit("/", 1)[-1]):
        return False
    ent = (
        session.query(ContentEntity)
        .filter(ContentEntity.source_media_id == old.id)
        .first()
    )
    if ent is not None:
        ent.source_media_id = new.id
        for link in (
            session.query(EntityAsset)
            .filter(EntityAsset.entity_id == ent.id, EntityAsset.asset_id == old.id)
            .all()
        ):
            link.asset_id = new.id
    # 绑定迁移：目标 media 已有新绑定行时删除旧行（新行即正确绑定），否则迁移
    if session.query(Episode).filter(Episode.media_id == new.id).first() is None:
        for ep in session.query(Episode).filter(Episode.media_id == old.id).all():
            ep.media_id = new.id
    else:
        for ep in session.query(Episode).filter(Episode.media_id == old.id).all():
            session.delete(ep)
    new_lesson = session.query(Lesson).filter(Lesson.media_id == new.id).first()
    old_lessons = session.query(Lesson).filter(Lesson.media_id == old.id).all()
    if new_lesson is None:
        for lsn in old_lessons:
            lsn.media_id = new.id
    else:
        # 课程进度迁到新 lesson 行后删除旧行（course_progress.lesson_id FK）
        from ..models import CourseProgress

        for lsn in old_lessons:
            for prog in (session.query(CourseProgress)
                         .filter(CourseProgress.lesson_id == lsn.id).all()):
                prog.lesson_id = new_lesson.id
            session.delete(lsn)
    for wh in session.query(WatchHistory).filter(WatchHistory.media_id == old.id).all():
        wh.media_id = new.id  # 断点/完成度随 entity 语义保留
    # 旧 media 的字幕轨随旧行删除（外置轨重扫已在新路径重建；内嵌轨随新 probe 重建）
    from ..models import SubtitleSegment, SubtitleTrack

    for track in (session.query(SubtitleTrack)
                  .filter(SubtitleTrack.media_id == old.id).all()):
        session.query(SubtitleSegment).filter(
            SubtitleSegment.track_id == track.id).delete()
        session.delete(track)
    old_asset = session.get(MediaAsset, old.id)
    if old_asset is not None:
        session.delete(old_asset)
    session.delete(old)
    session.flush()
    logger.info("文件移动迁移：%s → %s（历史保留）", old.path_key, new.path_key)
    return True


def sync_media_entity(session: Session, media: Media) -> ContentEntity | None:
    """scanner / admin 修改 media 行后的统一同步点。返回内容实体（散文件无则 None）。"""
    asset = sync_asset(session, media)
    episode = session.query(Episode).filter(Episode.media_id == media.id).first()
    entity: ContentEntity | None = None
    if episode is not None:
        from ..models import Series

        series = session.get(Series, episode.series_id)
        if series is not None:
            entity = _sync_episode(session, media, episode, series)
    if entity is None:
        lesson = session.query(Lesson).filter(Lesson.media_id == media.id).first()
        if lesson is not None:
            course = session.get(Course, lesson.course_id)
            if course is not None:
                entity = _sync_lesson(session, media, lesson, course)
    if entity is None and media.media_type == "movie":
        entity = _sync_movie(session, media)
    if entity is None and media.media_type in ("story", "song"):
        entity = (
            session.query(ContentEntity)
            .filter(ContentEntity.entity_type == media.media_type,
                    ContentEntity.source_media_id == media.id)
            .first())
        if entity is None:
            entity = ContentEntity(
                id=new_id(), entity_type=media.media_type, source_media_id=media.id,
                title=media.title, content_class=(
                    "STORY" if media.media_type == "story" else "MUSIC"),
                modality="AUDIO", sequence_no=1)
            session.add(entity)
            session.flush()
        _entity_apply_v02_fields(entity, media, title=media.title)
    if entity is not None:
        _link_asset(session, entity.id, asset.id)
        sync_tags(session, entity.id, media.tags_json or {})
    return entity

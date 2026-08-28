"""观看历史 / 课程进度 / 统计（PRD ANA-001~003、CRS-003，技术方案 §9.6）。

只描述可观察行为；不产生心理/能力/医学推断（ANA-005 红线）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import Config
from ..models import (
    Course,
    CourseProgress,
    Episode,
    Lesson,
    Media,
    Playback,
    Series,
    ViewingInterval,
    WatchHistory,
)


class HistoryService:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        try:
            self._tz: tzinfo = ZoneInfo(cfg.timezone)
        except Exception:
            self._tz = UTC

    @property
    def tz(self) -> tzinfo:
        """家庭时区（统计范围按本地日界，公开只读）。"""
        return self._tz

    # ---------- 断点 / 完成度（§9.6，阈值可配置） ----------

    def should_save_breakpoint(self, position_ms: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        remaining = max(0, duration_ms - position_ms)
        return (
            position_ms >= self._cfg.breakpoint_min_position_ms
            and remaining >= self._cfg.breakpoint_min_remaining_ms
        )

    def is_completed(self, position_ms: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        if position_ms >= duration_ms * self._cfg.completion_ratio:
            return True
        return duration_ms - position_ms <= self._cfg.completion_tail_ms

    def is_course_lesson_completed(self, position_ms: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        return position_ms >= duration_ms * self._cfg.course_completion_ratio

    def update_on_playback_change(
        self,
        session: Session,
        profile_id: str,
        media: Media,
        position_ms: int,
        add_watched_ms: int,
        *,
        ended: bool,
        entity_id: str | None = None,
    ) -> None:
        h = session.get(WatchHistory, (profile_id, media.id))
        if h is None:
            h = WatchHistory(
                profile_id=profile_id, media_id=media.id,
                last_position_ms=0, watched_seconds=0, completed=False,
            )
            session.add(h)
        # v0.3：断点/完成度挂 entity（目录改名不丢历史，决策二）
        if entity_id is not None and not h.entity_id:
            h.entity_id = entity_id
        h.watched_seconds = (h.watched_seconds or 0) + add_watched_ms // 1000
        h.last_watched_at = datetime.now(UTC)
        lesson = session.query(Lesson).filter(Lesson.media_id == media.id).one_or_none()
        if lesson is not None:
            p = session.get(CourseProgress, (profile_id, lesson.id))
            if p is None:
                p = CourseProgress(
                    profile_id=profile_id, lesson_id=lesson.id, course_id=lesson.course_id,
                    position_ms=0, completed=False,
                )
                session.add(p)
            p.position_ms = max(p.position_ms or 0, position_ms)
            p.updated_at = datetime.now(UTC)
            if ended or self.is_course_lesson_completed(position_ms, media.duration_ms):
                p.completed = True
            return
        if self.should_save_breakpoint(position_ms, media.duration_ms):
            h.last_position_ms = position_ms
        elif position_ms >= self._cfg.breakpoint_min_position_ms:
            h.last_position_ms = position_ms
        if ended or self.is_completed(position_ms, media.duration_ms):
            h.completed = True

    # ---------- 首页数据 ----------

    def continue_watching(self, session: Session, profile_id: str, limit: int = 6) -> list[dict]:
        rows = (
            session.query(WatchHistory, Media)
            .join(Media, Media.id == WatchHistory.media_id)
            .filter(
                WatchHistory.profile_id == profile_id,
                WatchHistory.completed.is_(False),
                Media.missing.is_(False),
                WatchHistory.last_position_ms > 0,
            )
            .order_by(WatchHistory.last_watched_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "media_id": m.id,
                "title": m.title,
                "media_type": m.media_type,
                "duration_ms": m.duration_ms,
                "last_position_ms": h.last_position_ms,
                "image_hint": (m.tags_json or {}).get("themes", [])[:1],
            }
            for h, m in rows
        ]

    def continue_learning(self, session: Session, profile_id: str, limit: int = 4) -> list[dict]:
        rows = (
            session.query(CourseProgress, Lesson, Media, Course)
            .join(Lesson, Lesson.id == CourseProgress.lesson_id)
            .join(Media, Media.id == Lesson.media_id)
            .join(Course, Course.id == Lesson.course_id)
            .filter(
                CourseProgress.profile_id == profile_id,
                CourseProgress.completed.is_(False),
                Media.missing.is_(False),
            )
            .order_by(CourseProgress.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "course_id": c.id,
                "course_title": c.title,
                "lesson_no": lesson.lesson_no,
                "media_id": m.id,
                "title": lesson.title or m.title,
                "position_ms": p.position_ms,
            }
            for p, lesson, m, c in rows
        ]

    def recent_series(self, session: Session, profile_id: str, days: int = 14, limit: int = 8) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = (
            session.query(func.count(Playback.id), Series, Episode)
            .join(Media, Media.id == Playback.media_id)
            .join(Episode, Episode.media_id == Media.id)
            .join(Series, Series.id == Episode.series_id)
            .filter(Playback.profile_id == profile_id, Playback.created_at >= since,
                    Media.missing.is_(False))  # 源端已删除的媒体不再出现在最近常看
            .group_by(Series.id)
            .order_by(func.count(Playback.id).desc())
            .limit(limit)
            .all()
        )
        # 封面：系列实体海报（TMDB，优先）> 系列内首个有海报的集 > 第一集
        # （网络源无集级海报时首页仍有图——2026-08-25 与系列墙同源）
        # 2026-08-26：系列↔实体改结构锚点关联（改名不断链）
        from ..media.content_catalog import series_entities_by_series
        from ..models import ArtworkAsset

        ent_by_sid = series_entities_by_series(session, [s for _n, s, _e in rows])
        artwork_sids = (
            {r[0] for r in (
                session.query(ArtworkAsset.entity_id)
                .filter(ArtworkAsset.kind == "poster",
                        ArtworkAsset.entity_id.in_([e.id for e in ent_by_sid.values()]))
                .all())}
            if ent_by_sid else set()
        )

        result = []
        for _n, s, _e in rows:
            eps = (
                session.query(Media)
                .join(Episode, Episode.media_id == Media.id)
                .filter(Episode.series_id == s.id, Media.missing.is_(False))
                .order_by(Episode.season_no, Episode.episode_no)
                .all()
            )
            cover = next((m for m in eps if m.has_poster), eps[0] if eps else None)
            series_ent = ent_by_sid.get(s.id)
            entity_id = series_ent.id if series_ent else None
            has_entity_poster = entity_id in artwork_sids if series_ent else False
            result.append({
                "series_id": s.id, "title": s.title, "language": s.language,
                "count": len(eps),
                "cover_media_id": cover.id if cover else None,
                "cover_has_poster": bool(cover and cover.has_poster),
                "entity_id": entity_id,
                "entity_poster": has_entity_poster,
            })
        return result

    # ---------- 统计（Admin Analytics） ----------

    def _period_bounds(self, period: str, now: datetime) -> tuple[datetime, datetime, int]:
        local = now.astimezone(self._tz)
        if period == "week":
            start = (local - timedelta(days=local.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return start.astimezone(UTC), now, 7
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(UTC), now, 1

    def analytics(self, session: Session, profile_id: str, period: str, now: datetime,
                  custom: tuple[datetime, datetime] | None = None) -> dict:
        """period=day|week|custom；custom 时由调用方给定 [start, end]（含端点）。"""
        if custom is not None:
            start, end = custom
        else:
            start, end, _days = self._period_bounds(period, now)
        base = (
            session.query(
                Playback.media_id,
                func.sum(ViewingInterval.duration_ms).label("watched_ms"),
                Media.title,
                Media.media_type,
                Media.language,
            )
            .join(ViewingInterval, ViewingInterval.playback_id == Playback.id)
            .join(Media, Media.id == Playback.media_id)
            .filter(
                Playback.profile_id == profile_id,
                ViewingInterval.started_at >= start,
                ViewingInterval.started_at <= end,
            )
            .group_by(Playback.media_id, Media.title, Media.media_type, Media.language)
            .all()
        )
        total_ms = sum(r.watched_ms or 0 for r in base)
        by_type: dict[str, int] = {}
        by_language: dict[str, int] = {}
        items: list[dict] = []
        for r in base:
            watched = r.watched_ms or 0
            by_type[r.media_type] = by_type.get(r.media_type, 0) + watched
            by_language[r.language or "unknown"] = by_language.get(r.language or "unknown", 0) + watched
            items.append({
                "media_id": r.media_id, "title": r.title,
                "media_type": r.media_type, "watched_seconds": watched // 1000,
            })
        items.sort(key=lambda x: -x["watched_seconds"])

        # 常看系列/主题（行为描述）
        series_rows = (
            session.query(func.sum(ViewingInterval.duration_ms).label("ms"), Series.title)
            .join(Playback, Playback.id == ViewingInterval.playback_id)
            .join(Media, Media.id == Playback.media_id)
            .join(Episode, Episode.media_id == Media.id)
            .join(Series, Series.id == Episode.series_id)
            .filter(
                Playback.profile_id == profile_id,
                ViewingInterval.started_at >= start,
                ViewingInterval.started_at <= end,
            )
            .group_by(Series.title)
            .order_by(func.sum(ViewingInterval.duration_ms).desc())
            .limit(5)
            .all()
        )
        theme_rows = (
            session.query(func.sum(ViewingInterval.duration_ms).label("ms"))
            .join(Playback, Playback.id == ViewingInterval.playback_id)
            .join(Media, Media.id == Playback.media_id)
            .filter(
                Playback.profile_id == profile_id,
                ViewingInterval.started_at >= start,
                ViewingInterval.started_at <= end,
            )
            .all()
        )
        # v0.3 正交维度（ANA-002 按媒介/分类）：经 source_media_id 挂实体维度
        from ..models import ContentEntity

        ent_dims = {
            e.source_media_id: (e.modality, e.content_class)
            for e in (
                session.query(ContentEntity)
                .filter(ContentEntity.source_media_id.in_(
                    [r.media_id for r in base])).all())
            if e.source_media_id
        }
        by_modality: dict[str, int] = {}
        by_class: dict[str, int] = {}
        for r in base:
            mod, cc = ent_dims.get(r.media_id, (None, None))
            by_modality[mod or "unknown"] = by_modality.get(mod or "unknown", 0) + (r.watched_ms or 0)
            by_class[cc or "unknown"] = by_class.get(cc or "unknown", 0) + (r.watched_ms or 0)

        # 观看记录明细（C-2 收口）：最近 20 条播放会话（起止/时长/完成度）
        recent = (
            session.query(Playback, Media)
            .join(Media, Media.id == Playback.media_id)
            .filter(Playback.profile_id == profile_id,
                    Playback.started_at >= start,
                    Playback.started_at <= end)
            .order_by(Playback.started_at.desc())
            .limit(20)
            .all()
        )
        recent_records = []
        for pb, m in recent:
            watched_ms = (
                session.query(func.coalesce(func.sum(ViewingInterval.duration_ms), 0))
                .filter(ViewingInterval.playback_id == pb.id).scalar() or 0)
            mod, cc = ent_dims.get(m.id, (None, None))
            recent_records.append({
                "title": m.title, "media_type": m.media_type,
                "modality": mod, "content_class": cc,
                "started_at": pb.started_at.isoformat(),
                "watched_seconds": watched_ms // 1000,
                "completed": bool(watched_ms >= (m.duration_ms or 1) * 0.9
                                  and m.duration_ms > 0),
            })
        return {
            "period": period,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_watched_seconds": total_ms // 1000,
            "by_media_type": {k: v // 1000 for k, v in by_type.items()},
            "by_language": {k: v // 1000 for k, v in by_language.items()},
            "by_modality": {k: v // 1000 for k, v in by_modality.items()},
            "by_content_class": {k: v // 1000 for k, v in by_class.items()},
            "top_media": items[:10],
            "top_series": [{"title": r.title, "watched_seconds": (r.ms or 0) // 1000} for r in series_rows],
            "recent_records": recent_records,
            "theme_activity_available": bool(theme_rows),
        }

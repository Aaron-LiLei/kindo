"""按来源清除入库资源（2026-08-25 产品决策：删除来源=该来源入库资源一并删除）。

停用只断开连接（资源保留）；删除则级联清除该 storage_mount_id 下的：
  媒体/资产/实体树（共享祖先仅在空巢时剪除）/观看与播放域/字幕/课程进度/
  海报与探测缓存/实体 artwork 与身份匹配记录；scan_job 一并清理。

保留：文件本身（只删库与缓存）、兴趣信号（客观行为历史）、认证/策略/设备。
执行前自动备份数据库到 data/backups/（可回滚）。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger("kindo.purge")


def backup_database(data_dir: Path) -> Path | None:
    """SQLite 在线备份（backup API，WAL 安全）→ data/backups/purge-<ts>.db。"""
    db_path = data_dir / "kindo.db"
    if not db_path.is_file():
        return None
    import sqlite3

    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest_path = backup_dir / f"purge-backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest_path


def _delete_files(paths) -> int:
    n = 0
    for p in paths:
        try:
            p.unlink(missing_ok=True)
            n += 1
        except OSError:
            pass
    return n


def purge_mount_media(session: Session, data_dir: Path, storage_mount_id: str) -> dict:
    """清除一个 storage_mount_id 下的全部入库资源。调用方负责 commit 与备份。"""
    from ..models import (
        ArtworkAsset,
        ContentEntity,
        Course,
        CourseProgress,
        EntityAsset,
        EntityCharacter,
        EntityTopic,
        Episode,
        ExternalIdentity,
        Lesson,
        MatchDecision,
        Media,
        MediaAsset,
        Playback,
        PlaybackEvent,
        PlaybackGrant,
        ScanJob,
        Series,
        SubtitleSegment,
        SubtitleTrack,
        ViewingInterval,
        WatchHistory,
    )

    media_ids = [r[0] for r in session.query(Media.id)
                 .filter(Media.mount_id == storage_mount_id).all()]
    counts: dict[str, int] = {"media": len(media_ids)}
    if not media_ids:
        return counts

    # ---- 播放域（playback → grant/interval/event）----
    playback_ids = [r[0] for r in session.query(Playback.id)
                    .filter(Playback.media_id.in_(media_ids)).all()]
    if playback_ids:
        counts["playback"] = (
            session.query(PlaybackEvent)
            .filter(PlaybackEvent.playback_id.in_(playback_ids))
            .delete(synchronize_session=False)
            + session.query(ViewingInterval)
            .filter(ViewingInterval.playback_id.in_(playback_ids))
            .delete(synchronize_session=False)
            + session.query(PlaybackGrant)
            .filter(PlaybackGrant.playback_id.in_(playback_ids))
            .delete(synchronize_session=False)
            + session.query(Playback)
            .filter(Playback.id.in_(playback_ids))
            .delete(synchronize_session=False)
        )

    # ---- 观看/课程进度/字幕 ----
    counts["watch_history"] = (
        session.query(WatchHistory)
        .filter(WatchHistory.media_id.in_(media_ids))
        .delete(synchronize_session=False))
    lesson_ids = [r[0] for r in session.query(Lesson.id)
                  .filter(Lesson.media_id.in_(media_ids)).all()]
    if lesson_ids:
        counts["course_progress"] = (
            session.query(CourseProgress)
            .filter(CourseProgress.lesson_id.in_(lesson_ids))
            .delete(synchronize_session=False))
    track_ids = [r[0] for r in session.query(SubtitleTrack.id)
                 .filter(SubtitleTrack.media_id.in_(media_ids)).all()]
    if track_ids:
        session.query(SubtitleSegment).filter(
            SubtitleSegment.track_id.in_(track_ids)).delete(synchronize_session=False)

    # ---- v0.3 实体树：叶子（source_media_id）→ 空巢祖先剪除 ----
    leaf_ids = [r[0] for r in session.query(ContentEntity.id)
                .filter(ContentEntity.source_media_id.in_(media_ids)).all()]
    doomed = set(leaf_ids)
    if leaf_ids:
        # 自底向上：无子且非文件挂靠的结构实体（season/series/course）逐轮剪除。
        # 共享祖先（多挂载同系列）在有其他子代时自然存活。
        while True:
            structural = (session.query(ContentEntity)
                          .filter(ContentEntity.entity_type.in_(("season", "series", "course")))
                          .all())
            newly = []
            for e in structural:
                if e.id in doomed:
                    continue
                has_child = (session.query(ContentEntity.id)
                             .filter(ContentEntity.parent_id == e.id,
                                     ContentEntity.id.notin_(doomed))
                             .first() is not None)
                if not has_child:
                    newly.append(e)
            if not newly:
                break
            doomed.update(e.id for e in newly)
        # 实体附属
        session.query(EntityAsset).filter(
            EntityAsset.entity_id.in_(doomed)).delete(synchronize_session=False)
        session.query(EntityTopic).filter(
            EntityTopic.entity_id.in_(doomed)).delete(synchronize_session=False)
        session.query(EntityCharacter).filter(
            EntityCharacter.entity_id.in_(doomed)).delete(synchronize_session=False)
        session.query(ExternalIdentity).filter(
            ExternalIdentity.entity_id.in_(doomed)).delete(synchronize_session=False)
        session.query(MatchDecision).filter(
            MatchDecision.entity_id.in_(doomed)).delete(synchronize_session=False)
        artwork_rows = session.query(ArtworkAsset).filter(
            ArtworkAsset.entity_id.in_(doomed)).all()
        counts["artwork_files"] = _delete_files(
            data_dir / r.file_path for r in artwork_rows if r.file_path)
        session.query(ArtworkAsset).filter(
            ArtworkAsset.entity_id.in_(doomed)).delete(synchronize_session=False)
        session.query(ContentEntity).filter(
            ContentEntity.id.in_(doomed)).delete(synchronize_session=False)
        counts["entities"] = len(doomed)

    # ---- v0.2 组织表 + media/asset ----
    session.query(SubtitleTrack).filter(
        SubtitleTrack.media_id.in_(media_ids)).delete(synchronize_session=False)
    series_ids = {r[0] for r in session.query(Episode.series_id)
                  .filter(Episode.media_id.in_(media_ids)).all()}
    course_ids = {r[0] for r in session.query(Lesson.course_id)
                  .filter(Lesson.media_id.in_(media_ids)).all()}
    session.query(Episode).filter(
        Episode.media_id.in_(media_ids)).delete(synchronize_session=False)
    session.query(Lesson).filter(
        Lesson.media_id.in_(media_ids)).delete(synchronize_session=False)
    if series_ids:
        empty_series = [sid for sid in series_ids
                        if session.query(Episode.id)
                        .filter(Episode.series_id == sid).first() is None]
        if empty_series:
            session.query(Series).filter(
                Series.id.in_(empty_series)).delete(synchronize_session=False)
    if course_ids:
        empty_courses = [cid for cid in course_ids
                         if session.query(Lesson.id)
                         .filter(Lesson.course_id == cid).first() is None]
        if empty_courses:
            session.query(Course).filter(
                Course.id.in_(empty_courses)).delete(synchronize_session=False)
    session.query(Media).filter(
        Media.id.in_(media_ids)).delete(synchronize_session=False)
    counts["media_assets"] = (
        session.query(MediaAsset)
        .filter(MediaAsset.mount_id == storage_mount_id)
        .delete(synchronize_session=False))

    # ---- 扫描任务与缓存文件 ----
    counts["scan_jobs"] = (
        session.query(ScanJob)
        .filter(ScanJob.mount_id == storage_mount_id)
        .delete(synchronize_session=False))
    poster_dir = data_dir / "cache" / "posters"
    probe_dir = data_dir / "cache" / "probe"
    id_set = set(media_ids)
    cache_files: list[Path] = []
    for d in (poster_dir, probe_dir):
        if d.is_dir():
            cache_files.extend(p for p in d.iterdir() if p.stem in id_set)
    counts["cache_files"] = _delete_files(cache_files)

    logger.info("来源资源清除 mount=%s counts=%s", storage_mount_id, counts)
    return counts

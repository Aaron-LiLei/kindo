"""backend hardening: unique series/course + query indexes (2026-08-19 后端加固)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19

1. series.title / course.title 唯一约束：并发扫描不再可能插入同名行
   （历史脏数据先按 title 去重合并再建约束）。
2. 高频查询路径补索引：episode.series_id、lesson.course_id、playback
   device/media/created_at、playback_grant(playback_id,revoked_at)、
   viewing_interval(playback_id/started_at)、playback_event.playback_id、
   media(media_type,missing)。
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _dedupe(bind, table: str, child_table: str, child_fk: str) -> None:
    """同名 title 保留最早行（rowid 最小），子表改指幸存行后删除重复行。"""
    dupes = bind.execute(sa.text(
        f"SELECT title FROM {table} GROUP BY title HAVING COUNT(*) > 1"  # noqa: S608
    )).fetchall()
    for (title,) in dupes:
        rows = bind.execute(sa.text(
            f"SELECT id FROM {table} WHERE title = :t ORDER BY rowid"  # noqa: S608
        ), {"t": title}).fetchall()
        keeper = rows[0][0]
        for (dup_id,) in rows[1:]:
            bind.execute(sa.text(
                f"UPDATE {child_table} SET {child_fk} = :k WHERE {child_fk} = :d"  # noqa: S608
            ), {"k": keeper, "d": dup_id})
            bind.execute(sa.text(
                f"DELETE FROM {table} WHERE id = :d"  # noqa: S608
            ), {"d": dup_id})


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)

    # ---- 唯一约束（去重后创建）----
    if not any(c.get("name") == "uq_series_title"
               for c in insp.get_unique_constraints("series")):
        _dedupe(bind, "series", "episode", "series_id")
        with op.batch_alter_table("series") as batch:
            batch.create_unique_constraint("uq_series_title", ["title"])
    if not any(c.get("name") == "uq_course_title"
               for c in insp.get_unique_constraints("course")):
        _dedupe(bind, "course", "lesson", "course_id")
        with op.batch_alter_table("course") as batch:
            batch.create_unique_constraint("uq_course_title", ["title"])

    # ---- 查询索引（存在即跳过）----
    def has_index(table: str, name: str) -> bool:
        return any(ix.get("name") == name for ix in insp.get_indexes(table))

    indexes: list[tuple[str, str, list[str]]] = [
        ("episode", "ix_episode_series_id", ["series_id"]),
        ("lesson", "ix_lesson_course_id", ["course_id"]),
        ("playback", "ix_playback_device_id", ["device_id"]),
        ("playback", "ix_playback_media_id", ["media_id"]),
        ("playback", "ix_playback_created_at", ["created_at"]),
        ("playback_grant", "ix_grant_playback_revoked", ["playback_id", "revoked_at"]),
        ("viewing_interval", "ix_viewing_interval_playback_id", ["playback_id"]),
        ("viewing_interval", "ix_viewing_interval_started_at", ["started_at"]),
        ("playback_event", "ix_playback_event_playback_id", ["playback_id"]),
        ("media", "ix_media_type_missing", ["media_type", "missing"]),
    ]
    for table, name, cols in indexes:
        if not has_index(table, name):
            op.create_index(name, table, cols)


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)

    for table, name in [
        ("media", "ix_media_type_missing"),
        ("playback_event", "ix_playback_event_playback_id"),
        ("viewing_interval", "ix_viewing_interval_started_at"),
        ("viewing_interval", "ix_viewing_interval_playback_id"),
        ("playback_grant", "ix_grant_playback_revoked"),
        ("playback", "ix_playback_created_at"),
        ("playback", "ix_playback_media_id"),
        ("playback", "ix_playback_device_id"),
        ("lesson", "ix_lesson_course_id"),
        ("episode", "ix_episode_series_id"),
    ]:
        if any(ix.get("name") == name for ix in insp.get_indexes(table)):
            op.drop_index(name, table_name=table)

    if any(c.get("name") == "uq_series_title"
           for c in insp.get_unique_constraints("series")):
        with op.batch_alter_table("series") as batch:
            batch.drop_constraint("uq_series_title", type_="unique")
    if any(c.get("name") == "uq_course_title"
           for c in insp.get_unique_constraints("course")):
        with op.batch_alter_table("course") as batch:
            batch.drop_constraint("uq_course_title", type_="unique")

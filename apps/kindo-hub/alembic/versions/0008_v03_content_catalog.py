"""v0.3 统一内容目录：新表 + 既有表增列（产品基线 v0.3 决策二，技术方案 §7.6 步骤 1）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-24

建 v0.3 新表（content_entity / media_asset / entity_asset / external_identity /
match_decision / artwork_asset / content_topic / content_character / entity_topic /
entity_character / transition_session / transition_activity / interest_signal）；
playback / viewing_interval / watch_history / course_progress 增列（可空，向后兼容）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_M = sa.MetaData()

_NEW_TABLES = [
    ("content_entity", sa.Table(
        "content_entity", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("content_entity.id"),
                  nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("content_class", sa.String(16), nullable=True),
        sa.Column("modality", sa.String(16), nullable=True),
        sa.Column("age_min", sa.Integer(), nullable=True),
        sa.Column("age_max", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("release_date", sa.String(10), nullable=True),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repeatable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ordering", sa.String(32), nullable=True),
        sa.Column("meta_provenance_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("match_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("candidates_json", sa.JSON(), nullable=True),
        sa.Column("source_media_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Index("ix_content_entity_parent_id", "parent_id"),
        sa.Index("ix_entity_type_class_modality", "entity_type", "content_class", "modality"),
        sa.Index("ix_entity_class_age", "content_class", "age_min", "age_max"),
        sa.Index("ix_content_entity_source_media_id", "source_media_id"),
    )),
    ("media_asset", sa.Table(
        "media_asset", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mount_id", sa.String(64), nullable=False),
        sa.Column("path_key", sa.String(1024), nullable=False),
        sa.Column("file_kind", sa.String(8), nullable=False, server_default="video"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mtime_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.String(64), nullable=True),
        sa.Column("probe_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("playable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("missing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_poster", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("mount_id", "path_key", name="uq_asset_mount_path"),
    )),
    ("entity_asset", sa.Table(
        "entity_asset", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36),
                  sa.ForeignKey("content_entity.id"), nullable=False),
        sa.Column("asset_id", sa.String(36),
                  sa.ForeignKey("media_asset.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="PRIMARY_VIDEO"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("entity_id", "asset_id", "role", name="uq_entity_asset_role"),
        sa.Index("ix_entity_asset_entity_id", "entity_id"),
        sa.Index("ix_entity_asset_asset_id", "asset_id"),
    )),
    ("external_identity", sa.Table(
        "external_identity", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36),
                  sa.ForeignKey("content_entity.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("matched_title", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("entity_id", "provider", name="uq_identity_entity_provider"),
        sa.Index("ix_identity_provider_ref", "provider", "ref_id"),
        sa.Index("ix_external_identity_entity_id", "entity_id"),
    )),
    ("match_decision", sa.Table(
        "match_decision", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("candidate_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Index("ix_match_decision_entity_id", "entity_id"),
    )),
    ("artwork_asset", sa.Table(
        "artwork_asset", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36),
                  sa.ForeignKey("content_entity.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("entity_id", "kind", name="uq_artwork_entity_kind"),
        sa.Index("ix_artwork_asset_entity_id", "entity_id"),
    )),
    ("content_topic", sa.Table(
        "content_topic", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("content_topic.id"),
                  nullable=True),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("name", name="uq_content_topic_name"),
    )),
    ("content_character", sa.Table(
        "content_character", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("name", name="uq_content_character_name"),
    )),
    ("entity_topic", sa.Table(
        "entity_topic", _M,
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("content_entity.id"),
                  primary_key=True),
        sa.Column("topic_id", sa.String(36), sa.ForeignKey("content_topic.id"),
                  primary_key=True),
    )),
    ("entity_character", sa.Table(
        "entity_character", _M,
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("content_entity.id"),
                  primary_key=True),
        sa.Column("character_id", sa.String(36), sa.ForeignKey("content_character.id"),
                  primary_key=True),
    )),
    ("transition_session", sa.Table(
        "transition_session", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_session_id", sa.String(64), nullable=True),
        sa.Column("parent_session_id", sa.String(64), nullable=True),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("trigger_key", sa.String(256), nullable=False),
        sa.Column("trigger_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("state", sa.String(16), nullable=False, server_default="offer"),
        sa.Column("selected_type", sa.String(24), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ended_reason", sa.String(32), nullable=True),
        sa.Column("ai_voice_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trigger_key", name="uq_transition_trigger_key"),
        sa.Index("ix_transition_profile_time", "profile_id", "created_at"),
    )),
    ("transition_activity", sa.Table(
        "transition_activity", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("topics_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("age_min", sa.Integer(), nullable=True),
        sa.Column("age_max", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )),
    ("interest_signal", sa.Table(
        "interest_signal", _M,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("topic_id", sa.String(36), nullable=True),
        sa.Column("signal_type", sa.String(24), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Index("ix_signal_profile_type_time", "profile_id", "signal_type", "created_at"),
        sa.Index("ix_interest_signal_entity_id", "entity_id"),
        sa.Index("ix_interest_signal_topic_id", "topic_id"),
    )),
]


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    existing = set(insp.get_table_names())

    for name, table in _NEW_TABLES:
        if name not in existing:
            table.create(bind)

    # 既有表增列（全部可空：v0.2 代码继续工作，搬迁在 0009 填值）
    _add_cols(insp, "playback", {
        "entity_id": sa.Column("entity_id", sa.String(36), nullable=True),
        "asset_id": sa.Column("asset_id", sa.String(36), nullable=True),
        "content_class": sa.Column("content_class", sa.String(16), nullable=True),
        "modality": sa.Column("modality", sa.String(16), nullable=True),
    })
    _add_cols(insp, "content_entity", {
        "overview": sa.Column("overview", sa.Text(), nullable=True),
        "release_date": sa.Column("release_date", sa.String(10), nullable=True),
    }) if "content_entity" in existing and "overview" not in {
        c["name"] for c in insp.get_columns("content_entity")} else None
    _add_cols(insp, "viewing_interval", {
        "content_class": sa.Column("content_class", sa.String(16), nullable=True),
        "modality": sa.Column("modality", sa.String(16), nullable=True),
    })
    _add_cols(insp, "watch_history", {
        "entity_id": sa.Column("entity_id", sa.String(36), nullable=True),
    })
    _add_cols(insp, "course_progress", {
        "lesson_entity_id": sa.Column("lesson_entity_id", sa.String(36), nullable=True),
    })


def _add_cols(insp, table: str, cols: dict) -> None:
    existing = {c["name"] for c in insp.get_columns(table)}
    for name, col in cols.items():
        if name not in existing:
            with op.batch_alter_table(table) as batch:
                batch.add_column(col)


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    existing = set(insp.get_table_names())
    for name, _table in reversed(_NEW_TABLES):
        if name in existing:
            op.drop_table(name)
    # 增列不回删（数据兼容优先；完整回退走 pre_migration_backup）

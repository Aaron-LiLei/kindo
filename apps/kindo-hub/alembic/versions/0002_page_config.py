"""page config: media mounts + llm providers (PRD v0.2.2 ADM-010/011)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

from kindo.models import AwareDateTime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    if "media_mount" in existing and "llm_provider" in existing:
        return
    op.create_table(
        "media_mount",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("root_id", sa.String(64), nullable=False),
        sa.Column("sub_path", sa.String(1024), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(16), nullable=False, server_default="page"),
        sa.Column("created_at", AwareDateTime(), nullable=True),
        sa.Column("deleted_at", AwareDateTime(), nullable=True),
    )
    op.create_table(
        "llm_provider",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False, server_default="openai_chat_completions"),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("api_key", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", AwareDateTime(), nullable=True),
        sa.Column("updated_at", AwareDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("llm_provider")
    op.drop_table("media_mount")

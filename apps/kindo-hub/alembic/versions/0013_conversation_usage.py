"""conversation_usage：常规 AI 语音对话计量（2026-08-26 ai_voice 预算闭环）。

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False, unique=True),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_conversation_usage_profile", "conversation_usage", ["profile_id"])
    op.create_index("ix_conversation_usage_started", "conversation_usage", ["started_at"])


def downgrade() -> None:
    op.drop_table("conversation_usage")

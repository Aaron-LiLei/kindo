"""ai_proposal / ai_job：家长 AI 助手（技术方案 §19，实施计划 S1）。

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_proposal",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("proposal_type", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("impact_level", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("source_context_hash", sa.String(64), nullable=True),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_proposal_status", "ai_proposal", ["status"])
    op.create_index("ix_ai_proposal_status_impact", "ai_proposal", ["status", "impact_level"])
    op.create_index("ix_ai_proposal_profile_type", "ai_proposal", ["profile", "proposal_type"])
    op.create_index("ix_ai_proposal_dedupe", "ai_proposal", ["dedupe_key"])
    op.create_index("ix_ai_proposal_job", "ai_proposal", ["job_id"])

    op.create_table(
        "ai_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(24), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_job_type", "ai_job", ["job_type"])


def downgrade() -> None:
    op.drop_table("ai_job")
    op.drop_table("ai_proposal")

"""scan_dir_state：目录 mtime 增量剪枝状态（2026-08-25 扫描优化 D）。

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_dir_state",
        sa.Column("mount_id", sa.String(64), primary_key=True),
        # 空串行 = last_full_scan_ms 标记（该挂载最近一次全量扫描时间）
        sa.Column("dir_path", sa.String(1024), primary_key=True),
        sa.Column("mtime_ms", sa.Integer, nullable=False),
    )
    op.create_index("ix_scan_dir_mount", "scan_dir_state", ["mount_id"])


def downgrade() -> None:
    op.drop_table("scan_dir_state")

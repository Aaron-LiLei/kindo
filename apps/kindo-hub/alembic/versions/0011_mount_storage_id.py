"""mount storage_id：配置根收养后的存储 id 覆盖（2026-08-25 全页面化决策）。

Revision ID: 0011_mount_storage_id
Revises: 0010_v03_policy_seed
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 收养的配置根行：id 为新 uuid，storage_id 保持原根 id（如 "family"），
    # 使既有媒体行 (mount_id=根id) 无缝衔接
    op.add_column("media_mount",
                  sa.Column("storage_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("media_mount", "storage_id")

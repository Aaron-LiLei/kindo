"""llm_provider.enabled：Provider 停用开关（2026-08-26 P2 小改进）。

停用 = 不参与 configured_count / active_model / 新会话解析（TV ai_available
随之变化），密钥与配置保留——区别于删除（丢 key 不可逆）。

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_provider", sa.Column("enabled", sa.Boolean(), nullable=False,
                                            server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("llm_provider", "enabled")

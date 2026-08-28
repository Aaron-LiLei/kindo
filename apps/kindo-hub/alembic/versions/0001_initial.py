"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-18

按冻结快照 kindo._schema_frozen_0001.Base 创建全部持久化实体（技术方案 §7.1）。
快照与 live kindo.models 解耦：模型演进只走增量迁移，新装库不再随运行时模型漂移。
"""
from alembic import op

from kindo._schema_frozen_0001 import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())

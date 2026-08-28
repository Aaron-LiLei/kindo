"""media scraped_json: 在线刮削结果标记（2026-08-21 PRD 修订：轻量 TMDB 海报刮削）

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-21

media.scraped_json：刮削命中记录（来源/外部引用/命中标题/海报地址/时间）。
非空表示该条目已刮削，重跑任务时跳过（force 可覆盖）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "scraped_json" not in cols:
        with op.batch_alter_table("media") as batch:
            batch.add_column(sa.Column("scraped_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "scraped_json" in cols:
        with op.batch_alter_table("media") as batch:
            batch.drop_column("scraped_json")

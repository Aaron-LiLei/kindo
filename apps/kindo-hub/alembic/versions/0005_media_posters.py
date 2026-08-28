"""media posters: 扫描期缩略海报落地（2026-08-20 媒体库展示重构）

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-20

media.has_poster 布尔列：海报文件固定存放 /data/cache/posters/{media_id}.jpg
（技术方案 §13.2 已规划的缓存目录），行内只记录就绪状态，避免列表页逐条探测文件。
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "has_poster" not in cols:
        with op.batch_alter_table("media") as batch:
            batch.add_column(
                sa.Column("has_poster", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "has_poster" in cols:
        with op.batch_alter_table("media") as batch:
            batch.drop_column("has_poster")

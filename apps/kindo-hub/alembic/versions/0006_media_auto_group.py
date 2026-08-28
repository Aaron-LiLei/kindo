"""media auto_series_key: 自动归组生命周期标记（2026-08-20 库内容治理）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-20

media.auto_series_key：自动归组（按目录结构推断）写入的合集键。非空表示
Episode 绑定由自动归组建立，可随目录结构调整重算或解除；sidecar/家长修正
声明的归组该列为 NULL，不受自动重算影响。
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "auto_series_key" not in cols:
        with op.batch_alter_table("media") as batch:
            batch.add_column(sa.Column("auto_series_key", sa.String(1024), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("media")}
    if "auto_series_key" in cols:
        with op.batch_alter_table("media") as batch:
            batch.drop_column("auto_series_key")

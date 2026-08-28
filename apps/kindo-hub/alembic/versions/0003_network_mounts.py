"""network mounts: smb/webdav support (PRD v0.2.3 MED-003 P0)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 用 Base.metadata.create_all 建表（反映当前模型），列可能已存在；逐列守卫
    from sqlalchemy import inspect

    bind = op.get_bind()
    existing_cols = {c["name"] for c in inspect(bind).get_columns("media_mount")}
    with op.batch_alter_table("media_mount") as batch:
        if "mount_type" not in existing_cols:
            batch.add_column(sa.Column("mount_type", sa.String(16), nullable=False,
                                       server_default="local"))
        if "config_json" not in existing_cols:
            batch.add_column(sa.Column("config_json", sa.JSON(), nullable=True))
        if "secret_json" not in existing_cols:
            batch.add_column(sa.Column("secret_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_mount") as batch:
        batch.drop_column("secret_json")
        batch.drop_column("config_json")
        batch.drop_column("mount_type")

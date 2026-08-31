"""content_entity 新增 story_text 列（2026-08-31 家长声音讲故事，第一步 A）。

sidecar kindo.yaml 可为 story 实体声明 story_text（朗读文本）：
- 仅 sidecar 来源，扫描写入；重扫覆盖（文本以 sidecar 为准）；
- 非可信内容数据：只作为 read_story Tool 的朗读素材在服务端直接分句播报
  （家长声音克隆优先、系统 TTS 兜底），不进入 LLM 上下文。

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_entity",
                  sa.Column("story_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_entity", "story_text")

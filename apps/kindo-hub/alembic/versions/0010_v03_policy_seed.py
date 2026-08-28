"""v0.3 Policy 规则升维快照 + 预置活动库 seed（技术方案 §7.6 步骤 3）

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-24

- 最新 policy_config.rules_json 若为 v1 结构（无 budgets 字段），追加 v2 三层预算
  映射（daily_limit_minutes→screen_total_minutes 等）；v1 字段保留（旧引擎兼容）。
- transition_activity 预置通用离屏活动（status=preset，决策七 7.3）。
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_PRESET_ACTIVITIES = [
    ("小小海洋学家", "在家找一个圆圆的东西当贝壳，问问爸爸妈妈：海龟为什么要背着重重的壳呀？",
     ["海洋", "动物"], 3, 6),
    ("工程车工地", "用积木或者纸盒搭一个工地，让玩具车把小球从一个地方运到另一个地方。",
     ["工程", "搭建"], 3, 7),
    ("数字寻宝", "在客厅里找出 3 个圆形的东西、2 个方形的东西，数一数一共有几个。",
     ["数字"], 3, 6),
    ("英语小侦探", "在家里找 3 样会英文的东西（比如 cup、door、bed），大声说出它的英文名字。",
     ["英语"], 4, 8),
    ("动物模仿秀", "学一学刚才动画里小动物走路的样子，让爸爸妈妈猜猜你演的是谁。",
     ["动物", "表演"], 3, 7),
    ("小小观察家", "在窗边看一看外面的天空和树，说说今天有没有云、风大不大。",
     ["自然", "观察"], 3, 8),
    ("绘本时间", "请爸爸妈妈读一本你喜欢的故事书，听完讲一讲你最喜欢哪一页。",
     ["阅读"], 3, 8),
    ("身体小挑战", "单脚站 10 秒，再换一只脚；试试能不能像小青蛙一样跳 3 下。",
     ["运动"], 3, 7),
]


def upgrade() -> None:
    conn = op.get_bind()

    # ---------- Policy v1 → v2 升维（幂等：已有 budgets 则跳过） ----------
    row = conn.execute(sa.text(
        "SELECT version, rules_json FROM policy_config ORDER BY version DESC LIMIT 1"
    )).mappings().first()
    if row is not None:
        rules = row["rules_json"]
        if isinstance(rules, str):
            rules = json.loads(rules)
        if isinstance(rules, dict) and "budgets" not in rules:
            daily = rules.get("daily_limit_minutes")
            rules["budgets"] = {
                "screen_total_minutes": daily,
                "video_by_class": {"ENTERTAINMENT": daily, "LEARNING": daily},
                "audio_minutes": None,
                "ai_voice_minutes": None,
            }
            rules.setdefault("offscreen", {"allowed": True, "offer_enabled": True})
            rules.setdefault("transition_policy", {
                "enabled": True,
                "types": ["knowledge", "quiz", "roleplay", "vocabulary",
                          "song_story", "offscreen_game", "real_explore"],
                "max_minutes": 4,
                "daily_offer_limit": 3,
            })
            conn.execute(sa.text(
                "UPDATE policy_config SET rules_json = :r WHERE version = :v"),
                {"r": json.dumps(rules, ensure_ascii=False), "v": row["version"]})

    # ---------- 预置活动库（幂等：按标题去重） ----------
    for title, summary, topics, amin, amax in _PRESET_ACTIVITIES:
        exists = conn.execute(sa.text(
            "SELECT 1 FROM transition_activity WHERE title = :t"), {"t": title}).scalar()
        if exists:
            continue
        conn.execute(sa.text(
            "INSERT INTO transition_activity (id, title, summary, topics_json,"
            " age_min, age_max, source, status, created_by, created_at)"
            " VALUES (:id, :t, :s, :topics, :amin, :amax, 'builtin', 'preset',"
            " 'system', '2026-08-24T00:00:00')"), {
            "id": str(uuid.uuid4()), "t": title, "s": summary,
            "topics": json.dumps(topics, ensure_ascii=False),
            "amin": amin, "amax": amax,
        })


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM transition_activity WHERE source = 'builtin'"))

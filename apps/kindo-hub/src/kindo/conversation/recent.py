"""最近讨论对象（T-20260902-003-01 / PRD AI-011~013）。

短期 Conversation Session 是内存权威，结束/过期即释放；但「刚才那个」
「换一个」等指代在会话中断后仍需落点。本模块在会话收尾时把该会话最后
讨论的媒体/故事提炼为一条客观引用（media_id / 标题 / 时间）写入
app_setting，供新会话的 Context Assembler 按需恢复。

红线：只存引用与时间，不存儿童语音原文、不存对话、不做推断（硬性约束 14、
AI-013 不以保存完整聊天为前提）。read_story 的 speak_text 原文永不入库。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger("kindo.conversation.recent")

KEY_PREFIX = "recent_discussed_"
_MAX_TITLE_CHARS = 200


def discussed_key(profile_id: str) -> str:
    return KEY_PREFIX + profile_id


def capture_from_session(db_session_factory, conv) -> None:
    """会话收尾钩子（end 与过期 sweep 共用）：失败不影响收尾主流程。"""
    try:
        ref = _extract(conv)
        if ref is None:
            return
        from ..models import AppSetting

        with db_session_factory() as session:
            session.merge(AppSetting(
                key=discussed_key(conv.profile_id),
                value_json={**ref, "at": datetime.now(UTC).isoformat()},
            ))
            session.commit()
    except Exception:
        logger.exception("最近讨论对象记录失败（不影响会话收尾）")


def load_discussed(db, profile_id: str) -> dict | None:
    """读取最近讨论对象（原样返回引用；时效过滤由调用方做）。"""
    from ..models import AppSetting

    row = db.get(AppSetting, discussed_key(profile_id))
    if row is None:
        return None
    v = row.value_json or {}
    return v if (v.get("media_id") or v.get("title")) else None


def _extract(conv) -> dict | None:
    """从会话最近工具结果倒序提取最后讨论对象（最后一个有引用的结果优先）。

    只读通用字段 media_id / title / label / candidates / direct_speak，
    不复制任何文本正文（story 原文、字幕、语音转写均不进入存储）。
    """
    for r in reversed(conv.recent_tool_results):
        d = r.get("data") or {}
        if not isinstance(d, dict):
            continue
        mid = d.get("media_id")
        title = d.get("title") or d.get("label")
        if mid:
            return {
                "media_id": str(mid),
                "title": str(title)[:_MAX_TITLE_CHARS] if title else "",
            }
        for c in d.get("candidates") or []:
            if isinstance(c, dict) and c.get("media_id"):
                label = c.get("title") or c.get("label") or ""
                return {
                    "media_id": str(c["media_id"]),
                    "title": str(label)[:_MAX_TITLE_CHARS],
                }
        if d.get("history"):
            for item in d["history"]:
                if isinstance(item, dict) and item.get("media_id"):
                    return {
                        "media_id": str(item["media_id"]),
                        "title": str(item.get("title") or "")[:_MAX_TITLE_CHARS],
                    }
        if d.get("direct_speak") and d.get("title"):
            # read_story：故事是实体引用（无 media_id），只留标题
            return {"title": str(d["title"])[:_MAX_TITLE_CHARS]}
    return None

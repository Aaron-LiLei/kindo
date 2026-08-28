"""常规对话的 ai_voice 计量（2026-08-26 工程治理：补齐 §9.2 开放项）。

口径：会话开始 → 最后一次互动（last_activity_at）的墙钟时长，空闲段不计；
与 transition_session.ai_voice_ms 一同构成 ai_voice 预算当日消耗。
创建即落行（crash-safe），显式结束/空闲清扫时更新，重启收尾孤儿行。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..models import ConversationUsage
from .service import ConversationSession

logger = logging.getLogger("kindo.conversation.usage")


def engaged_ms(s: ConversationSession) -> int:
    """created_at → last_activity_at（负值防护：新会话 touch 后为正）。"""
    return max(0, int((s.last_activity_at - s.created_at).total_seconds() * 1000))


class ConversationUsageService:
    def __init__(self, session_factory) -> None:
        self._db = session_factory

    def record_start(self, s: ConversationSession) -> None:
        try:
            with self._db() as session:
                session.add(ConversationUsage(
                    session_id=s.session_id, profile_id=s.profile_id,
                    device_id=s.device_id, started_at=s.created_at))
                session.commit()
        except Exception:
            logger.exception("conversation_usage 起始记录失败（不影响会话）")

    def record_end(self, s: ConversationSession) -> None:
        try:
            with self._db() as session:
                row = session.query(ConversationUsage).filter(
                    ConversationUsage.session_id == s.session_id).one_or_none()
                if row is None or row.ended_at is not None:
                    return
                row.ended_at = datetime.now(UTC)
                row.duration_ms = engaged_ms(s)
                session.commit()
        except Exception:
            logger.exception("conversation_usage 结束记录失败（不影响会话）")

    def finalize_orphans(self, idle_cap_seconds: int) -> int:
        """重启收尾：未闭合行按 min(now-started_at, 空闲上限) 估算时长。"""
        now = datetime.now(UTC)
        with self._db() as session:
            rows = session.query(ConversationUsage).filter(
                ConversationUsage.ended_at.is_(None)).all()
            for row in rows:
                row.ended_at = now
                elapsed = (now - row.started_at).total_seconds()
                row.duration_ms = int(max(0, min(elapsed, idle_cap_seconds)) * 1000)
            if rows:
                session.commit()
            return len(rows)

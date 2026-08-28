"""Conversation Protocol（技术方案 §6）。

Session/Turn/Utterance/CandidateSet 为 Hub 内存权威（§7.2）：
空闲超时（默认 600s）、Hub 重启失效；不把完整聊天永久化作为系统前提。
Provider/model 在 Session 创建时固定；同一会话不做跨 Provider failover；
无副作用时同 Provider 瞬时错误可安全重试 1 次（§6.4）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import Config
from ..errors import not_found
from ..util import new_id

logger = logging.getLogger("kindo.conversation")

MAX_TURNS_KEPT = 8
MAX_TOOL_RESULTS_KEPT = 3
MAX_LLM_TOOL_ROUNDS = 4
STATE_ACTIVE = "active"
STATE_ENDED = "ended"
STATE_EXPIRED = "expired"


@dataclass
class Turn:
    turn_no: int
    user_input: str
    assistant_output: str = ""
    tool_calls: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ConversationSession:
    session_id: str
    device_id: str
    profile_id: str
    provider_id: str
    model_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state: str = STATE_ACTIVE
    turns: list[Turn] = field(default_factory=list)
    candidates: dict = field(default_factory=dict)  # option_id -> candidate
    tool_call_cache: dict = field(default_factory=dict)  # session:tool_call_id -> result
    recent_tool_results: list = field(default_factory=list)
    current_playback_id: str | None = None
    current_topic: str | None = None
    follow_up_deadline: float | None = None
    tts_to_session: dict = field(default_factory=dict)  # tts_id -> session_id（TTS 生命周期映射）

    def touch(self) -> None:
        self.last_activity_at = datetime.now(UTC)

    def register_candidates(self, options: list[dict], source_tool: str) -> None:
        self.candidates = {o["option_id"]: {**o, "source_tool": source_tool} for o in options}

    @property
    def idle_seconds(self) -> float:
        return (datetime.now(UTC) - self.last_activity_at).total_seconds()


class ConversationManager:
    """会话注册表为 Hub 内存权威（§7.2）。

    方法会被主事件循环（WS）、FastAPI 线程池（REST）与 orchestrator 工作线程
    并发调用，因此用 threading.Lock 保护注册表本身；单个 Session 内部的
    Turn 串行化由 Orchestrator 的 per-session 锁负责。
    """

    def __init__(self, cfg: Config, usage=None):
        self._cfg = cfg
        # 可选计量钩子（ConversationUsageService；ai_voice 预算闭环，2026-08-26）
        self._usage = usage
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = threading.Lock()

    def create(self, device_id: str, profile_id: str, provider_id: str,
               model_id: str, resume_session_id: str | None) -> ConversationSession:
        with self._lock:
            if resume_session_id:
                s = self._sessions.get(resume_session_id)
                # resume 只允许原设备恢复自己的会话（跨设备劫持防护）
                if (s is not None and s.state == STATE_ACTIVE
                        and s.device_id == device_id):
                    s.touch()
                    return s
            s = ConversationSession(
                session_id=new_id(), device_id=device_id, profile_id=profile_id,
                provider_id=provider_id, model_id=model_id,
            )
            self._sessions[s.session_id] = s
        if self._usage is not None:
            self._usage.record_start(s)  # 创建即落行（crash-safe 计量）
        return s

    def get(self, session_id: str) -> ConversationSession:
        s = self._sessions.get(session_id)
        if s is None or s.state != STATE_ACTIVE:
            raise not_found("Conversation Session 不存在或已失效")
        return s

    def get_for_device(self, session_id: str, device_id: str) -> ConversationSession:
        """读取会话并校验设备归属（不匹配视同不存在，防越权读取）。"""
        s = self.get(session_id)
        if s.device_id != device_id:
            raise not_found("Conversation Session 不存在或已失效")
        return s

    def get_optional(self, session_id: str) -> ConversationSession | None:
        s = self._sessions.get(session_id)
        return s if s is not None and s.state == STATE_ACTIVE else None

    def end(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None:
                s.state = STATE_ENDED
        if s is not None and self._usage is not None:
            self._usage.record_end(s)

    def all_sessions(self) -> list[ConversationSession]:
        with self._lock:
            return list(self._sessions.values())

    def sweep_idle(self) -> int:
        """约 session_idle_seconds 空闲即失效（PoC 基线 600s）。"""
        expired: list[str] = []
        ended: list[ConversationSession] = []
        with self._lock:
            for sid, s in self._sessions.items():
                if s.state == STATE_ACTIVE and s.idle_seconds > self._cfg.session_idle_seconds:
                    expired.append(sid)
            for sid in expired:
                self._sessions[sid].state = STATE_EXPIRED
                ended.append(self._sessions[sid])
                # 结束后释放内存（不持久化完整对话，§7.2）
            self._sessions = {
                sid: s for sid, s in self._sessions.items()
                if not (sid in expired or s.state in (STATE_ENDED, STATE_EXPIRED))
            }
        if self._usage is not None:
            for s in ended:
                self._usage.record_end(s)
        return len(expired)

    def drop_all(self) -> None:
        """Hub 重启后内存态清空由进程本身保证；此方法仅供测试。"""
        self._sessions.clear()

    def snapshot(self, s: ConversationSession) -> dict:
        return {
            "session_id": s.session_id,
            "state": s.state,
            "provider_id": s.provider_id,
            "model_id": s.model_id,
            "idle_timeout_seconds": self._cfg.session_idle_seconds,
            "follow_up_seconds": self._cfg.follow_up_seconds,
            "turns": [
                {
                    "turn_no": t.turn_no,
                    "user_input": t.user_input,
                    "assistant_output": t.assistant_output,
                    "tool_calls": [c.get("name") for c in t.tool_calls if isinstance(c, dict)],
                }
                for t in s.turns[-MAX_TURNS_KEPT:]
            ],
            "created_at": s.created_at.isoformat(),
            "last_activity_at": s.last_activity_at.isoformat(),
        }

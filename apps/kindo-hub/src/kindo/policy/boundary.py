"""Policy Boundary Event（v0.3 决策六，阶段 3b）。

三源统一发布：①播放中 threshold_reached（Meter 检测）②playback.ended 且配额
已耗尽（自然播完，无 play 请求也触发）③play 请求被软限制 deny。

幂等：trigger_key = profile_id + policy_day + limit_type + boundary_id，以
transition_session(trigger_key) 唯一约束兜底——TV 重连、重复 progress 事件、
重复 deny 请求均不得产生重复 offer。时段硬截止与内容范围拒绝不产生事件。
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import TransitionSession
from ..util import new_id

logger = logging.getLogger("kindo.policy.boundary")

SOFT_LIMIT_REASONS = {"daily_limit_reached", "session_limit_reached",
                      "episode_limit_reached"}


class BoundaryEventPublisher:
    """边界事件发布器：落库幂等 + 进程内队列（Transition Orchestrator 订阅）。"""

    def __init__(self, tz) -> None:
        self._tz = tz
        self._queue: deque[dict] = deque()
        self._lock = threading.Lock()

    # ---------- 发布 ----------

    def publish(self, session: Session, profile_id: str, limit_type: str,
                boundary_id: str, payload: dict | None = None) -> bool:
        """发布边界事件；同 trigger_key 已存在返回 False（幂等）。"""
        now = datetime.now(UTC)
        day = now.astimezone(self._tz).strftime("%Y-%m-%d")
        key = f"{profile_id}:{day}:{limit_type}:{boundary_id}"
        try:
            session.add(TransitionSession(
                id=new_id(), profile_id=profile_id, trigger_key=key,
                trigger_json=payload or {"limit_type": limit_type},
                state="offer", created_at=now,
            ))
            session.flush()
        except IntegrityError:
            session.rollback()
            return False
        with self._lock:
            self._queue.append({
                "trigger_key": key, "profile_id": profile_id,
                "limit_type": limit_type, "boundary_id": boundary_id,
                "payload": payload or {}, "ts": now.isoformat(),
            })
        logger.info("Policy Boundary Event: %s", key)
        return True

    def publish_soft_deny(self, session: Session, profile_id: str,
                          reason_code: str, boundary_id: str,
                          payload: dict | None = None) -> bool:
        """deny 源：仅软限制 reason 触发（决策六非触发清单之外的兜底过滤）。"""
        if reason_code not in SOFT_LIMIT_REASONS:
            return False
        return self.publish(session, profile_id, reason_code, boundary_id, payload)

    # ---------- 订阅 ----------

    def drain(self) -> list[dict]:
        """取出待处理事件（Transition Orchestrator 消费）。"""
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out


def check_threshold_reached(session, policy_engine, rules, profile_id: str,
                            now: datetime, playback) -> list[tuple[str, str]]:
    """Meter 源：active playback 是否使某 VIDEO 预算恰好耗尽。

    返回 [(limit_type, boundary_id)] 列表（去重由 trigger_key 兜底）。
    """
    from ..models import Media

    media = session.get(Media, playback.media_id)
    if media is None:
        return []
    cc, mod = policy_engine.entity_dims(session, media)
    if mod != "VIDEO":
        return []
    out: list[tuple[str, str]] = []
    screen_total = rules.screen_total_minutes()
    if screen_total is not None:
        used = policy_engine.consumed_ms_today(session, profile_id, now,
                                               modality="VIDEO")
        if used >= screen_total * 60_000:
            out.append(("daily_limit_reached", f"{playback.id}:screen_total"))
    class_budget = rules.video_class_minutes(cc)
    if class_budget is not None and not (
            media.media_type == "lesson"
            and not rules.course_counts_as_entertainment):
        used_c = policy_engine.consumed_ms_today(session, profile_id, now,
                                                 modality="VIDEO", content_class=cc)
        if used_c >= class_budget * 60_000:
            out.append(("daily_limit_reached", f"{playback.id}:class_{cc}"))
    return out


def check_quota_exhausted(session, policy_engine, rules, profile_id: str,
                          now: datetime, playback) -> list[tuple[str, str]]:
    """ended 源：自然播完且 VIDEO 预算已耗尽（最常见触发场景，决策六）。"""
    return check_threshold_reached(session, policy_engine, rules, profile_id,
                                   now, playback)

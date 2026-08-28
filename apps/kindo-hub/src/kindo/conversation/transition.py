"""Growth Transition Orchestrator（v0.3 决策七，阶段 4a）。

订阅 Policy Boundary Event（唯一触发源）→ 频控与启用检查 → 生成 offer
（≤3 选项，模板开场承接刚播内容）→ Realtime transition.* 事件 →
选择（interaction/audio/offscreen）→ 时间盒收尾 → 结束路由与兴趣信号。

红线（硬性约束 11）：拒绝即止不反复说服；时间盒硬上限；当日频控。
单一状态源：交互状态属 Conversation，TransitionSession 只存业务事实。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..config import Config
from ..models import (
    ContentEntity,
    ContentTopic,
    EntityTopic,
    InterestSignal,
    Media,
    TransitionActivity,
    TransitionSession,
)
from ..policy.engine import PolicyEngine
from ..util import new_id

logger = logging.getLogger("kindo.transition")

# 选项类型 → 儿童端标签与开场承接语（交互细节由 TV 端渲染）
TYPE_LABELS = {
    "knowledge": "聊聊刚才的故事",
    "quiz": "答个小问题",
    "roleplay": "演一演小剧场",
    "vocabulary": "学几个新单词",
    "song_story": "听个相关的故事",
    "offscreen_game": "玩个不看屏幕的小游戏",
    "real_explore": "去发现身边的东西",
}


class TransitionOrchestrator:
    def __init__(self, cfg: Config, db_session_factory, policy: PolicyEngine,
                 boundary, notifier, playback, provider_resolver) -> None:
        self._cfg = cfg
        self._db = db_session_factory
        self._policy = policy
        self._boundary = boundary
        self._notifier = notifier  # realtime.emit
        self._playback = playback
        self._provider_resolver = provider_resolver  # 判断 LLM 可用性

    # ---------- 主循环 tick（app 后台循环驱动，每 15s） ----------

    def tick(self) -> None:
        self._drain_events()
        self._enforce_deadlines()

    # ---------- 事件消费 ----------

    def _drain_events(self) -> None:
        for ev in self._boundary.drain():
            try:
                self._offer(ev)
            except Exception:
                logger.exception("Transition offer 失败 trigger=%s", ev.get("trigger_key"))

    def _offer(self, ev: dict) -> bool:
        profile_id = ev["profile_id"]
        device_id = ev.get("payload", {}).get("device_id")
        with self._db() as session:
            rules, _v = self._policy.current(session)
            # 启用与频控（硬性约束 11；trigger_key 已保证单边界一次）
            if not rules.transition_enabled():
                return False
            now = datetime.now(UTC)
            day_start, _ = self._policy._local_day_bounds(now)
            # 频控只数"实际发起过"的 offer（started_at 非空）；
            # Boundary 幂等插入但未发起的行不计（如 LLM 不可用放弃的）
            offered = (
                session.query(TransitionSession)
                .filter(TransitionSession.profile_id == profile_id,
                        TransitionSession.created_at >= day_start,
                        TransitionSession.started_at.isnot(None))
                .count()
            )
            if offered >= rules.transition_daily_offer_limit():
                return False
            # LLM 不可用 → 静默放弃（决策七：接力是增强层）
            provider_id, _model = self._active_provider(session)
            if provider_id is None:
                return False
            # 定位 trigger 对应的 transition 行（Boundary 发布时已插入）
            ts = (session.query(TransitionSession)
                  .filter(TransitionSession.trigger_key == ev["trigger_key"]).one())
            if ts.state != "offer" or ts.accepted or ts.rejected:
                return False
            # device 解析：Boundary payload 无 device 时取最近 active/最近设备
            if device_id is None:
                from ..models import Device

                dev = session.query(Device).order_by(Device.last_seen_at.desc()).first()
                device_id = dev.id if dev else None
            if device_id is None:
                return False
            ts.started_at = now
            ts.deadline = now + timedelta(minutes=rules.transition_max_minutes(self._cfg))
            media_title = (ev.get("payload") or {}).get("title") or "刚才的内容"
            options = self._build_options(session, rules, ev)
            opening = (
                f"今天的动画时间看完啦。刚才的《{media_title}》是不是很有意思？"
                "要不要一起做点别的？"
            )
            session.commit()
            transition_id = ts.id
            deadline_iso = ts.deadline.isoformat()
        self._notify(device_id, "transition.offer", {
            "transition_id": transition_id,
            "opening_text": opening,
            "options": options,
            "deadline_ts": deadline_iso,
            "max_minutes": rules.transition_max_minutes(self._cfg),
        })
        return True

    def _build_options(self, session: Session, rules, ev: dict) -> list[dict]:
        """≤3 个选项：优先与刚播内容主题相关（有音频内容→song_story；
        活动库有匹配→离屏类；其余 knowledge/quiz）。附"不聊了"由 TV 端常驻。"""
        payload = ev.get("payload") or {}
        media_id = payload.get("media_id")
        topics: list[str] = []
        if media_id:
            rows = (
                session.query(ContentTopic.name)
                .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
                .filter(EntityTopic.entity_id == (
                    session.query(ContentEntity.id)
                    .filter(ContentEntity.source_media_id == media_id)
                    .limit(1)))
                .all()
            )
            topics = [r[0] for r in rows]
        allowed = rules.transition_types()
        out: list[dict] = []
        # 相关音频内容 → song_story 优先
        if "song_story" in allowed and self._find_audio(session, topics):
            out.append({"type": "song_story", "topics": topics})
        # 活动库匹配 → 离屏
        activity = None
        if {"offscreen_game", "real_explore"} & set(allowed):
            activity = self._find_activity(session, topics)
        if activity is not None:
            out.append({"type": "real_explore", "topics": topics,
                        "activity_id": activity.id})
        for t in ("knowledge", "quiz", "roleplay", "vocabulary"):
            if t in allowed and len(out) < 3:
                out.append({"type": t, "topics": topics})
            if len(out) >= 3:
                break
        for o in out:
            o["label"] = TYPE_LABELS.get(o["type"], o["type"])
            if o["topics"]:
                o["label"] = f"{o['label']}（{o['topics'][0]}）"
        return out[:3]

    def _find_audio(self, session: Session, topics: list[str]):
        q = (session.query(ContentEntity)
             .filter(ContentEntity.modality == "AUDIO",
                     ContentEntity.title.isnot(None))
             .limit(5))
        if topics:
            matched = (
                session.query(ContentEntity)
                .join(EntityTopic, EntityTopic.entity_id == ContentEntity.id)
                .join(ContentTopic, ContentTopic.id == EntityTopic.topic_id)
                .filter(ContentEntity.modality == "AUDIO",
                        ContentTopic.name.in_(topics))
                .limit(3)
                .all()
            )
            if matched:
                return matched
        return q.all()

    def _find_activity(self, session: Session, topics: list[str]):
        q = (session.query(TransitionActivity)
             .filter(TransitionActivity.status.in_(("preset", "published"))))
        for act in q.all():
            if topics and set(act.topics_json or []) & set(topics):
                return act
        return q.first()

    def _active_provider(self, session):
        try:
            from ..models import AppSetting

            row = session.get(AppSetting, "active_model")
            pid = (row.value_json or {}).get("provider_id") if row else None
            if pid and self._provider_resolver(pid) is not None:
                return pid, None
            from ..models import LlmProviderRow

            first = session.query(LlmProviderRow).first()
            if first is not None and self._provider_resolver(first.id) is not None:
                return first.id, None
        except Exception:
            pass
        return None, None

    # ---------- TV 上行（WS transition.*） ----------

    def on_select(self, session: Session, device_id: str, transition_id: str,
                  option_type: str) -> dict:
        ts = session.get(TransitionSession, transition_id)
        if ts is None or ts.state == "ended":
            return {"ok": False, "reason": "transition 不存在或已结束"}
        ts.state = "interaction"
        ts.accepted = True
        ts.selected_type = option_type
        ts.started_at = ts.started_at or datetime.now(UTC)
        self._record_signal(session, ts, "transition_joined")
        session.commit()
        # 路由：音频 → 直接找内容并播放（受 AUDIO 预算）；离屏 → 活动卡；
        # 其余 → interaction（TV 进入语音对话，Context Assembler 注入 Transition 块）
        if option_type == "song_story":
            self._route_audio(session, device_id, ts)
        elif option_type in ("offscreen_game", "real_explore"):
            self._route_offscreen(session, device_id, ts)
        else:
            self._notify(device_id, "transition.state", {
                "state": "interaction", "selected_type": option_type,
            })
        return {"ok": True, "state": ts.state}

    def on_reject(self, session: Session, device_id: str, transition_id: str) -> dict:
        """拒绝即止（硬性约束 11）：立即 END + 记录，不重复说服。"""
        ts = session.get(TransitionSession, transition_id)
        if ts is None:
            return {"ok": False}
        ts.rejected = True
        self._end(session, device_id, ts, "rejected")
        self._record_signal(session, ts, "transition_rejected")
        session.commit()
        return {"ok": True}

    def on_activity_done(self, session: Session, device_id: str,
                         transition_id: str) -> dict:
        ts = session.get(TransitionSession, transition_id)
        if ts is None:
            return {"ok": False}
        self._end(session, device_id, ts, "accepted_completed")
        session.commit()
        return {"ok": True}

    # ---------- 路由 ----------

    def _route_audio(self, session: Session, device_id: str, ts) -> None:
        topics = self._trigger_topics(ts)
        cands = self._find_audio(session, topics)
        if not cands:
            self._notify(device_id, "transition.state", {
                "state": "interaction", "selected_type": "song_story",
                "transition_id": ts.id})
            return
        from ..models import Device, EntityAsset

        target = cands[0]
        device = session.query(Device).filter(Device.id == device_id).first()
        link = (session.query(EntityAsset)
                .filter(EntityAsset.entity_id == target.id).first())
        if device is None or link is None:
            return
        media = session.get(Media, link.asset_id)
        if media is None:
            return
        pb, decision, token = self._playback.request_playback(
            session, device, media, "play", 0, "ai", None)
        if not decision.allowed:
            # AUDIO 预算不足 → 温和收尾（不绕过 Policy）
            self._notify(device_id, "transition.state", {
                "state": "ended", "ended_reason": "audio_budget_denied",
                "transition_id": ts.id})
            self._end(session, device_id, ts, "audio_budget_denied")
            return
        descriptor = self._playback.stream_descriptor(session, pb, token)
        self._end(session, device_id, ts, "audio_handoff")
        self._notify(device_id, "playback.command", {
            "command_id": new_id(), "action": "start", "playback_id": pb.id,
            "media_id": pb.media_id, "stream_descriptor": descriptor,
        }, playback_id=pb.id)

    def _route_offscreen(self, session: Session, device_id: str, ts) -> None:
        activity = self._find_activity(session, self._trigger_topics(ts))
        ts.state = "interaction"
        if activity is not None:
            self._notify(device_id, "transition.activity", {
                "transition_id": ts.id,
                "activity": {"title": activity.title, "summary": activity.summary},
            })
        self._notify(device_id, "transition.state", {
            "state": "offscreen"})

    def _trigger_topics(self, ts) -> list[str]:
        return list((ts.trigger_json or {}).get("topics") or [])

    # ---------- 时间盒 ----------

    def _enforce_deadlines(self) -> None:
        now = datetime.now(UTC)
        with self._db() as session:
            expired = (
                session.query(TransitionSession)
                .filter(TransitionSession.state.in_(("offer", "interaction")),
                        TransitionSession.deadline.isnot(None),
                        TransitionSession.deadline < now)
                .all()
            )
            for ts in expired:
                self._end(session, None, ts, "timeout")
            session.commit()

    def _end(self, session: Session, device_id: str | None, ts,
             reason: str) -> None:
        now = datetime.now(UTC)
        ts.state = "ended"
        ts.ended_reason = reason
        ts.finished_at = now
        if ts.started_at is not None and ts.accepted:
            ts.ai_voice_ms = max(0, int((now - ts.started_at).total_seconds() * 1000))
        if device_id:
            self._notify(device_id, "transition.ended", {
                "transition_id": ts.id, "ended_reason": reason,
            })

    # ---------- 兴趣信号（决策九：只存客观引用） ----------

    def _record_signal(self, session: Session, ts, signal_type: str) -> None:
        payload = ts.trigger_json or {}
        entity_id = None
        media_id = payload.get("media_id")
        if media_id:
            entity_id = (
                session.query(ContentEntity.id)
                .filter(ContentEntity.source_media_id == media_id)
                .limit(1)
            ).scalar()
        session.add(InterestSignal(
            id=new_id(), profile_id=ts.profile_id, entity_id=entity_id,
            signal_type=signal_type, source="transition", created_at=datetime.now(UTC)))

    # ---------- notifier ----------

    def _notify(self, device_id: str, event_type: str, payload: dict,
                **kw) -> None:
        try:
            self._notifier(device_id=device_id, event_type=event_type,
                           payload=payload, **kw)
        except Exception:
            logger.exception("transition 事件下发失败 %s", event_type)

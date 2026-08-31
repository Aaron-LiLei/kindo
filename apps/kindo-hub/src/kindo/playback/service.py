"""Playback State / Playback Grant / 观看计时（技术方案 §9）。

- Playback Grant 与 playback 生命周期绑定：无 TTL、无续签；ALLOW 签发，
  stop/ended/error、设备撤销、Policy/内容变化、媒体切换即撤销（§9.2）。
- 观看时长只按 TV 已 ACK 事件驱动的 viewing_interval 累计（§9.5）；
  Seek 只改 position 不计时；event_id 去重保留到 playback 结束。
- 失联保护：进度事件与流请求均静默超过阈值（默认 120s）→ stopped + 撤销 Grant。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Config
from ..errors import conflict, grant_invalid, grant_mismatch, invalid_request, not_found
from ..history.service import HistoryService
from ..models import (
    AppSetting,
    Device,
    InterestSignal,
    Media,
    Playback,
    PlaybackEvent,
    PlaybackGrant,
    ViewingInterval,
)
from ..policy.engine import PolicyDecision, PolicyEngine
from ..security import sha256_hex
from ..util import new_id, new_opaque_token

# 兴趣信号来源映射（ANA-007：浏览/AI/接力）：Playback.source(ui|ai) → interest source
_INTEREST_SOURCE_MAP = {"ui": "browse", "ai": "ai"}

_MIME_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".mov": "video/quicktime",
}


def path_ext(path_key: str) -> str:
    dot = path_key.rfind(".")
    return path_key[dot:].lower() if dot >= 0 else ""


logger = logging.getLogger("kindo.playback")

ACTIVE_STATES = {"created", "authorized", "starting", "playing", "paused"}
TERMINAL_STATES = {"ended", "stopped", "error"}

# §9.1 状态机：事件 -> (来源状态集合, 目标状态)
_TRANSITIONS: dict[str, dict] = {
    "started": {"from": {"authorized", "starting"}, "to": "playing"},
    "resumed": {"from": {"paused"}, "to": "playing"},
    "paused": {"from": {"playing"}, "to": "paused"},
    "ended": {"from": {"playing", "paused"}, "to": "ended"},
    "stopped": {"from": {"playing", "paused", "starting", "authorized", "created"}, "to": "stopped"},
    "error": {"from": {"playing", "paused", "starting", "authorized"}, "to": "error"},
}

Notifier = Callable[..., object]


class PlaybackService:
    def __init__(self, db_session_factory, cfg: Config, policy: PolicyEngine, history: HistoryService,
                 notifier: Notifier | None = None, boundary=None):
        self._db = db_session_factory
        self._cfg = cfg
        self._policy = policy
        self._history = history
        self._notifier = notifier or (lambda **kw: None)
        self._boundary = boundary  # Policy Boundary Event 发布器（v0.3 决策六）
        # playback_id -> 最近一次媒体流请求时间（失联保护的流侧信号）
        self._last_stream_at: dict[str, datetime] = {}

    # ---------- 查询 ----------

    def current_playback(self, session: Session, profile_id: str) -> Playback | None:
        return (
            session.query(Playback)
            .filter(Playback.profile_id == profile_id, Playback.state.in_(ACTIVE_STATES))
            .order_by(Playback.created_at.desc())
            .first()
        )

    def get(self, session: Session, playback_id: str) -> Playback:
        pb = session.get(Playback, playback_id)
        if pb is None:
            raise not_found("playback 不存在")
        return pb

    # ---------- 统一播放入口（§3.1 POST /playbacks） ----------

    def request_playback(
        self,
        session: Session,
        device: Device,
        media: Media,
        action: str,
        start_position_ms: int | None,
        source: str,
        idempotency_key: str | None,
        interest_source: str | None = None,
    ) -> tuple[Playback | None, PolicyDecision, str | None]:
        """返回 (playback|None, decision, grant_token|None)。deny 时不建立 active playback。

        interest_source：兴趣信号来源覆盖（ANA-007 接力路由传 "transition"），
        缺省按 source 映射（ui→browse / ai→ai）。"""
        if action not in ("play", "resume", "next", "course_continue"):
            raise invalid_request(f"非法 action: {action}")
        if media.missing:
            raise invalid_request("这个内容不在媒体库里（文件可能已被移动或删除）",
                                  details={"reason_code": "media_missing"})
        if not media.playable:
            # §1.2 兼容矩阵外：明确报错（不转码）；TV 按 reason_code 分型文案
            raise invalid_request("这台电视放不了这个视频（格式不兼容）",
                                  details={"reason_code": "media_not_playable",
                                           "notes": (media.probe_json or {}).get("notes") or [],
                                           "container": (media.probe_json or {}).get("container"),
                                           "video_codec": (media.probe_json or {}).get("video_codec")})

        profile_id = self._default_profile(session)
        current = self.current_playback(session, profile_id)
        now = datetime.now(UTC)

        # 幂等：同一 Key + 同一 device 返回首次结果（§2.1）。
        # 重放前仍执行 may_continue：Policy 版本/内容范围/时段变化立即生效，
        # 幂等重放不得绕过校验（§9.2）。
        idem_key = None
        if idempotency_key:
            idem_key = f"idem:{device.id}:{idempotency_key}"
            row = session.get(AppSetting, idem_key)
            if row is not None:
                data = row.value_json or {}
                pb = session.get(Playback, data.get("playback_id", ""))
                if pb is not None and pb.state not in TERMINAL_STATES:
                    grant = self._active_grant(session, pb.id)
                    media_row = session.get(Media, pb.media_id)
                    decision = (
                        self._policy.may_continue(session, pb, media_row, now)
                        if media_row is not None else None
                    )
                    if grant is not None and decision is not None and decision.allowed:
                        token = self._reissue_grant(session, pb, grant)
                        return pb, decision, token
                    if decision is not None and not decision.allowed:
                        self._deny_and_terminate(session, pb, decision, "policy_changed")
                        session.commit()

        if action == "resume":
            if current is None or current.state != "paused":
                raise conflict("当前没有可恢复的暂停播放")
            decision = self._policy.may_start(session, profile_id, media, "resume", now, current)
            if not decision.allowed:
                self._publish_boundary_on_deny(session, profile_id, decision,
                                               media_id=media.id,
                                               device_id=device.id, title=media.title)
                session.commit()
                return None, decision, None
            # resume 复用同一 playback：重签 Grant（旧 Grant 撤销）
            self._revoke_grants(session, current.id)
            token = self._issue_grant(session, current, decision.policy_version)
            current.state = "authorized"
            current.last_seen_at = now
            session.commit()
            return current, decision, token

        # play / next / course_continue：先 Policy，allow 后 A-11 单 active 自动切换（停旧建新）
        decision = self._policy.may_start(session, profile_id, media, action, now, current)
        if not decision.allowed:
            self._publish_boundary_on_deny(session, profile_id, decision,
                                           media_id=media.id,
                                           device_id=device.id, title=media.title)
            session.commit()
            return None, decision, None

        if current is not None:
            self._terminate(session, current, "stopped", reason="switch_media")

        # v0.3 维度快照：entity/asset 挂载 + content_class/modality（Policy v2 计量用）
        entity = self._entity_of(session, media)
        dims_cc, dims_mod = self._policy.entity_dims(session, media)

        pb = Playback(
            id=new_id(), device_id=device.id, profile_id=profile_id, media_id=media.id,
            entity_id=entity.id if entity else None,
            asset_id=media.id,
            content_class=dims_cc,
            modality=dims_mod,
            action=action, source=source, state="authorized",
            position_ms=start_position_ms or 0,
            started_at=now, last_seen_at=now,
        )
        session.add(pb)
        session.flush()
        token = self._issue_grant(session, pb, decision.policy_version)
        # 兴趣信号：选择即记录（ANA-007 内容来源 browse/ai/transition）
        self._record_interest(session, profile_id, media.id, "selected",
                               interest_source or source)

        if idem_key:
            session.merge(AppSetting(key=idem_key, value_json={
                "playback_id": pb.id, "media_id": media.id, "action": action,
            }))
        session.commit()
        return pb, decision, token

    def _default_profile(self, session: Session) -> str:
        from ..models import Profile

        row = session.query(Profile).first()
        return row.id if row else "default"

    @staticmethod
    def _entity_of(session: Session, media: Media):
        """media 行对应的内容实体（v0.3 统一目录）；无绑定返回 None。"""
        from ..models import ContentEntity

        return (
            session.query(ContentEntity)
            .filter(ContentEntity.source_media_id == media.id)
            .first()
        )

    def default_profile_id(self, session: Session) -> str:
        return self._default_profile(session)

    # ---------- 控制（§3.1 POST /playbacks/{id}/control） ----------

    def control(self, session: Session, device: Device, playback_id: str, action: str,
                position_ms: int | None) -> dict:
        pb = self.get(session, playback_id)
        if pb.device_id != device.id:
            raise not_found("playback 不存在")
        now = datetime.now(UTC)
        result: dict[str, object]

        if action == "pause":
            self._apply_transition(session, pb, "paused", position_ms, now, source="control")
            result = {"playback_id": pb.id, "state": pb.state}
        elif action == "seek":
            if position_ms is None or position_ms < 0:
                raise invalid_request("seek 需要 position_ms")
            duration = self._duration(session, pb)
            # duration 未知（0）时不钳制，保留 TV 上报原值
            pb.position_ms = min(position_ms, duration) if duration > 0 else position_ms
            pb.last_seen_at = now
            result = {"playback_id": pb.id, "position_ms": pb.position_ms}
        elif action == "stop":
            self._terminate(session, pb, "stopped", reason="user_stop")
            result = {"playback_id": pb.id, "state": pb.state}
        elif action == "resume":
            # resume 建议走 play_media(resume)/POST /playbacks 统一入口重新 Policy（§8.2）
            raise conflict("请经 POST /api/v1/playbacks (action=resume) 重新执行 Policy")
        else:
            raise invalid_request(f"非法控制动作: {action}")
        session.commit()
        return result

    def _duration(self, session: Session, pb: Playback) -> int:
        media = session.get(Media, pb.media_id)
        return media.duration_ms if media else 0

    # ---------- TV 播放事件（§4.1 / §9.5） ----------

    def handle_tv_event(self, session: Session, device: Device, event: dict) -> dict:
        event_type = event.get("type", "")
        if not event_type.startswith("playback."):
            raise invalid_request(f"非 playback 事件: {event_type}")
        kind = event_type.split(".", 1)[1]
        playback_id = event.get("playback_id")
        event_id = event.get("event_id")
        if not playback_id or not event_id:
            raise invalid_request("缺少 playback_id / event_id")

        pb = session.get(Playback, playback_id)
        if pb is None:
            raise not_found("playback 不存在")
        if pb.device_id != device.id:
            raise grant_mismatch()

        # event_id 去重（§4.2）：重复事件直接 ack，不重复计时
        if session.get(PlaybackEvent, event_id) is not None:
            return {"ack": event_id, "duplicate": True}
        session.add(PlaybackEvent(event_id=event_id, playback_id=playback_id))

        position_ms = event.get("position_ms")
        if isinstance(position_ms, int) and position_ms < 0:
            position_ms = 0
        now = datetime.now(UTC)
        pb.last_event_id = event_id

        if kind == "progress":
            pb.position_ms = position_ms if position_ms is not None else pb.position_ms
            pb.last_seen_at = now
        elif kind == "seeked":
            pb.position_ms = position_ms if position_ms is not None else pb.position_ms
            pb.last_seen_at = now  # Seek 位移不当观看时长（§9.5）
        elif kind == "track_changed":
            if event.get("audio_track_id") is not None:
                pb.audio_track_id = event["audio_track_id"]
            if event.get("subtitle_track_id") is not None:
                pb.subtitle_track_id = event["subtitle_track_id"]
            pb.last_seen_at = now
        elif kind in _TRANSITIONS:
            self._apply_transition(
                session, pb, kind, position_ms, now, source="tv_event",
                player_state=event.get("player_state"),
            )
        else:
            raise invalid_request(f"未知播放事件: {kind}")

        try:
            session.commit()
        except IntegrityError:
            # event_id 主键冲突 = 并发重复事件（TV 重试）：按 duplicate ack（§4.2）
            session.rollback()
            return {"ack": event_id, "duplicate": True}
        return {"ack": event_id}

    def _apply_transition(self, session: Session, pb: Playback, kind: str,
                          position_ms: int | None, now: datetime, *,
                          source: str, player_state: str | None = None) -> None:
        if kind not in _TRANSITIONS:
            raise invalid_request(f"非法状态转移: {kind}")
        trans = _TRANSITIONS[kind]
        # started 允许从 authorized/starting；对重复/迟到事件宽容处理（幂等由 event_id 保证）
        if pb.state in trans["from"] or pb.state == trans["to"]:
            if pb.state == trans["to"]:
                return  # 已处于目标态（迟到重复）
        elif pb.state in TERMINAL_STATES:
            logger.warning("终态 playback %s 收到 %s 事件，忽略", pb.id, kind)
            return
        # 其他非法来源状态也按容错处理：记录日志并按目标态收敛
        if pb.state not in trans["from"] and pb.state != trans["to"]:
            logger.warning("playback %s 状态 %s 收到 %s（期望 %s）",
                           pb.id, pb.state, kind, trans["from"])

        if position_ms is not None:
            pb.position_ms = position_ms
        pb.last_seen_at = now
        pb.state = trans["to"]

        if kind in ("started", "resumed"):
            self._open_interval(session, pb, now)
        elif kind in ("paused", "ended", "stopped", "error"):
            self._close_interval(session, pb, now, close_reason=kind)
            media = session.get(Media, pb.media_id)
            if media is not None:
                self._history.update_on_playback_change(
                    session, pb.profile_id, media, pb.position_ms,
                    add_watched_ms=0, ended=(kind == "ended"), entity_id=pb.entity_id,
                )
        if kind in ("ended", "stopped", "error"):
            self._revoke_grants(session, pb.id)
            pb.ended_at = now
            if kind == "ended":
                self._record_interest(session, pb.profile_id, pb.media_id,
                                      "watched", pb.source)
                self._publish_boundary_on_ended(session, pb, now)

    # ---------- viewing_interval ----------

    def _open_interval(self, session: Session, pb: Playback, now: datetime) -> None:
        open_one = (
            session.query(ViewingInterval)
            .filter(ViewingInterval.playback_id == pb.id, ViewingInterval.ended_at.is_(None))
            .one_or_none()
        )
        if open_one is not None:
            return
        session.add(ViewingInterval(
            playback_id=pb.id, started_at=now, id=new_id(),
            content_class=pb.content_class, modality=pb.modality,
        ))

    def _close_interval(self, session: Session, pb: Playback, now: datetime, *, close_reason: str) -> None:
        for iv in (
            session.query(ViewingInterval)
            .filter(ViewingInterval.playback_id == pb.id, ViewingInterval.ended_at.is_(None))
            .all()
        ):
            iv.ended_at = now
            iv.duration_ms = max(0, int((now - iv.started_at).total_seconds() * 1000))
            iv.close_reason = close_reason
            pb.watched_ms += iv.duration_ms

    def _terminate(self, session: Session, pb: Playback, state: str, *, reason: str) -> None:
        now = datetime.now(UTC)
        if pb.state in TERMINAL_STATES:
            return
        self._close_interval(session, pb, now, close_reason=reason)
        pb.state = state
        pb.ended_at = now
        self._revoke_grants(session, pb.id)
        self._last_stream_at.pop(pb.id, None)
        media = session.get(Media, pb.media_id)
        if media is not None:
            self._history.update_on_playback_change(
                session, pb.profile_id, media, pb.position_ms, add_watched_ms=0,
                ended=False, entity_id=pb.entity_id,
            )

    def _record_interest(self, session: Session, profile_id: str, media_id: str,
                         signal_type: str, source: str | None) -> None:
        """兴趣信号客观记录（ANA-007）：selected（选择播放）/ watched（自然看完）；
        只存 entity 引用与来源（browse/ai/transition），失败不影响播放主流程。"""
        try:
            from ..models import ContentEntity

            entity_id = (
                session.query(ContentEntity.id)
                .filter(ContentEntity.source_media_id == media_id)
                .limit(1)
            ).scalar()
            session.add(InterestSignal(
                id=new_id(), profile_id=profile_id, entity_id=entity_id,
                signal_type=signal_type,
                source=_INTEREST_SOURCE_MAP.get(source or "", source) or "browse",
            ))
        except Exception:
            logger.exception("兴趣信号记录失败（不影响主流程）")

    # ---------- Grant ----------

    def _issue_grant(self, session: Session, pb: Playback, policy_version: int) -> str:
        token = new_opaque_token(32)
        session.add(PlaybackGrant(
            id=new_id(), playback_id=pb.id, device_id=pb.device_id, media_id=pb.media_id,
            token_hash=sha256_hex(token), policy_version=policy_version,
        ))
        return token

    def _reissue_grant(self, session: Session, pb: Playback, old: PlaybackGrant) -> str:
        old.revoked_at = datetime.now(UTC)
        return self._issue_grant(session, pb, old.policy_version)

    def regrant_for_device(self, session: Session, device: Device,
                           playback_id: str) -> tuple[Playback, str]:
        """REST 兜底（接力 audio handoff）：对自己设备名下的活跃 playback 重发
        新 Grant（旧 Grant 立即作废；同 playback 任一时刻仅一个有效 Grant）。"""
        pb = session.get(Playback, playback_id)
        if pb is None:
            raise not_found("播放会话不存在")
        if pb.device_id != device.id:
            raise grant_mismatch()
        if pb.state in TERMINAL_STATES:
            raise grant_invalid("该播放已结束")
        grant = self._active_grant(session, pb.id)
        token = (self._reissue_grant(session, pb, grant) if grant is not None
                 else self._issue_grant(session, pb, 0))
        session.commit()
        return pb, token

    def _active_grant(self, session: Session, playback_id: str) -> PlaybackGrant | None:
        return (
            session.query(PlaybackGrant)
            .filter(PlaybackGrant.playback_id == playback_id, PlaybackGrant.revoked_at.is_(None))
            .order_by(PlaybackGrant.created_at.desc())
            .first()
        )

    def _revoke_grants(self, session: Session, playback_id: str) -> None:
        now = datetime.now(UTC)
        for g in session.query(PlaybackGrant).filter(
            PlaybackGrant.playback_id == playback_id, PlaybackGrant.revoked_at.is_(None)
        ).all():
            g.revoked_at = now

    def validate_stream_access(self, session: Session, device: Device, media_id: str,
                               grant_token: str) -> Playback:
        """媒体流逐请求校验（§9.2）：Device Token + Grant hash + binding + revoke。"""
        from ..errors import grant_invalid

        g = (
            session.query(PlaybackGrant)
            .filter(PlaybackGrant.token_hash == sha256_hex(grant_token))
            .order_by(PlaybackGrant.created_at.desc())
            .first()
        )
        if g is None or g.revoked_at is not None:
            raise grant_invalid()
        if g.device_id != device.id or g.media_id != media_id:
            raise grant_mismatch()
        pb = session.get(Playback, g.playback_id)
        if pb is None or pb.state in TERMINAL_STATES:
            raise grant_invalid()
        self._last_stream_at[pb.id] = datetime.now(UTC)
        return pb

    # ---------- Policy 变化 / 设备撤销 / 失联保护（§9.2） ----------

    def _deny_and_terminate(self, session: Session, pb: Playback,
                            decision: PolicyDecision, reason: str) -> None:
        """may_continue deny：撤销 Grant，向 TV 推送 policy.denied + stop（立即生效）。"""
        self._terminate(session, pb, "stopped", reason=reason)
        self._notifier(
            device_id=pb.device_id, event_type="policy.denied",
            payload={
                "reason_code": decision.reason_code,
                "constraints": decision.constraints,
                "playback_id": pb.id,
            },
            playback_id=pb.id,
        )
        self._notifier(
            device_id=pb.device_id, event_type="playback.command",
            payload={"command_id": new_id(), "action": "stop", "playback_id": pb.id},
            playback_id=pb.id,
        )

    def on_policy_saved(self, session: Session, new_version: int) -> int:
        """保存 Policy：撤销 active grants，向受影响 TV 推送 stop/deny（立即生效）。"""
        affected = 0
        now = datetime.now(UTC)
        for pb in session.query(Playback).filter(Playback.state.in_(ACTIVE_STATES)).all():
            media = session.get(Media, pb.media_id)
            if media is None:
                continue
            decision = self._policy.may_continue(session, pb, media, now)
            if not decision.allowed:
                self._deny_and_terminate(session, pb, decision, "policy_changed")
                affected += 1
        session.commit()
        return affected

    def _publish_boundary_on_deny(self, session, profile_id: str,
                                  decision, media_id: str,
                                  device_id: str = "", title: str = "") -> None:
        """Boundary Event 源③：软限制 deny（决策六）。boundary 取 media 粒度：
        同日同媒体同限制的重复 deny（连点/重试）只 offer 一次。"""
        if self._boundary is None:
            return
        try:
            self._boundary.publish_soft_deny(
                session, profile_id, decision.reason_code or "", media_id, {
                    "source": "deny",
                    "reason_code": decision.reason_code,
                    "device_id": device_id or None,
                    "media_id": media_id,
                    "title": title or None,
                    "constraints": decision.constraints,
                })
        except Exception:
            logger.exception("deny 边界事件发布失败")

    def _publish_boundary_on_ended(self, session, pb, now) -> None:
        """Boundary Event 源②：自然播完 + 配额耗尽（最常见触发场景）。"""
        from ..policy.boundary import check_quota_exhausted

        if self._boundary is None:
            return
        try:
            rules, _v = self._policy.current(session)
            media = session.get(Media, pb.media_id)
            if media is None:
                return
            for limit_type, bid in check_quota_exhausted(
                    session, self._policy, rules, pb.profile_id, now, pb):
                self._boundary.publish(session, pb.profile_id, limit_type, bid, {
                    "source": "ended", "playback_id": pb.id,
                    "device_id": pb.device_id,
                    "media_id": pb.media_id, "title": media.title,
                    "topics": [t[0] for t in session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT ct.name FROM entity_topic et"
                            " JOIN content_topic ct ON ct.id = et.topic_id"
                            " JOIN content_entity ce ON ce.id = et.entity_id"
                            " WHERE ce.source_media_id = :m LIMIT 3"),
                        {"m": pb.media_id}).fetchall()],
                })
        except Exception:
            logger.exception("ended 边界事件发布失败")

    def enforce_policy_continues(self) -> int:
        """Policy 执行闭环（§9.2 may_continue）：周期性复核全部 active playback。

        硬截止（可观看时段结束）到点停止、Policy 版本/内容范围变化立即生效——
        由后台循环调用（不依赖保存动作或客户端请求触发）。
        """
        now = datetime.now(UTC)
        affected = 0
        with self._db() as session:
            for pb in session.query(Playback).filter(Playback.state.in_(ACTIVE_STATES)).all():
                media = session.get(Media, pb.media_id)
                if media is None:
                    continue
                decision = self._policy.may_continue(session, pb, media, now)
                if not decision.allowed:
                    self._deny_and_terminate(session, pb, decision, "policy_changed")
                    affected += 1
                    continue
                # Boundary Event 源①：Meter 检测播放中到界（软限制不切断，
                # 但到达边界即发布事件，供 Transition Orchestrator 接力）
                if self._boundary is not None and pb.state == "playing":
                    from ..policy.boundary import check_threshold_reached

                    rules, _v = self._policy.current(session)
                    try:
                        for limit_type, bid in check_threshold_reached(
                                session, self._policy, rules, pb.profile_id, now, pb):
                            self._boundary.publish(session, pb.profile_id, limit_type, bid, {
                                "source": "threshold", "playback_id": pb.id,
                                "media_id": pb.media_id,
                            })
                    except Exception:
                        logger.exception("threshold 边界事件发布失败")
            session.commit()
        return affected

    def on_device_revoked(self, session: Session, device_id: str) -> None:
        now = datetime.now(UTC)
        for pb in session.query(Playback).filter(
            Playback.device_id == device_id, Playback.state.in_(ACTIVE_STATES)
        ).all():
            self._close_interval(session, pb, now, close_reason="device_revoked")
            pb.state = "stopped"
            pb.ended_at = now
            self._revoke_grants(session, pb.id)
        session.commit()

    def sweep_lost_playbacks(self) -> int:
        """失联保护：进度事件与流请求均静默超阈值 → stopped + 撤销 Grant。"""
        threshold = timedelta(seconds=self._cfg.lost_protection_seconds)
        now = datetime.now(UTC)
        affected = 0
        with self._db() as session:
            for pb in session.query(Playback).filter(Playback.state.in_(ACTIVE_STATES)).all():
                last_seen = pb.last_seen_at or pb.created_at
                last_stream = self._last_stream_at.get(pb.id)
                silence = now - max(last_seen, last_stream or last_seen)
                if silence > threshold:
                    self._terminate(session, pb, "stopped", reason="lost_protection")
                    affected += 1
            session.commit()
        return affected

    def recover_on_startup(self) -> int:
        """Hub 重启：Session 失效（内存态），打开的 interval 收口，active playback 转 stopped。"""
        now = datetime.now(UTC)
        affected = 0
        with self._db() as session:
            for pb in session.query(Playback).filter(Playback.state.in_(ACTIVE_STATES)).all():
                self._close_interval(session, pb, now, close_reason="hub_restart")
                pb.state = "stopped"
                pb.ended_at = now
                self._revoke_grants(session, pb.id)
                affected += 1
            session.commit()
        self._last_stream_at.clear()
        return affected

    # ---------- stream descriptor（§9.3） ----------

    def stream_descriptor(self, session: Session, pb: Playback, grant_token: str) -> dict:
        media = session.get(Media, pb.media_id)
        from ..models import SubtitleTrack

        tracks = session.query(SubtitleTrack).filter(SubtitleTrack.media_id == pb.media_id).all()
        probe = (media.probe_json or {}) if media else {}
        return {
            "playback_id": pb.id,
            "media_id": pb.media_id,
            "url": f"/api/v1/media/{pb.media_id}/stream",
            # 网络源跳过探测时 mime_type 为空——按容器扩展名推断，
            # stream_descriptor 不得携带 null（TV 端 DTO 解析曾因此全线失败）
            "mime_type": (media.mime_type if media else None)
            or _MIME_BY_EXT.get(path_ext(media.path_key) if media else "", "video/mp4"),
            "duration_ms": media.duration_ms if media else 0,
            "grant": grant_token,
            "audio_tracks": [
                {"id": t["id"], "language": t.get("language"), "label": t.get("title") or t.get("codec")}
                for t in probe.get("audio", [])
            ],
            "subtitle_tracks": [
                {
                    "id": t.id,
                    "language": t.language,
                    "format": "webvtt",
                    "source_type": t.source_type,
                    "grounding_available": t.grounding_available,
                }
                for t in tracks
            ],
        }

"""Family Policy v2 引擎（产品基线 v0.3 决策五/六，PRD POL-001~014）。

判定语义（正交维度）：
- content_class（ENTERTAINMENT/LEARNING/STORY/MUSIC/OTHER）× modality
  （VIDEO/AUDIO/AI_VOICE/OFFSCREEN）取自统一内容目录 Canonical 值；
- 判定矩阵：VIDEO → screen_total ∧ video_by_class[class]；AUDIO → audio 预算；
  AI_VOICE → ai_voice 预算；OFFSCREEN → allowed 开关（无分钟配额）；
- may_start 全规则校验；may_continue 软限制不切断当前内容、时段硬截止到点停止
  且不触发接力；Policy 保存 version+1 立即生效（v0.2 语义延续）；
- v1 规则读时自动升维映射（daily_limit → screen_total 等）；
- 计量按 (modality, content_class) 分桶（viewing_interval 维度列）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import Config
from ..models import Media, Playback, PolicyConfig, ViewingInterval

logger = logging.getLogger("kindo.policy")

MEDIA_TYPES = {"episode", "movie", "lesson", "story", "song"}

# v0.2 media_type → (content_class, modality) 缺省映射（entity 缺失时 fallback）
_FALLBACK_DIMS = {
    "episode": ("ENTERTAINMENT", "VIDEO"),
    "movie": ("ENTERTAINMENT", "VIDEO"),
    "lesson": ("LEARNING", "VIDEO"),
    "story": ("STORY", "AUDIO"),
    "song": ("MUSIC", "AUDIO"),
}

TRANSITION_TYPES = ("knowledge", "quiz", "roleplay", "vocabulary",
                    "song_story", "offscreen_game", "real_explore")


@dataclass
class PolicyRules:
    # v1 字段（兼容读取/保存；v2 落于 budgets）
    daily_limit_minutes: int | None = None
    session_limit_minutes: int | None = None
    daily_episode_limit: int | None = None
    allowed_windows: list[dict] = field(default_factory=list)  # [] = 全天允许
    content_scope: dict = field(default_factory=dict)
    autoplay: bool = True
    course_counts_as_entertainment: bool = True
    # v2 三层预算（决策五）
    budgets: dict = field(default_factory=dict)
    offscreen: dict = field(default_factory=dict)
    transition_policy: dict = field(default_factory=dict)

    # ---------- 预算访问（v2 语义，v1 自动升维） ----------

    def screen_total_minutes(self) -> int | None:
        if not self.budgets:
            return self.daily_limit_minutes
        v = self.budgets.get("screen_total_minutes")
        if v is None:
            return self.daily_limit_minutes  # budgets 显式 null → 沿用 v1 升维语义
        return v

    def video_class_minutes(self, content_class: str) -> int | None:
        """分类视频子预算：未配置该类 → 沿用 screen_total（决策五缺省语义）。"""
        by_class = (self.budgets or {}).get("video_by_class") or {}
        if content_class in by_class and by_class[content_class] is not None:
            return by_class[content_class]
        return self.screen_total_minutes()

    def audio_minutes(self) -> int | None:
        if not self.budgets:
            return None  # v1 无音频预算 = 不限
        return self.budgets.get("audio_minutes")

    def ai_voice_minutes(self) -> int | None:
        return (self.budgets or {}).get("ai_voice_minutes")

    def offscreen_allowed(self) -> bool:
        return bool((self.offscreen or {}).get("allowed", True))

    def offscreen_offer_enabled(self) -> bool:
        return bool((self.offscreen or {}).get("offer_enabled", True))

    def transition_enabled(self) -> bool:
        return bool((self.transition_policy or {}).get("enabled", True))

    def transition_types(self) -> list[str]:
        return list((self.transition_policy or {}).get("types") or TRANSITION_TYPES)

    def transition_max_minutes(self, cfg: Config | None = None) -> int:
        v = (self.transition_policy or {}).get("max_minutes")
        if isinstance(v, int | float):
            return int(v)
        return getattr(cfg, "transition_default_max_minutes", 4) if cfg else 4

    def transition_daily_offer_limit(self) -> int:
        v = (self.transition_policy or {}).get("daily_offer_limit")
        return int(v) if isinstance(v, int | float) else 3

    @classmethod
    def parse(cls, raw: dict | None) -> PolicyRules:
        raw = raw or {}
        r = cls()
        for k in ("daily_limit_minutes", "session_limit_minutes", "daily_episode_limit"):
            v = raw.get(k)
            if v is not None:
                r.__setattr__(k, int(v))
        r.allowed_windows = _parse_windows(raw.get("allowed_windows"))
        r.content_scope = raw.get("content_scope") or {}
        r.autoplay = bool(raw.get("autoplay", True))
        r.course_counts_as_entertainment = bool(
            raw.get("course_counts_as_entertainment", True))
        r.budgets = _parse_budgets(raw.get("budgets"))
        r.offscreen = raw.get("offscreen") or {}
        r.transition_policy = raw.get("transition_policy") or {}
        return r

    def to_json(self) -> dict:
        out = {
            "daily_limit_minutes": self.daily_limit_minutes,
            "session_limit_minutes": self.session_limit_minutes,
            "daily_episode_limit": self.daily_episode_limit,
            "allowed_windows": self.allowed_windows,
            "content_scope": self.content_scope,
            "autoplay": self.autoplay,
            "course_counts_as_entertainment": self.course_counts_as_entertainment,
            "budgets": self.budgets or {},
            "offscreen": self.offscreen or {},
            "transition_policy": self.transition_policy or {},
        }
        return out


def _parse_budgets(raw: object) -> dict:
    """v2 预算解析即校验：负数/非整数拒绝。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("budgets 必须是对象")
    out: dict = {}
    for key in ("screen_total_minutes", "audio_minutes", "ai_voice_minutes"):
        v = raw.get(key)
        if v is not None:
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"budgets.{key} 必须是非负整数或 null")
            out[key] = v
        else:
            out[key] = None
    vb = raw.get("video_by_class")
    if vb is not None:
        if not isinstance(vb, dict):
            raise ValueError("budgets.video_by_class 必须是对象")
        out["video_by_class"] = {}
        for k, v in vb.items():
            if v is not None and (not isinstance(v, int) or v < 0):
                raise ValueError(f"video_by_class.{k} 必须是非负整数或 null")
            out["video_by_class"][k] = v
    return out


@dataclass
class PolicyDecision:
    decision: str  # allow | deny
    reason_code: str | None = None
    constraints: dict = field(default_factory=dict)
    policy_version: int = 0
    evaluated_at: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")[:2]
    return dt_time(int(h), int(m))


def _parse_windows(raw: object) -> list[dict]:
    """解析即校验（§9.2）：坏窗口入库会让此后所有 may_start/may_continue 崩溃。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("allowed_windows 必须是列表")
    windows: list[dict] = []
    for w in raw:
        if not isinstance(w, dict):
            raise ValueError("allowed_windows 的每一项必须是对象")
        start = w.get("start", "00:00")
        end = w.get("end", "23:59")
        for label, v in (("start", start), ("end", end)):
            if not isinstance(v, str):
                raise ValueError(f"allowed_windows.{label} 必须是 HH:MM 字符串")
            try:
                _parse_hhmm(v)
            except (ValueError, IndexError) as exc:
                raise ValueError(
                    f"allowed_windows.{label} 非法: {v!r}（需要 HH:MM，00-23/00-59）"
                ) from exc
        windows.append({"start": start, "end": end})
    return windows


class PolicyEngine:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        try:
            self._tz: tzinfo = ZoneInfo(cfg.timezone)
        except Exception:
            logger.error("配置时区 %r 非法（回退 UTC），请检查 kindo.yaml timezone 字段",
                         cfg.timezone)
            self._tz = UTC

    # ---------- 规则存取 ----------

    def current(self, session: Session) -> tuple[PolicyRules, int]:
        row = session.query(PolicyConfig).order_by(PolicyConfig.version.desc()).first()
        if row is None:
            return PolicyRules(), 0
        return PolicyRules.parse(row.rules_json), row.version

    def save(self, session: Session, rules_json: dict) -> tuple[PolicyRules, int]:
        rules = PolicyRules.parse(rules_json)  # 解析即校验；非法类型抛 ValueError
        latest = session.query(PolicyConfig).order_by(PolicyConfig.version.desc()).first()
        version = (latest.version + 1) if latest else 1
        session.add(PolicyConfig(version=version, rules_json=rules.to_json()))
        session.flush()
        return rules, version

    # ---------- 时间窗 ----------

    def _in_allowed_window(self, rules: PolicyRules, now: datetime) -> bool:
        if not rules.allowed_windows:
            return True
        local = now.astimezone(self._tz)
        t = local.time()
        for w in rules.allowed_windows:
            start = _parse_hhmm(w.get("start", "00:00"))
            end = _parse_hhmm(w.get("end", "23:59"))
            if start <= end:
                if start <= t <= end:
                    return True
            else:  # 跨午夜窗口
                if t >= start or t <= end:
                    return True
        return False

    def _window_constraints(self, rules: PolicyRules) -> dict:
        if not rules.allowed_windows:
            return {}
        return {"allowed_windows": rules.allowed_windows}

    # ---------- 维度与计量（Policy Meter，决策五） ----------

    @staticmethod
    def entity_dims(session: Session, media: Media) -> tuple[str, str]:
        """media 行 → (content_class, modality)：统一目录 Canonical 值优先，
        缺失时按 v0.2 media_type fallback（硬性约束 12）。

        绕过防护（决策五 5.3）：content_class 与结构缺省不一致且来源未经家长
        （parent/sidecar）确认时，按结构缺省（更严格侧）判定——改标 LEARNING
        不得放宽 ENTERTAINMENT 内容的预算。
        """
        from ..models import ContentEntity

        fallback = _FALLBACK_DIMS.get(media.media_type, ("ENTERTAINMENT", "VIDEO"))
        ent = (
            session.query(ContentEntity)
            .filter(ContentEntity.source_media_id == media.id)
            .first()
        )
        if ent is None:
            return fallback
        cc = ent.content_class or fallback[0]
        if cc != fallback[0]:
            prov = ((ent.meta_provenance_json or {}).get("content_class")) or {}
            if prov.get("source") not in ("parent", "sidecar"):
                cc = fallback[0]  # 未确认的分类漂移回落
        return cc, ent.modality or fallback[1]

    def _local_day_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        local = now.astimezone(self._tz)
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def consumed_ms_today(self, session: Session, profile_id: str, now: datetime,
                          modality: str | None = None,
                          content_class: str | None = None) -> int:
        """按 (modality, content_class) 分桶的当日消耗（viewing_interval 维度列）。"""
        start, end = self._local_day_bounds(now)
        q = (
            session.query(func.coalesce(func.sum(ViewingInterval.duration_ms), 0))
            .join(Playback, Playback.id == ViewingInterval.playback_id)
            .filter(
                Playback.profile_id == profile_id,
                ViewingInterval.started_at >= start,
                ViewingInterval.started_at < end,
            )
        )
        if modality is not None:
            if modality == "VIDEO":
                # v0.2 存量行无维度列：NULL modality 时代全是视频
                from sqlalchemy import or_

                q = q.filter(or_(ViewingInterval.modality == "VIDEO",
                                 ViewingInterval.modality.is_(None)))
            else:
                q = q.filter(ViewingInterval.modality == modality)
        if content_class is not None:
            q = q.filter(ViewingInterval.content_class == content_class)
        return int(q.scalar() or 0)

    def ai_voice_consumed_ms(self, session: Session, profile_id: str,
                             now: datetime) -> int:
        """AI 语音互动消耗（本地日）= 接力 Σ(transition_session.ai_voice_ms)
        + 常规对话 Σ(conversation_usage.duration_ms)（2026-08-26 口径闭环）。"""
        from ..models import ConversationUsage, TransitionSession

        start, end = self._local_day_bounds(now)
        transition = (
            session.query(func.coalesce(func.sum(TransitionSession.ai_voice_ms), 0))
            .filter(TransitionSession.profile_id == profile_id,
                    TransitionSession.created_at >= start,
                    TransitionSession.created_at < end)
            .scalar()
        )
        conversation = (
            session.query(func.coalesce(func.sum(ConversationUsage.duration_ms), 0))
            .filter(ConversationUsage.profile_id == profile_id,
                    ConversationUsage.started_at >= start,
                    ConversationUsage.started_at < end)
            .scalar()
        )
        return int(transition or 0) + int(conversation or 0)

    def may_start_ai_voice(self, session: Session, profile_id: str,
                           now: datetime) -> PolicyDecision:
        """判定矩阵 AI_VOICE 分支（§9.2）：ai_voice 预算尽 → 拒新对话。

        软限制语义：不切断进行中的会话（resume 不设门），仅拦新 Session。
        预算未配置（null，默认）→ 永远允许。
        """
        rules, version = self.current(session)
        from ..util import now_iso

        ai_budget = rules.ai_voice_minutes()
        if ai_budget is None:
            return PolicyDecision(decision="allow", policy_version=version,
                                  evaluated_at=now_iso(),
                                  constraints={"modality": "AI_VOICE"})
        remaining = self.budget_remaining(session, profile_id, rules, now)
        if remaining.get("ai_voice_seconds", 1) == 0:
            return PolicyDecision(
                decision="deny", reason_code="daily_limit_reached",
                constraints={
                    "modality": "AI_VOICE",
                    "remaining": remaining,
                    "allowed_modalities": self._allowed_modalities(rules, remaining),
                },
                policy_version=version, evaluated_at=now_iso(),
            )
        return PolicyDecision(decision="allow", policy_version=version,
                              evaluated_at=now_iso(),
                              constraints={"modality": "AI_VOICE"})

    def watched_ms_today(self, session: Session, profile_id: str, rules: PolicyRules,
                         now: datetime) -> int:
        """v0.2 兼容：当日全部观看（v1 视角 = 各维度合计）。"""
        return self.consumed_ms_today(session, profile_id, now)

    def budget_remaining(self, session: Session, profile_id: str, rules: PolicyRules,
                         now: datetime, media: Media | None = None,
                         dims: tuple[str, str] | None = None) -> dict:
        """各维度剩余（constraints / summary 用；None = 不限）。"""
        if dims is None:
            dims = self.entity_dims(session, media) if media is not None else (
                "ENTERTAINMENT", "VIDEO")
        cc, mod = dims
        screen_total = rules.screen_total_minutes()
        # 课程豁免（course_counts_as_entertainment=False，POL-007 v1 语义）：
        # LEARNING 桶不计入总屏消耗
        class_exempt = not rules.course_counts_as_entertainment
        out: dict = {}
        if screen_total is not None:
            used = self.consumed_ms_today(session, profile_id, now, modality="VIDEO")
            if class_exempt:
                used -= self.consumed_ms_today(session, profile_id, now,
                                               modality="VIDEO", content_class="LEARNING")
            out["screen_total_seconds"] = max(0, screen_total * 60 - used // 1000)
        class_budget = rules.video_class_minutes(cc)
        if mod == "VIDEO" and class_budget is not None:
            used_c = self.consumed_ms_today(session, profile_id, now,
                                            modality="VIDEO", content_class=cc)
            out["video_class_seconds"] = max(0, class_budget * 60 - used_c // 1000)
            out["video_class"] = cc
        audio_budget = rules.audio_minutes()
        if audio_budget is not None:
            used_a = self.consumed_ms_today(session, profile_id, now, modality="AUDIO")
            out["audio_seconds"] = max(0, audio_budget * 60 - used_a // 1000)
        ai_budget = rules.ai_voice_minutes()
        if ai_budget is not None:
            used_v = self.ai_voice_consumed_ms(session, profile_id, now)
            out["ai_voice_seconds"] = max(0, ai_budget * 60 - used_v // 1000)
        return out

    def episodes_watched_today(self, session: Session, profile_id: str, rules: PolicyRules,
                               now: datetime) -> int:
        """集数计数（§9.6）：单集 ≥50% 或 ended 计一次，每集每日最多一次。"""
        start, end = self._local_day_bounds(now)
        rows = (
            session.query(Playback.media_id, Playback.watched_ms, Playback.state,
                          Media.duration_ms)
            .join(Media, Media.id == Playback.media_id)
            .filter(
                Playback.profile_id == profile_id,
                Media.media_type == "episode",
                Playback.created_at >= start,
                Playback.created_at < end,
            )
            .all()
        )
        counted: set[str] = set()
        for media_id, watched_ms, state, duration_ms in rows:
            reached = duration_ms > 0 and watched_ms >= duration_ms * self._cfg.episode_count_ratio
            if reached or state == "ended":
                counted.add(media_id)
        return len(counted)

    def playback_watched_ms(self, session: Session, playback_id: str) -> int:
        pb = session.get(Playback, playback_id)
        return pb.watched_ms if pb else 0

    # ---------- 内容范围 ----------

    def _scope_check(self, rules: PolicyRules, media: Media) -> str | None:
        scope = rules.content_scope or {}
        mounts = scope.get("allowed_mount_ids")
        if isinstance(mounts, list) and mounts and media.mount_id not in mounts:
            return "content_not_allowed"
        types = scope.get("allowed_media_types")
        if isinstance(types, list) and types and media.media_type not in types:
            return "content_not_allowed"
        blocked_tags = scope.get("blocked_tags") or []
        tags = (media.tags_json or {})
        all_tags = set(tags.get("characters", []) + tags.get("themes", []) + tags.get("tags", []))
        if blocked_tags and all_tags & set(blocked_tags):
            return "content_not_allowed"
        return None

    # ---------- may_start（判定矩阵） ----------

    def may_start(
        self,
        session: Session,
        profile_id: str,
        media: Media,
        action: str,
        now: datetime,
        current_playback: Playback | None = None,
    ) -> PolicyDecision:
        rules, version = self.current(session)
        from ..util import now_iso

        def deny(reason: str, constraints: dict | None = None) -> PolicyDecision:
            return PolicyDecision(
                decision="deny", reason_code=reason,
                constraints=constraints or {}, policy_version=version,
                evaluated_at=now_iso(),
            )

        if action not in ("play", "resume", "next", "course_continue"):
            from ..errors import invalid_request

            raise invalid_request(f"非法播放动作: {action}")

        if not self._in_allowed_window(rules, now):
            reason = (
                "course_rule_denied" if action == "course_continue"
                else "outside_allowed_window"
            )
            return deny(reason, self._window_constraints(rules))

        scope_violation = self._scope_check(rules, media)
        if scope_violation:
            return deny(scope_violation, {"allowed_categories": self._scope_summary(rules)})

        if action == "next" and not rules.autoplay:
            return deny("autoplay_disabled", {"allowed_actions": ["home", "choose"]})

        dims = self.entity_dims(session, media)
        cc, mod = dims
        remaining = self.budget_remaining(session, profile_id, rules, now, dims=dims)

        # ---- 判定矩阵（决策五 5.2）----
        if mod == "VIDEO":
            # 分类豁免：course_counts_as_entertainment=False 时课程不占娱乐/总屏
            class_exempt = (media.media_type == "lesson"
                            and not rules.course_counts_as_entertainment)
            if (not class_exempt
                    and remaining.get("screen_total_seconds") == 0):
                return deny("daily_limit_reached", {
                    "content_class": cc, "modality": mod,
                    "remaining": remaining,
                    "allowed_modalities": self._allowed_modalities(rules, remaining),
                    "transition_available": self._transition_available(
                        session, rules, profile_id, now),
                })
            if (not class_exempt
                    and remaining.get("video_class_seconds") == 0
                    and "video_class_seconds" in remaining):
                return deny("daily_limit_reached", {
                    "content_class": cc, "modality": mod,
                    "remaining": remaining,
                    "allowed_modalities": self._allowed_modalities(rules, remaining),
                    "transition_available": self._transition_available(
                        session, rules, profile_id, now),
                })
        elif mod == "AUDIO":
            if remaining.get("audio_seconds") == 0:
                return deny("daily_limit_reached", {
                    "content_class": cc, "modality": mod,
                    "remaining": remaining,
                    "allowed_modalities": self._allowed_modalities(rules, remaining),
                })
        elif mod == "OFFSCREEN":
            if not rules.offscreen_allowed():
                return deny("content_not_allowed",
                            {"modality": "OFFSCREEN", "offscreen_allowed": False})
        # AI_VOICE 无媒体动作，不经本函数；新对话入口经 may_start_ai_voice（§9.2）

        if rules.session_limit_minutes is not None and current_playback is not None:
            watched = self.playback_watched_ms(session, current_playback.id)
            remaining_ms = max(0, rules.session_limit_minutes * 60_000 - watched)
            if remaining_ms <= 0 and action in ("resume",):
                return deny("session_limit_reached", {"remaining_session_seconds": 0,
                                                      "modality": mod})

        if (rules.daily_episode_limit is not None and media.media_type == "episode"
                and action in ("play", "next")):
            count = self.episodes_watched_today(session, profile_id, rules, now)
            if count >= rules.daily_episode_limit:
                return deny("episode_limit_reached", {"remaining_episodes": 0})

        return PolicyDecision(
            decision="allow", policy_version=version, evaluated_at=now_iso(),
            constraints={"content_class": cc, "modality": mod},
        )

    def _allowed_modalities(self, rules: PolicyRules, remaining: dict) -> list[str]:
        out = []
        if remaining.get("screen_total_seconds", 1) != 0:
            out.append("video")
        if remaining.get("audio_seconds", 1) != 0:
            out.append("audio")
        if remaining.get("ai_voice_seconds", 1) != 0:
            out.append("ai_voice")
        return out

    def _transition_available(self, session: Session, rules: PolicyRules,
                              profile_id: str, now: datetime) -> bool:
        if not rules.transition_enabled():
            return False
        start, _end = self._local_day_bounds(now)
        from ..models import TransitionSession

        offered = (
            session.query(TransitionSession)
            .filter(TransitionSession.profile_id == profile_id,
                    TransitionSession.created_at >= start)
            .count()
        )
        return offered < rules.transition_daily_offer_limit()

    def _scope_summary(self, rules: PolicyRules) -> list:
        scope = rules.content_scope or {}
        return scope.get("allowed_media_types") or ["episode", "movie", "lesson",
                                                    "story", "song"]

    # ---------- may_continue ----------

    def may_continue(self, session: Session, playback: Playback, media: Media,
                     now: datetime) -> PolicyDecision:
        """进行中播放：软限制放行；硬截止到点停止（§9.2，时段截止不触发接力）。"""
        rules, version = self.current(session)
        from ..util import now_iso

        def deny(reason: str, constraints: dict | None = None) -> PolicyDecision:
            return PolicyDecision(
                decision="deny", reason_code=reason,
                constraints=constraints or {}, policy_version=version,
                evaluated_at=now_iso(),
            )

        # Policy 版本或内容范围变化 → 立即生效（撤销 Grant 并推送 stop/deny）
        from ..models import PlaybackGrant

        grant = (
            session.query(PlaybackGrant)
            .filter(PlaybackGrant.playback_id == playback.id,
                    PlaybackGrant.revoked_at.is_(None))
            .order_by(PlaybackGrant.created_at.desc())
            .first()
        )
        if grant is not None and grant.policy_version != version:
            return deny("policy_changed", {"refresh_required": True})
        scope_violation = self._scope_check(rules, media)
        if scope_violation:
            return deny(scope_violation, {"allowed_categories": self._scope_summary(rules)})

        # 硬截止：可观看时段结束 → 到点停止
        if not self._in_allowed_window(rules, now):
            return deny("outside_allowed_window", self._window_constraints(rules))

        # 软限制（时长/集数）不切断当前内容：allow（下一集由 may_start 拦截）
        return PolicyDecision(decision="allow", policy_version=version,
                              evaluated_at=now_iso())

    # ---------- get_family_policy 摘要（POL-009，v2 维度化） ----------

    def summary_for_child(self, session: Session, profile_id: str, now: datetime) -> dict:
        rules, version = self.current(session)
        out: dict = {"policy_version": version}
        remaining = self.budget_remaining(session, profile_id, rules, now,
                                          media=None,
                                          dims=("ENTERTAINMENT", "VIDEO"))
        if rules.screen_total_minutes() is not None:
            out["screen_total"] = {
                "limit_minutes": rules.screen_total_minutes(),
                "remaining_seconds": remaining.get("screen_total_seconds"),
            }
        if rules.audio_minutes() is not None:
            out["audio_limit"] = {
                "limit_minutes": rules.audio_minutes(),
                "remaining_seconds": remaining.get("audio_seconds"),
            }
        if rules.ai_voice_minutes() is not None:
            out["ai_voice_limit"] = {
                "limit_minutes": rules.ai_voice_minutes(),
                "remaining_seconds": remaining.get("ai_voice_seconds"),
            }
        by_class = (rules.budgets or {}).get("video_by_class") or {}
        if by_class:
            out["video_by_class"] = {
                cc: rules.video_class_minutes(cc) for cc in by_class
            }
        if rules.session_limit_minutes is not None:
            out["session_limit_minutes"] = rules.session_limit_minutes
        if rules.daily_episode_limit is not None:
            count = self.episodes_watched_today(session, profile_id, rules, now)
            out["episode_limit"] = {
                "limit": rules.daily_episode_limit,
                "remaining_episodes": max(0, rules.daily_episode_limit - count),
            }
        if rules.allowed_windows:
            out["allowed_windows"] = rules.allowed_windows
        out["autoplay"] = rules.autoplay
        if rules.content_scope:
            out["content_scope"] = {"allowed_media_types": self._scope_summary(rules)}
        out["offscreen_allowed"] = rules.offscreen_allowed()
        out["transition_policy"] = {
            "enabled": rules.transition_enabled(),
            "types": rules.transition_types(),
            "max_minutes": rules.transition_max_minutes(self._cfg),
            "daily_offer_limit": rules.transition_daily_offer_limit(),
        }
        return out

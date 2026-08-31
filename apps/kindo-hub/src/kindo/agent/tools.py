"""Agent / Tool Runtime（技术方案 §8）。

- LLM 只看到业务 Tool；device_id/profile_id/session_id 由 Runtime 从调用上下文注入。
- 写 Tool 幂等：相同 (session_id, tool_call_id) 重复执行读取第一次结果。
- play_media 内部必须再走 Policy → Playback → Grant；Tool 不把 Grant/stream URL 返回给 LLM。
- Tool 返回 data 做字段白名单（§8.4）。
"""
from __future__ import annotations

import logging
from datetime import UTC
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..errors import invalid_request
from ..grounding import grounding_window
from ..history.service import HistoryService
from ..media.catalog import public_media_fields
from ..media.catalog import search_media as catalog_search
from ..models import Device, Media, Playback
from ..playback.service import PlaybackService
from ..policy.engine import PolicyEngine

logger = logging.getLogger("kindo.tools")

VALID_STATUS = {"ok", "clarify", "denied", "not_found", "error"}


def _result(status: str, data: dict | None = None, reason_code: str | None = None,
            constraints: dict | None = None, message_hint: str | None = None) -> dict:
    return {
        "status": status, "data": data or {},
        "reason_code": reason_code, "constraints": constraints or {},
        "message_hint": message_hint,
    }


# ---------- 参数模型（strict；additionalProperties=false 语义） ----------

class SearchMediaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100)
    media_types: list[Literal["episode", "movie", "lesson", "song", "story"]] | None = None
    language: str | None = None
    tags: list[str] | None = Field(default=None, max_length=5)
    limit: int = Field(default=4, ge=1, le=8)


class GetMediaDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str


class GetPlaybackContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_grounding: bool = False


class CheckPlayPermissionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str
    action: str


class GetFamilyPolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[str] | None = Field(default=None, max_length=4)


class PlayMediaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_id: str
    action: str
    start_position_ms: int | None = Field(default=None, ge=0)


class ControlPlaybackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    position_ms: int | None = Field(default=None, ge=0)


class GetWatchHistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str | None = None
    limit: int = Field(default=5, ge=1, le=10)


class GetCourseProgressArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str | None = None


class RecommendMediaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str | None = None
    limit: int = Field(default=4, ge=1, le=8)


# ---------- §8.3 JSON Schema（发送给 LLM 的 tools 定义） ----------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "search_media",
        "description": "Search the family media catalog: videos, lessons, and audio (songs/stories).",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 100},
                "media_types": {"type": "array", "items": {"enum": ["episode", "movie", "lesson", "song", "story"]}, "maxItems": 5},
                "language": {"type": ["string", "null"]},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
            "required": ["query"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_media_detail",
        "description": "Get media detail with series/course structure.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"media_id": {"type": "string"}},
            "required": ["media_id"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_playback_context",
        "description": "Get current or recent playback context, optionally with subtitle grounding window.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"include_grounding": {"type": "boolean", "default": False}},
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "check_play_permission",
        "description": "Pre-check whether playing a media is allowed. Does NOT replace the server-side check inside play_media.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "media_id": {"type": "string"},
                "action": {"enum": ["play", "resume", "next", "course_continue"]},
            },
            "required": ["media_id", "action"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_family_policy",
        "description": "Return a summary of family viewing policy for explaining rules to the child.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {"enum": ["daily_limit", "session_limit", "episode_limit",
                                       "allowed_window", "content_scope", "autoplay"]},
                    "maxItems": 4,
                }
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "play_media",
        "description": "Start/resume/continue playback of a family media item. Server re-checks Family Policy internally.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "media_id": {"type": "string"},
                "action": {"enum": ["play", "resume", "next", "course_continue"]},
                "start_position_ms": {"type": ["integer", "null"], "minimum": 0},
            },
            "required": ["media_id", "action"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "control_playback",
        "description": "Control current playback: pause/stop/seek. Resume must go through play_media(action=resume).",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "action": {"enum": ["pause", "stop", "seek"]},
                "position_ms": {"type": ["integer", "null"], "minimum": 0},
            },
            "required": ["action"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_watch_history",
        "description": "Get recent watch history (limited count).",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "scope": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_course_progress",
        "description": "Get course progress: last lesson and position for continue-learning.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"course_id": {"type": ["string", "null"]}},
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "recommend_media",
        "description": "Recommend media from the family catalog only, pre-filtered by content scope.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "topic": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_transition_options",
        "description": "Read current growth transition availability: allowed types, remaining budgets and offers today. Read-only.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {},
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_related_topics",
        "description": "List topics related to recent interest signals or a given entity. Read-only.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "entity_id": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "find_audio_content",
        "description": "Find AUDIO-modality content (stories/songs). Read-only. "
                    "The media_id in each item (when present) can be passed to play_media.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "query": {"type": ["string", "null"]},
                "topic": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 4, "default": 4},
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "suggest_offscreen_activity",
        "description": "Suggest an off-screen activity. Library first; generated suggestions are draft-only. Read-only.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "topic": {"type": ["string", "null"]},
            },
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_story",
        "description": "Read a family story aloud with the parent's recorded voice "
                       "(falls back to system voice). Use when the child asks to hear "
                       "a story read/told (讲故事/念故事/用爸爸妈妈的声音讲). "
                       "The full text is spoken directly by the system; do not repeat it. "
                       "Prefer search_media+play_media for original audio recordings.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "query": {"type": ["string", "null"], "maxLength": 100},
            },
        },
        "strict": True,
    },
]

_CHILD_STATUS = {
    "search_media": "正在找动画",
    "get_media_detail": "正在看看这个内容",
    "get_playback_context": "正在看看现在看到哪儿了",
    "check_play_permission": "正在检查今天还能不能看",
    "get_family_policy": "正在看看家里的规则",
    "play_media": "准备播放",
    "control_playback": "正在操作播放",
    "get_watch_history": "正在看看最近看了什么",
    "get_course_progress": "正在看看学到哪儿了",
    "recommend_media": "正在挑一些好看的",
    "get_transition_options": "正在看看还能做什么",
    "get_related_topics": "正在想想相关的主题",
    "find_audio_content": "正在找好听的故事和儿歌",
    "suggest_offscreen_activity": "正在想一个好玩的活动",
    "read_story": "正在翻开故事书",
}


class ToolRuntime:
    def __init__(self, db_session_factory, policy: PolicyEngine, playback: PlaybackService,
                 history: HistoryService):
        self._db = db_session_factory
        self._policy = policy
        self._playback = playback
        self._history = history

    @staticmethod
    def child_friendly_status(tool_name: str) -> str:
        return _CHILD_STATUS.get(tool_name, "正在处理")

    def execute(self, conv_session, device: Device, profile_id: str,
                tool_name: str, raw_args: dict, tool_call_id: str) -> dict:
        """conv_session: ConversationSession（用于幂等缓存与候选集）。"""
        # 幂等（§8.4）：相同 session + tool_call_id 直接读首次结果
        cache_key = f"{conv_session.session_id}:{tool_call_id}"
        if cache_key in conv_session.tool_call_cache:
            return conv_session.tool_call_cache[cache_key]

        result = self._dispatch(conv_session, device, profile_id, tool_name, raw_args)
        conv_session.tool_call_cache[cache_key] = result
        conv_session.recent_tool_results.append({
            "tool": tool_name, "status": result["status"], "data": result["data"],
        })
        return result

    def _dispatch(self, conv_session, device: Device, profile_id: str,
                  tool_name: str, raw_args: dict) -> dict:
        try:
            with self._db() as session:
                if tool_name == "search_media":
                    return self._search(conv_session, session, SearchMediaArgs(**raw_args))
                if tool_name == "get_media_detail":
                    return self._detail(session, profile_id, GetMediaDetailArgs(**raw_args))
                if tool_name == "get_playback_context":
                    return self._playback_context(
                        session, profile_id, GetPlaybackContextArgs(**raw_args))
                if tool_name == "check_play_permission":
                    return self._check_permission(session, profile_id, CheckPlayPermissionArgs(**raw_args))
                if tool_name == "get_family_policy":
                    return self._family_policy(session, profile_id, GetFamilyPolicyArgs(**raw_args))
                if tool_name == "play_media":
                    return self._play(conv_session, device, session, PlayMediaArgs(**raw_args))
                if tool_name == "control_playback":
                    return self._control(session, device, ControlPlaybackArgs(**raw_args))
                if tool_name == "get_watch_history":
                    return self._history_tool(session, profile_id, GetWatchHistoryArgs(**raw_args))
                if tool_name == "get_course_progress":
                    return self._course_progress(session, profile_id, GetCourseProgressArgs(**raw_args))
                if tool_name == "recommend_media":
                    return self._recommend(session, profile_id, RecommendMediaArgs(**raw_args))
                if tool_name == "get_transition_options":
                    return self._transition_options(session, profile_id)
                if tool_name == "get_related_topics":
                    return self._related_topics(session, profile_id, raw_args)
                if tool_name == "find_audio_content":
                    return self._find_audio(session, raw_args)
                if tool_name == "suggest_offscreen_activity":
                    return self._suggest_activity(session, profile_id, raw_args)
                if tool_name == "read_story":
                    return self._read_story(session, profile_id, raw_args)
                raise invalid_request(f"未知 Tool: {tool_name}")
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in ("invalid_request",):
                logger.warning("Tool 参数校验失败 %s: %s", tool_name, exc)
                return _result("error", message_hint="工具参数不合法")
            if code == "not_found":
                return _result("not_found", message_hint="没有找到对应的内容")
            if code == "policy_denied":
                return _result(
                    "denied",
                    reason_code=getattr(exc, "reason_code", None),
                    constraints=getattr(exc, "constraints", None) or {},
                )
            logger.exception("Tool 执行异常 %s", tool_name)
            return _result("error", message_hint="工具暂时出了点问题")

    # ---------- 各 Tool ----------

    def _search(self, conv_session, session: Session, args: SearchMediaArgs) -> dict:
        media_types: list[str] | None = (
            list(args.media_types) if args.media_types else None)
        rows, _cursor = catalog_search(
            session, args.query, media_types=media_types, language=args.language,
            tags=args.tags, limit=args.limit,
        )
        if not rows:
            return _result("not_found", message_hint="家里的媒体库没有找到相关内容")
        options = []
        for i, m in enumerate(rows):
            option_id = f"opt{i + 1}"
            options.append({
                "option_id": option_id,
                "label": m.title,
                "media_id": m.id,
            })
        conv_session.register_candidates(options, source_tool="search_media")
        return _result(
            "ok" if len(rows) == 1 else "clarify",
            data={"candidates": [public_media_fields(m) | {"option_id": o["option_id"]}
                                 for m, o in zip(rows, options, strict=False)]},
        )

    def _detail(self, session: Session, profile_id: str, args: GetMediaDetailArgs) -> dict:
        from ..media.catalog import get_media

        media = get_media(session, args.media_id)
        if media is None or media.missing:
            return _result("not_found", message_hint="没有找到这个内容")
        d = {
            "media_id": media.id, "title": media.title, "media_type": media.media_type,
            "duration_ms": media.duration_ms, "language": media.language,
            "age_band": media.age_band, "tags": media.tags_json or {},
            "playable": media.playable,
        }
        detail = None
        from ..models import Course, Episode, Lesson, Series

        ep = session.query(Episode).filter(Episode.media_id == media.id).one_or_none()
        if ep:
            s = session.get(Series, ep.series_id)
            d["series"] = {"title": s.title if s else None, "season_no": ep.season_no,
                           "episode_no": ep.episode_no}
        ls = session.query(Lesson).filter(Lesson.media_id == media.id).one_or_none()
        if ls:
            c = session.get(Course, ls.course_id)
            d["course"] = {"title": c.title if c else None, "chapter_no": ls.chapter_no,
                           "lesson_no": ls.lesson_no}
        detail = d
        return _result("ok", data=detail)

    def _playback_context(self, session: Session, profile_id: str,
                          args: GetPlaybackContextArgs) -> dict:
        pb = self._playback.current_playback(session, profile_id)
        if pb is None:
            return _result("ok", data={"playback": None})
        media = session.get(Media, pb.media_id)
        if media is None:
            return _result("ok", data={"playback": None})
        data: dict[str, object] = {
            "playback": {
                "playback_id": pb.id,
                "media_id": media.id,
                "title": media.title,
                "media_type": media.media_type,
                "position_ms": pb.position_ms,
                "duration_ms": media.duration_ms,
                "state": pb.state,
                "language": media.language,
                "audio_track_id": pb.audio_track_id,
                "subtitle_track_id": pb.subtitle_track_id,
            }
        }
        if args.include_grounding and pb.state == "playing":
            g = grounding_window(session, pb, media)
            from ..grounding import wrap_untrusted

            data["grounding"] = wrap_untrusted(g)
            data["grounding_quality"] = g.get("grounding_quality")
            data["grounding_missing"] = g.get("grounding_missing", False)
        return _result("ok", data=data)

    def _check_permission(self, session: Session, profile_id: str,
                          args: CheckPlayPermissionArgs) -> dict:
        media = session.get(Media, args.media_id)
        if media is None or media.missing:
            return _result("not_found", message_hint="没有找到这个内容")
        from datetime import datetime

        decision = self._policy.may_start(
            session, profile_id, media, args.action, datetime.now(UTC),
            self._playback.current_playback(session, profile_id),
        )
        return _result(
            "ok" if decision.allowed else "denied",
            data={"decision": decision.decision, "media_id": media.id, "action": args.action,
                  "policy_version": decision.policy_version, "evaluated_at": decision.evaluated_at},
            reason_code=decision.reason_code,
            constraints=decision.constraints,
            message_hint=None if decision.allowed else "预检查未通过；播放时服务端会再次校验",
        )

    def _family_policy(self, session: Session, profile_id: str, args: GetFamilyPolicyArgs) -> dict:
        from datetime import datetime

        summary = self._policy.summary_for_child(session, profile_id, datetime.now(UTC))
        if args.topics:
            keep = set(args.topics)
            summary = {k: v for k, v in summary.items() if k in keep or k == "policy_version"}
        return _result("ok", data={"policy": summary})

    def _play(self, conv_session, device: Device, session: Session, args: PlayMediaArgs) -> dict:
        media = session.get(Media, args.media_id)
        if media is None or media.missing:
            return _result("not_found", message_hint="没有找到这个内容")
        # play_media 即使模型已先调用 check_play_permission，也必须再次进入 Policy（§8.4）
        pb, decision, token = self._playback.request_playback(
            session, device, media, args.action, args.start_position_ms,
            source="ai", idempotency_key=f"{conv_session.session_id}:{args.media_id}:{args.action}"
            if args.action != "play" else None,
        )
        if not decision.allowed:
            return _result(
                "denied",
                data={"media_id": media.id, "action": args.action},
                reason_code=decision.reason_code,
                constraints=decision.constraints,
                message_hint="家庭规则没有允许这次播放",
            )
        assert pb is not None  # deny 分支已提前返回
        if decision.allowed and token is not None:
            # Grant 只随 Realtime 播放命令发给 TV（设备通道），绝不进入 Tool 结果/LLM（§8.2）
            descriptor = self._playback.stream_descriptor(session, pb, token)
            self._notify_tv_playback(device.id, pb, conv_session.session_id, descriptor)
        # 注意：Grant 与 stream URL 永不进入 Tool 返回（§8.2 play_media 关键保证）
        return _result("ok", data={
            "playback_id": pb.id, "media_id": media.id, "title": media.title,
            "action": args.action, "state": pb.state,
        })

    def _notify_tv_playback(self, device_id: str, pb: Playback, session_id: str,
                            descriptor: dict | None = None) -> None:
        """play_media 成功后经 Realtime 下发 playback.command（TV 凭 descriptor 拉流）。"""
        if self._notifier:
            payload = {
                "command_id": f"cmd_{pb.id[:8]}",
                "action": "start",
                "playback_id": pb.id,
                "media_id": pb.media_id,
                "position_ms": pb.position_ms,
            }
            if descriptor is not None:
                payload["stream_descriptor"] = descriptor
            self._notifier(
                device_id=device_id, event_type="playback.command",
                payload=payload,
                session_id=session_id, playback_id=pb.id,
            )

    # ---------- Growth Transition Tools（v0.3 §8，阶段 4b） ----------

    def _transition_options(self, session: Session, profile_id: str) -> dict:
        """当前允许的接力类型 + 各维度剩余 + 当日剩余次数（仅接力场景读）。"""
        from datetime import UTC, datetime

        rules, _v = self._policy.current(session)
        now = datetime.now(UTC)
        remaining = self._policy.budget_remaining(
            session, profile_id, rules, now, dims=("ENTERTAINMENT", "VIDEO"))
        day_start, _ = self._policy._local_day_bounds(now)
        from ..models import TransitionSession

        offered = (
            session.query(TransitionSession)
            .filter(TransitionSession.profile_id == profile_id,
                    TransitionSession.created_at >= day_start).count()
        )
        return _result("ok", data={
            "enabled": rules.transition_enabled(),
            "types": rules.transition_types(),
            "max_minutes": rules.transition_max_minutes(),
            "remaining_today": max(0, rules.transition_daily_offer_limit() - offered),
            "budget_remaining": remaining,
            "offscreen_allowed": rules.offscreen_allowed(),
        })

    def _related_topics(self, session: Session, profile_id: str, args: dict) -> dict:
        """相关主题：content_topic + 兴趣信号派生（决策九，客观事实）。"""
        limit = max(1, min(int(args.get("limit") or 5), 5))
        from datetime import UTC, datetime, timedelta

        from ..models import ContentTopic, EntityTopic, InterestSignal

        since = datetime.now(UTC) - timedelta(days=30)
        rows = (
            session.query(ContentTopic.name,
                          __import__("sqlalchemy").func.count(InterestSignal.id))
            .join(InterestSignal, InterestSignal.topic_id == ContentTopic.id)
            .filter(InterestSignal.profile_id == profile_id,
                    InterestSignal.created_at >= since)
            .group_by(ContentTopic.name)
            .order_by(__import__("sqlalchemy").func.count(InterestSignal.id).desc())
            .limit(limit)
            .all()
        )
        topics = [r[0] for r in rows]
        if not topics:
            ent_id = args.get("entity_id")
            if ent_id:
                topics = [r[0] for r in (
                    session.query(ContentTopic.name)
                    .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
                    .filter(EntityTopic.entity_id == ent_id)
                    .limit(limit).all())]
        return _result("ok", data={"topics": topics})

    def _find_audio(self, session: Session, args: dict) -> dict:
        """modality=AUDIO 内容检索（故事/儿歌，接力与音频点播共用）。"""
        query = (args.get("query") or "").strip()
        topic = (args.get("topic") or "").strip()
        limit = max(1, min(int(args.get("limit") or 4), 4))

        from ..models import ContentEntity, ContentTopic, EntityAsset, EntityTopic

        q = (session.query(ContentEntity)
             .filter(ContentEntity.modality == "AUDIO"))
        if query:
            q = q.filter(ContentEntity.title.ilike(f"%{query}%"))
        if topic:
            q = q.join(EntityTopic, EntityTopic.entity_id == ContentEntity.id
                       ).join(ContentTopic, ContentTopic.id == EntityTopic.topic_id
                              ).filter(ContentTopic.name.ilike(f"%{topic}%"))
        rows = q.order_by(ContentEntity.title).limit(limit).all()
        # 附带可播放的 media_id（EntityAsset.asset_id）：LLM 可直接交给 play_media
        links = (session.query(EntityAsset)
                 .filter(EntityAsset.entity_id.in_([r.id for r in rows])).all()
                 if rows else [])
        asset_of = {lk.entity_id: lk.asset_id for lk in links}
        return _result("ok", data={"items": [
            {"entity_id": r.id, "media_id": asset_of.get(r.id), "title": r.title,
             "entity_type": r.entity_type, "duration_ms": r.duration_ms}
            for r in rows
        ]})

    def _suggest_activity(self, session: Session, profile_id: str, args: dict) -> dict:
        """离屏活动建议：库（preset/published）优先；库缺 AI 生成 draft（不入池）。"""
        topic = (args.get("topic") or "").strip()
        from ..models import TransitionActivity

        pool = (session.query(TransitionActivity)
                .filter(TransitionActivity.status.in_(("preset", "published")))
                .all())
        if topic:
            matched = [a for a in pool if topic in (a.topics_json or [])]
            if matched:
                pool = matched
        if pool:
            a = pool[0]
            return _result("ok", data={
                "source": a.source, "status": a.status, "activity_id": a.id,
                "title": a.title, "summary": a.summary,
            })
        # 库缺：AI 生成 draft 建议（决策七 7.3：本次可用，不入推荐池）
        title = f"去发现身边的{topic or '有趣东西'}"
        return _result("ok", data={
            "source": "generated", "status": "draft",
            "title": title,
            "summary": "和爸爸妈妈一起，在家里找一个和刚才故事有关的东西，"
                       "说说它像什么、能用来做什么。",
        })

    def _read_story(self, session: Session, profile_id: str, args: dict) -> dict:
        """朗读故事（§7.4 story_text）：返回 direct_speak 结果——编排器直接
        分句播报原文（家长声音克隆优先、系统 TTS 兜底），原文不经 LLM 复述、
        不进入模型上下文（非可信内容数据只作朗读素材）。"""
        from ..models import ContentEntity, ContentTopic, EntityTopic, InterestSignal

        query = (args.get("query") or "").strip()
        stories = (
            session.query(ContentEntity)
            .filter(ContentEntity.entity_type == "story",
                    ContentEntity.story_text.isnot(None))
            .order_by(ContentEntity.title)
            .all()
        )
        if not stories:
            return _result("not_found", message_hint="家里还没有能读的故事文本")

        target = None
        if query:
            target = next(
                (e for e in stories if query in (e.title or "")), None)
        if target is None and query:
            # 标题未命中 → 主题命中（实体 topics）
            topic_rows = (
                session.query(EntityTopic.entity_id, ContentTopic.name)
                .join(ContentTopic, ContentTopic.id == EntityTopic.topic_id)
                .filter(EntityTopic.entity_id.in_([e.id for e in stories]))
                .all())
            by_topic = [e for e in stories if any(
                eid == e.id and query in name for eid, name in topic_rows)]
            if len(by_topic) == 1:
                target = by_topic[0]
            elif by_topic:
                return _result("clarify", data={"candidates": [
                    {"title": e.title} for e in by_topic[:4]]})
        if target is None and not query:
            # 无偏好：取近期兴趣信号最近接触的故事，缺省第一个
            recent = (
                session.query(InterestSignal.entity_id)
                .filter(InterestSignal.entity_id.in_([e.id for e in stories]))
                .order_by(InterestSignal.created_at.desc())
                .limit(1).scalar())
            target = next((e for e in stories if e.id == recent), stories[0])
        if target is None:
            return _result("clarify", data={"candidates": [
                {"title": e.title} for e in stories[:4]]})
        return _result("ok", data={
            "direct_speak": True,
            "entity_id": target.id,
            "title": target.title,
            "speak_text": (target.story_text or "").strip(),
        })

    _notifier = None

    def set_notifier(self, notifier) -> None:
        self._notifier = notifier

    def _control(self, session: Session, device: Device, args: ControlPlaybackArgs) -> dict:
        profile_id = self._playback.default_profile_id(session)
        pb = self._playback.current_playback(session, profile_id)
        if pb is None:
            return _result("not_found", message_hint="现在没有正在播放的内容")
        try:
            result = self._playback.control(session, device, pb.id, args.action, args.position_ms)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == "conflict":
                return _result("error", message_hint="请改用 play_media(resume) 恢复播放")
            raise
        return _result("ok", data=result)

    def _history_tool(self, session: Session, profile_id: str, args: GetWatchHistoryArgs) -> dict:
        items = self._history.continue_watching(session, profile_id, limit=args.limit)
        return _result("ok", data={"history": items})

    def _course_progress(self, session: Session, profile_id: str, args: GetCourseProgressArgs) -> dict:
        items = self._history.continue_learning(session, profile_id, limit=5)
        if args.course_id:
            items = [i for i in items if i.get("course_id") == args.course_id]
        return _result("ok", data={"progress": items})

    def _recommend(self, session: Session, profile_id: str, args: RecommendMediaArgs) -> dict:
        rows, _cursor = (
            catalog_search(session, args.topic or "", limit=args.limit)
            if args.topic else ([], None)
        )
        if not rows:
            from ..media.catalog import list_media

            rows, _flat_cursor = list_media(session, limit=args.limit)
        return _result("ok", data={"candidates": [public_media_fields(m) for m in rows]})

"""TV / Hub 资源与命令接口（技术方案 §3.1）。"""
from __future__ import annotations

from datetime import UTC, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..errors import grant_invalid, invalid_request, not_found, policy_denied
from ..media.catalog import admin_collections, get_media, list_media, media_detail
from ..models import ContentEntity, Device, InterestSignal, Media, Playback, SubtitleSegment, SubtitleTrack
from ..util import now_iso, now_utc
from .deps import RangeUnsatisfiable, device_from_request, get_db, get_state, parse_range_header

router = APIRouter(prefix="/api/v1", tags=["tv"])

CHUNK_SIZE = 256 * 1024


class CreateConversationBody(BaseModel):
    resume_session_id: str | None = None
    ui_context: dict | None = None


class PlaybackBody(BaseModel):
    media_id: str
    action: str = Field(pattern="^(play|resume|next|course_continue)$")
    start_position_ms: int | None = Field(default=None, ge=0)
    source: str = "ui"


class ControlBody(BaseModel):
    action: str = Field(pattern="^(pause|resume|seek|stop)$")
    position_ms: int | None = Field(default=None, ge=0)


@router.get("/bootstrap")
async def bootstrap(request: Request, device: Device = Depends(device_from_request),
                    session: Session = Depends(get_db)):
    state = get_state(request)
    asr_health = await state.asr.health()
    ai_available = state.provider_registry.configured_count > 0
    return {
        "instance_id": state.instance_id,
        "display_name": state.config.instance_display_name,
        "server_time": now_iso(),
        "api_version": 1,
        "device": {"device_id": device.id, "name": device.name,
                   "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None},
        "capabilities": {
            "voice_available": bool(asr_health.get("ready")),
            "ai_available": ai_available,
            "tts_available": True,  # V0.1 Android 系统 TTS（TV 端执行）
        },
    }


@router.get("/home")
def home(request: Request, device: Device = Depends(device_from_request),
         session: Session = Depends(get_db)):
    state = get_state(request)
    profile_id = state.playback.default_profile_id(session)
    rules, _v = state.policy.current(session)

    # 内容范围过滤后的可探索主题（来自本地媒体标签，交互 §4.2）
    medias, _cursor = list_media(session, limit=200)
    scope = rules.content_scope or {}
    blocked = set(scope.get("blocked_tags") or [])
    allowed_types = set(scope.get("allowed_media_types") or [])
    allowed_mounts = set(scope.get("allowed_mount_ids") or [])

    def visible(m: Media) -> bool:
        if allowed_types and m.media_type not in allowed_types:
            return False
        if allowed_mounts and m.mount_id not in allowed_mounts:
            return False
        tags = m.tags_json or {}
        if blocked & set(tags.get("characters", []) + tags.get("themes", []) + tags.get("tags", [])):
            return False
        return True

    themes: list[str] = []
    for m in medias:
        if visible(m):
            for t in (m.tags_json or {}).get("themes", []):
                if t not in themes:
                    themes.append(t)
    # 兴趣反哺（REC-002/ANA-004）：可探索主题按孩子近 30 天兴趣信号频次前置
    # 排序（selected/watched/transition_joined 客观计数；transition_rejected 不计；
    # 同频次保持原序）——表述为使用行为排序，不引入推断标签
    from datetime import datetime

    cutoff = datetime.now(UTC) - timedelta(days=30)
    visible_ids = [m.id for m in medias if visible(m)]
    ent_rows = (
        session.query(ContentEntity.id, ContentEntity.source_media_id)
        .filter(ContentEntity.source_media_id.in_(visible_ids))
        .all()
        if visible_ids else []
    )
    ent_counts: dict[str, int] = {}
    if ent_rows:
        rows = (
            session.query(InterestSignal.entity_id, func.count(InterestSignal.id))
            .filter(InterestSignal.profile_id == profile_id,
                    InterestSignal.entity_id.in_([e for e, _m in ent_rows]),
                    InterestSignal.created_at >= cutoff,
                    InterestSignal.signal_type != "transition_rejected")
            .group_by(InterestSignal.entity_id)
            .all()
        )
        ent_counts = {eid: int(cnt) for eid, cnt in rows}
    media_to_ent = {mid: eid for eid, mid in ent_rows}
    theme_weight: dict[str, int] = {}
    for m in medias:
        if not visible(m):
            continue
        weight = ent_counts.get(media_to_ent.get(m.id, ""), 0)
        if weight:
            for t in (m.tags_json or {}).get("themes", []):
                theme_weight[t] = theme_weight.get(t, 0) + weight
    if theme_weight:
        themes.sort(key=lambda t: -theme_weight.get(t, 0))
    # 断点行按媒介拆分（交互 §4.2：继续观看 / 继续收听分列，音频不混入视频行）
    continue_all = [i for i in state.history.continue_watching(session, profile_id, limit=30)
                    if _visible_by_id(session, i["media_id"], visible)]
    continue_items = [i for i in continue_all
                      if i["media_type"] not in ("song", "story")][:6]
    continue_listening = [i for i in continue_all
                          if i["media_type"] in ("song", "story")][:6]
    learning = state.history.continue_learning(session, profile_id)
    return {
        "profile_id": profile_id,
        "continue_watching": continue_items,
        "continue_listening": continue_listening,
        "continue_learning": learning,
        "explore_themes": themes[:12],
        "recent_series": state.history.recent_series(session, profile_id),
    }


def _visible_by_id(session: Session, media_id: str, visible_fn) -> bool:
    m = session.get(Media, media_id)
    return m is not None and visible_fn(m)


@router.get("/media")
def media_list(request: Request, device: Device = Depends(device_from_request),
               session: Session = Depends(get_db),
               query: str | None = None, type: str | None = None,
               language: str | None = None, tag: str | None = None,
               series_id: str | None = None,
               cursor: str | None = None, limit: int = 20):
    limit = max(1, min(limit, 100))
    if query:
        from ..media.catalog import search_media

        cur = None
        if cursor:
            cur_title, _, cur_id = cursor.rpartition("\x1f")
            if cur_title and cur_id:
                cur = (cur_title, cur_id)
        rows, next_cursor = search_media(
            session, query, media_types=[type] if type else None,
            language=language, tags=[tag] if tag else None, limit=limit, cursor=cur,
        )
        items = [_media_summary(m) for m in rows]
        encoded = f"{next_cursor[0]}\x1f{next_cursor[1]}" if next_cursor else None
        return {"items": items, "next_cursor": encoded}
    if series_id:
        # 系列内集列表：按季/集号排序，offset 翻页（cursor 即页码，调用方透传）。
        # 附集号与断点/完看（2026-08-27：TV 集网格以"第 N 集"大数字卡呈现，
        # 不再展示完整文件名——文件名是家长的归档习惯，不是孩子能读的语言）
        try:
            page = max(0, int(cursor)) if cursor else 0
        except ValueError:
            page = 0
        from ..models import Episode, WatchHistory

        profile_id = get_state(request).playback.default_profile_id(session)
        srows = (
            session.query(Media, Episode.episode_no,
                          WatchHistory.last_position_ms, WatchHistory.completed)
            .join(Episode, Episode.media_id == Media.id)
            .outerjoin(WatchHistory, (WatchHistory.media_id == Media.id) &
                                       (WatchHistory.profile_id == profile_id))
            .filter(Episode.series_id == series_id, Media.missing.is_(False))
            .order_by(Episode.season_no, Episode.episode_no, Media.title)
            .offset(page * limit).limit(limit).all()
        )
        items = []
        for m, ep_no, pos, completed in srows:
            d = _media_summary(m)
            d["episode_no"] = ep_no
            d["last_position_ms"] = pos or 0
            d["completed"] = bool(completed)
            items.append(d)
        paged_cursor: str | None = str(page + 1) if len(srows) == limit else None
        return {"items": items, "next_cursor": paged_cursor}
    rows, flat_cursor = list_media(
        session, media_type=type, language=language, tag=tag,
        series_id=None, cursor=cursor, limit=limit,
    )
    return {"items": [_media_summary(m) for m in rows], "next_cursor": flat_cursor}


@router.get("/collections")
def collections(request: Request, device: Device = Depends(device_from_request),
                session: Session = Depends(get_db)):
    """系列/课程聚合（TV 按合集浏览，2026-08-21；镜像 Admin 聚合语义）。"""
    return admin_collections(session)


@router.get("/entities/{entity_id}/poster")
def entity_poster(entity_id: str, request: Request,
                  device: Device = Depends(device_from_request),
                  session: Session = Depends(get_db)):
    """实体级 Series poster（v0.3 MED-013：系列卡优先 Series poster；
    无实体图时回退默认海报，与媒体级海报端点同一占位语义）。"""
    from ..media.posters import default_poster
    from ..models import ArtworkAsset, ContentEntity

    entity = session.get(ContentEntity, entity_id)
    if entity is None:
        raise not_found("内容不存在")
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == "poster")
           .one_or_none())
    if row is not None:
        path = Path(get_state(request).config.data_dir) / row.file_path
        if path.is_file():
            return FileResponse(
                path, media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=3600"})
    try:
        return FileResponse(
            default_poster(get_state(request).config, seed=entity_id),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except FileNotFoundError:
        raise not_found("该内容暂无海报") from None


def _media_summary(m: Media) -> dict:
    return {
        "media_id": m.id, "title": m.title, "media_type": m.media_type,
        "duration_ms": m.duration_ms, "language": m.language,
        "age_band": m.age_band, "tags": m.tags_json or {},
        "playable": m.playable, "metadata_version": m.metadata_version,
        "has_poster": m.has_poster,
    }


@router.get("/media/{media_id}/poster")
def media_poster(media_id: str, request: Request,
                 device: Device = Depends(device_from_request),
                 session: Session = Depends(get_db)):
    """TV 儿童端海报（2026-08-21 决策：媒体库在 TV 展示；镜像 Admin 缓存语义 §13.2）。
    无真实海报时回退默认海报（产品决策：占位统一视觉，has_poster 语义不变）。"""
    from ..media.posters import default_poster, poster_path, poster_ready

    media = session.get(Media, media_id)
    if media is None or media.missing:
        raise not_found("媒体不存在")
    if poster_ready(get_state(request).config, media_id):
        return FileResponse(
            poster_path(get_state(request).config, media_id),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    # 集级无自有海报 → 系列实体海报（MED-013 海报来源一致的 URL 级落实，
    # 2026-08-27：系列集网格等所有走该 URL 的消费方统一获得系列图）
    from ..media.content_catalog import series_poster_file

    series_poster = series_poster_file(
        session, get_state(request).config.data_dir, media_id)
    if series_poster is not None:
        return FileResponse(
            series_poster, media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"})
    try:
        return FileResponse(
            default_poster(get_state(request).config, seed=media_id),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except FileNotFoundError:
        raise not_found("该媒体暂无海报") from None


@router.get("/media/{media_id}")
def media_get(request: Request, media_id: str,
              device: Device = Depends(device_from_request), session: Session = Depends(get_db)):
    state = get_state(request)
    media = get_media(session, media_id)
    if media is None or media.missing:
        raise not_found("媒体不存在")
    profile_id = state.playback.default_profile_id(session)
    detail = media_detail(session, media, profile_id)
    # 可执行动作摘要：Policy 预检查（§3.1）
    from datetime import datetime

    decision = state.policy.may_start(
        session, profile_id, media, "play", datetime.now(UTC),
        state.playback.current_playback(session, profile_id),
    )
    detail["actions"] = {
        # 维度化预检数据（交互 v0.3 §7.3：TV 本地按媒介/分类给儿童提示）
        "play": {
            "allowed": decision.allowed, "reason_code": decision.reason_code,
            "constraints": decision.constraints or {},
        },
    }
    return detail


# ---------- Conversation（§3.1） ----------

@router.post("/conversations")
def conversations_create(request: Request, body: CreateConversationBody,
                        device: Device = Depends(device_from_request),
                        session: Session = Depends(get_db)):
    state = get_state(request)
    provider_id, model_id = state.active_model()
    if provider_id is None or state.provider_registry.get(provider_id) is None:
        from ..errors import provider_unavailable

        raise provider_unavailable("未配置 LLM Provider，AI 对话不可用")
    profile_id = state.playback.default_profile_id(session)
    if not body.resume_session_id:
        # §9.2 判定矩阵 AI_VOICE 分支：预算尽拒新对话（resume 不设门=软限制
        # 不切断进行中；transition 时间盒短，同样经此端点时若被拒按文案降级）
        decision = state.policy.may_start_ai_voice(session, profile_id, now_utc())
        if decision.decision == "deny":
            from ..errors import policy_denied

            raise policy_denied(
                decision.reason_code or "daily_limit_reached",
                decision.constraints or {}, "今天的 AI 聊天时间用完啦，明天再来吧")
    conv = state.conversation_manager.create(
        device.id, profile_id, provider_id, model_id, body.resume_session_id
    )
    return state.conversation_manager.snapshot(conv)


@router.get("/conversations/{session_id}")
def conversations_get(session_id: str, request: Request,
                      device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    state = get_state(request)
    conv = state.conversation_manager.get_for_device(session_id, device.id)
    return state.conversation_manager.snapshot(conv)


@router.post("/conversations/{session_id}/end")
def conversations_end(session_id: str, request: Request,
                      device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    state = get_state(request)
    state.conversation_manager.get_for_device(session_id, device.id)
    state.conversation_manager.end(session_id)
    return {"session_id": session_id, "state": "ended"}


# ---------- TTS 音频拉取（§6.7 hub_tts） ----------

@router.get("/tts/{tts_id}/audio")
def tts_audio(tts_id: str, request: Request,
              device: Device = Depends(device_from_request),
              session: Session = Depends(get_db)):
    """hub_tts 合成音频：tts_id 一次性短生命周期，未命中/过期 404（TV 回退系统 TTS）。"""
    state = get_state(request)
    wav = state.tts.get_audio(tts_id)
    if wav is None:
        raise not_found("TTS 音频不存在或已过期")
    return Response(content=wav, media_type="audio/wav")


# ---------- Playback（§3.1） ----------

@router.post("/playbacks")
def playbacks_create(request: Request, body: PlaybackBody,
                     device: Device = Depends(device_from_request),
                     session: Session = Depends(get_db),
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    state = get_state(request)
    media = get_media(session, body.media_id)
    if media is None or media.missing:
        raise not_found("媒体不存在")
    try:
        state.storage.get(media.mount_id)
    except KeyError:
        raise invalid_request("媒体所属挂载已停用或删除，请重新挂载后扫描") from None
    pb, decision, grant_token = state.playback.request_playback(
        session, device, media, body.action, body.start_position_ms,
        body.source, idempotency_key,
    )
    if not decision.allowed:
        raise policy_denied(
            decision.reason_code or "policy_denied", decision.constraints,
            "playback is not allowed",
        )
    return {
        "playback_id": pb.id,
        "decision": "allow",
        "stream_descriptor": state.playback.stream_descriptor(session, pb, grant_token),
    }


@router.get("/playbacks/current")
def playbacks_current(request: Request, device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    state = get_state(request)
    profile_id = state.playback.default_profile_id(session)
    pb = state.playback.current_playback(session, profile_id)
    if pb is None:
        return {"playback": None}
    media = session.get(Media, pb.media_id)
    return {
        "playback": {
            "playback_id": pb.id, "media_id": pb.media_id,
            "title": media.title if media else None,
            "state": pb.state, "position_ms": pb.position_ms,
            "duration_ms": media.duration_ms if media else 0,
            "audio_track_id": pb.audio_track_id,
            "subtitle_track_id": pb.subtitle_track_id,
        }
    }


@router.post("/playbacks/{playback_id}/regrant")
def playbacks_regrant(playback_id: str, request: Request,
                      device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    """接力 audio handoff 等 WS 事件的 REST 兜底：TV 错过 playback.command 时，
    对自己设备名下的活跃 playback 重发一个新 Grant（旧 Grant 同时作废——
    同一 playback 任一时刻只有一个有效 Grant，无 TTL、无续签语义不变）。"""
    state = get_state(request)
    pb, token = state.playback.regrant_for_device(session, device, playback_id)
    return {
        "playback_id": pb.id,
        "stream_descriptor": state.playback.stream_descriptor(session, pb, token),
    }


@router.post("/playbacks/{playback_id}/control")
def playbacks_control(playback_id: str, request: Request, body: ControlBody,
                      device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    state = get_state(request)
    result = state.playback.control(session, device, playback_id, body.action, body.position_ms)
    # AI/Policy 之外的控制也经 Realtime 通知（多连接场景保持一致）
    return result


# ---------- Media Stream / Subtitles（§9.4，Device Token + Grant 双校验） ----------

def _authorize_stream(request: Request, session: Session, device: Device,
                      media_id: str) -> tuple[Media, Playback]:
    state = get_state(request)
    grant = request.headers.get("X-Kindo-Playback-Grant")
    if not grant:
        raise grant_invalid("缺少 X-Kindo-Playback-Grant")
    pb = state.playback.validate_stream_access(session, device, media_id, grant)
    media = session.get(Media, pb.media_id)
    if media is None:
        raise not_found("媒体不存在")
    return media, pb


def _file_iter(state, media: Media, start: int, length: int):
    provider = state.storage.get(media.mount_id)
    remaining = length
    with provider.open_range(media.path_key, start) as f:
        while remaining > 0:
            chunk = f.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _stream_response(request: Request, session: Session, device: Device, media_id: str,
                     is_head: bool):
    from fastapi.responses import Response

    state = get_state(request)
    media, pb = _authorize_stream(request, session, device, media_id)
    try:
        provider = state.storage.get(media.mount_id)
    except KeyError:
        raise not_found("媒体所属挂载已停用或删除，无法拉流") from None
    # 文件大小用扫描入库的 size_bytes（避免每次 Range 都对网盘做 stat/HEAD——
    # 百度链路 TTFB 可达 30s+，是播放器 30s 读超时断连的根因）；缺失时才回退实时探测
    size = media.size_bytes or provider.stat(media.path_key).size
    range_header = request.headers.get("Range")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": media.mime_type or "video/mp4",
        "X-Playback-Id": pb.id,
    }
    try:
        parsed = parse_range_header(range_header, size)
    except RangeUnsatisfiable:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", **headers})

    if parsed is None:
        # 无 Range / 格式非法（宽松忽略）→ 200 全量
        headers["Content-Length"] = str(size)
        if is_head:
            return Response(status_code=200, headers=headers)
        return StreamingResponse(_file_iter(state, media, 0, size),
                                 status_code=200, headers=headers)

    start, end = parsed
    end = size - 1 if end is None else end
    length = end - start + 1
    headers["Content-Length"] = str(length)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if is_head:
        return Response(status_code=206, headers=headers)
    return StreamingResponse(_file_iter(state, media, start, length),
                             status_code=206, headers=headers)


@router.get("/media/{media_id}/stream")
def media_stream(request: Request, media_id: str,
                 device: Device = Depends(device_from_request),
                 session: Session = Depends(get_db)):
    return _stream_response(request, session, device, media_id, is_head=False)


@router.head("/media/{media_id}/stream")
def media_stream_head(request: Request, media_id: str,
                      device: Device = Depends(device_from_request),
                      session: Session = Depends(get_db)):
    return _stream_response(request, session, device, media_id, is_head=True)


@router.get("/media/{media_id}/subtitles/{track_id}")
def media_subtitle(request: Request, media_id: str, track_id: str,
                   device: Device = Depends(device_from_request),
                   session: Session = Depends(get_db)):
    _authorize_stream(request, session, device, media_id)
    track = session.get(SubtitleTrack, track_id)
    if track is None or track.media_id != media_id:
        raise not_found("字幕轨不存在")
    segments = (
        session.query(SubtitleSegment)
        .filter(SubtitleSegment.track_id == track.id)
        .order_by(SubtitleSegment.start_ms)
        .all()
    )
    from ..media.subtitles import ParsedSubtitle, to_webvtt

    parsed = [ParsedSubtitle(seq=s.seq, start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
              for s in segments]
    return StreamingResponse(
        iter([to_webvtt(parsed)]),
        media_type="text/vtt; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{track_id}.vtt"'},
    )

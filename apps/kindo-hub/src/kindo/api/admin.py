"""Web Admin 接口（技术方案 §3.3）。写操作要求 Admin Session + CSRF；
Device Token 调用 /admin/* 一律拒绝（§14.4）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import secretbox
from ..errors import KindoError, conflict, invalid_request, not_found
from ..media.auto_group import rebuild_auto_groups
from ..media.catalog import admin_collections, list_media, media_course_map, media_series_map
from ..media.curation import remove_episode, remove_lesson, upsert_episode, upsert_lesson
from ..media.posters import poster_path, poster_ready
from ..models import AdminUser, AppSetting, ContentEntity, Device, Media
from ..util import new_id, now_iso
from .deps import ADMIN_COOKIE, get_db, get_state, require_admin_read, require_admin_write

SLUG_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

logger = logging.getLogger("kindo.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------- Auth ----------

class BootstrapBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    # 不限最短长度：错误 token（即使很短）应走 403 常时比较语义，而非 400 校验
    bootstrap_token: str = Field(max_length=256)


class LoginBody(BaseModel):
    # 超长密码直接进 Argon2id 会放大 CPU 消耗，先在入口限长
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/bootstrap")
def auth_bootstrap(request: Request, body: BootstrapBody, session: Session = Depends(get_db)):
    state = get_state(request)
    return state.admin_auth.bootstrap(session, body.username, body.password, body.bootstrap_token)


@router.get("/auth/state")
def auth_state(request: Request, session: Session = Depends(get_db)):
    """认证入口状态机（免认证只读）：前端据此渲染 初始化/登录/应用。
    泄露面仅"管理员是否已初始化" 1 bit 与调用者自身会话有效性（LAN 单家庭边界内可接受）。"""
    state = get_state(request)
    if session.query(AdminUser).count() == 0:
        return {"phase": "setup_required", "authenticated": False}
    authenticated = False
    username = None
    sid = request.cookies.get(ADMIN_COOKIE)
    if sid:
        try:
            row = state.admin_auth.authenticate(session, sid)
        except KindoError:
            row = None
        if row is not None:
            authenticated = True
            user = session.get(AdminUser, row.user_id)
            username = user.username if user else None
    return {"phase": "ready", "authenticated": authenticated, "username": username}


@router.post("/auth/login")
def auth_login(request: Request, body: LoginBody, response: Response,
               session: Session = Depends(get_db)):
    state = get_state(request)
    result = state.admin_auth.login(session, body.username, body.password, _client_ip(request))
    response.set_cookie(
        ADMIN_COOKIE, result["session_id"], httponly=True, samesite="strict",
        path="/", max_age=24 * 3600,
    )
    return {
        "csrf_token": result["csrf_token"], "expires_at": result["expires_at"],
        "user": result["user"],
    }


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response, session: Session = Depends(get_db),
                _admin=Depends(require_admin_write)):
    from .deps import admin_session_id

    state = get_state(request)
    try:
        sid = admin_session_id(request)
        state.admin_auth.logout(session, sid)
    finally:
        response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/status")
def auth_status(request: Request, session: Session = Depends(get_db),
                _admin=Depends(require_admin_read)):
    user = session.get(AdminUser, _admin.user_id)
    return {"authenticated": True, "username": user.username if user else None}


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/auth/password")
def auth_change_password(request: Request, body: PasswordChangeBody,
                         session: Session = Depends(get_db),
                         _admin=Depends(require_admin_write)):
    """修改管理员密码（需当前密码；成功后撤销该用户其余会话，当前会话保留）。"""
    from .deps import admin_session_id

    state = get_state(request)
    username = state.admin_auth.change_password(
        session, admin_session_id(request),
        body.current_password, body.new_password)
    return {"ok": True, "username": username,
            "note": "其他已登录的浏览器需要重新登录"}


# ---------- Health ----------

@router.get("/health")
async def admin_health(request: Request, session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    state = get_state(request)
    asr = await state.asr.health()
    labels = _mount_labels(state)
    # 同步 DB / 挂载探测（SMB/WebDAV 网络 IO）放线程池，避免阻塞事件循环
    def _sync_part():
        devices = session.query(Device).all()
        # 在线优先 + 最近活跃在前（此前 DB 插入序，长列表翻找困难）
        devices.sort(key=lambda d: (
            not state.realtime.is_online(d.id),
            -(d.last_seen_at.timestamp() if d.last_seen_at else 0),
        ))
        def _with_label(m: dict) -> dict:
            mid = m.get("mount_id") or ""
            m["label"] = labels.get(mid, mid)
            return m

        # 首跑引导检查清单数据（概览页"开始使用 Kindo"卡）：入库量 + 待确认匹配数
        from sqlalchemy import func

        media_total = (session.query(func.count(Media.id))
                       .filter(Media.missing.is_(False)).scalar() or 0)
        match_pending = (session.query(func.count(ContentEntity.id))
                         .filter(ContentEntity.match_status == "pending").scalar() or 0)

        return {
            "media": {
                "total": media_total,
                "match_pending": match_pending,
                "mounts": [_with_label(m) for m in state.storage.health()],
                "latest_jobs": [
                    {
                        "id": j.id, "mount_id": j.mount_id, "state": j.state,
                        "progress": j.progress, "error_summary": j.error_summary,
                        "started_at": j.started_at.isoformat() if j.started_at else None,
                        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                        "label": labels.get(j.mount_id, j.mount_id),
                    }
                    for j in state.scanner.latest_jobs(10)
                ],
            },
            "devices": [
                {
                    "device_id": d.id, "name": d.name, "status": d.status,
                    "paired_at": d.paired_at.isoformat(),
                    "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                    "online": state.realtime.is_online(d.id),
                }
                for d in devices
            ],
        }

    sync = await asyncio.to_thread(_sync_part)
    providers = [
        {"provider_id": v.id, "display_name": v.display_name,
         "protocol": v.protocol, "model": v.model,
         "source": v.source, "configured": True}
        for v in state.provider_registry.all()
    ]
    active_provider_id, _ = state.active_model()
    return {
        "hub": {"version": "0.1.0", "time": now_iso()},
        "database": {"ready": True},
        **sync,
        "asr": asr,
        "llm_providers": providers,
        "active_model": {"provider_id": active_provider_id},
    }


# ---------- Media / Mounts / Scan ----------

def _mount_labels(state) -> dict[str, str]:
    """存储 id → 显示名（扫描任务/健康表展示用）。"""
    from ..models import MediaMount

    with state.db.session() as session:
        rows = session.query(MediaMount).all()
    out = {}
    for row in rows:
        out[state.mounts.resolve_mount_id(row)] = row.label
    return out


@router.get("/media-mounts")
def admin_mounts(request: Request, session: Session = Depends(get_db),
                 _admin=Depends(require_admin_read)):
    """媒体来源列表（2026-08-25 全页面化：本地目录与网络源统一页面管理）。"""
    state = get_state(request)
    payload = state.mounts.list_mounts(session)
    payload["scan_targets"] = state.storage.mount_ids
    return payload


class MountCreateBody(BaseModel):
    # 本地源：服务器（容器内）绝对路径
    path: str = Field(default="", max_length=1024)
    # 通用
    label: str | None = Field(default=None, max_length=128)
    read_only: bool = True
    mount_type: str = Field(default="local", pattern="^(local|smb|webdav)$")
    # SMB
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    share: str | None = Field(default=None, max_length=255)
    # WebDAV
    url: str | None = Field(default=None, max_length=2048)
    # 网络源子路径/账号（本地源时 path 为绝对路径，二者语义由 mount_type 区分）
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)  # 写-only
    net_path: str | None = Field(default=None, max_length=512)  # 网络源子路径
    # 媒体探测策略（B）：range=Range 反代（默认）/ skip / full
    probe_mode: str | None = Field(default=None, pattern="^(range|skip|full)$")


@router.post("/media-mounts")
def admin_mount_create(request: Request, body: MountCreateBody,
                       session: Session = Depends(get_db),
                       _admin=Depends(require_admin_write)):
    state = get_state(request)
    if body.mount_type == "local":
        # 本地：path = 服务器/容器内绝对路径（Docker 部署者经 compose 卷映射）
        return state.mounts.create(
            session, body.label or "", body.read_only,
            mount_type="local", config={"path": body.path})
    # 网络源（SMB/WebDAV，MED-003 P0）
    config = {"host": body.host, "port": body.port, "share": body.share,
              "url": body.url, "path": body.net_path, "username": body.username,
              "probe_mode": body.probe_mode or "range"}
    config = {k: v for k, v in config.items() if v not in (None, "")}
    secret = {"password": body.password} if body.password else {}
    return state.mounts.create(
        session, body.label or "", True,
        mount_type=body.mount_type, config=config, secret=secret)


class MountPatchBody(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    read_only: bool | None = None
    active: bool | None = None
    # 本地源：绝对路径（须存在）；网络源：子路径。语义由 mount_type 区分
    path: str | None = Field(default=None, max_length=1024)
    # 网络源连接字段（写-only 密码；空串=清除用户名，密码空串=清除密码）
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    share: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    probe_mode: str | None = Field(default=None, pattern="^(range|skip|full)$")


@router.patch("/media-mounts/{mount_id}")
def admin_mount_patch(mount_id: str, request: Request, body: MountPatchBody,
                      session: Session = Depends(get_db),
                      _admin=Depends(require_admin_write)):
    """编辑媒体来源：显示名/启停/只读 + 本地路径 / 网络连接字段
    （密码写-only，提交空串=清除；未提交字段不变）。连接字段变化即重建 provider。"""
    state = get_state(request)
    from ..models import MediaMount

    row = session.get(MediaMount, mount_id)
    if row is None or row.deleted_at is not None:
        raise not_found("挂载不存在")
    mtype = row.mount_type or "local"
    fields_set = body.model_fields_set
    network_fields = {"host", "port", "share", "url", "username", "password"}
    if mtype == "local" and network_fields & fields_set:
        raise invalid_request("本地来源仅支持 path / 显示名 / 只读 / 启停")
    if mtype != "local" and "path" in fields_set:
        # 网络源的 path 语义 = 子路径
        pass
    if mtype == "smb" and "host" in fields_set and not (body.host or "").strip():
        raise invalid_request("SMB 主机不能为空")
    if mtype == "smb" and "share" in fields_set and not (body.share or "").strip():
        raise invalid_request("SMB 共享名不能为空")
    if mtype == "webdav" and "url" in fields_set and body.url and \
            not body.url.startswith(("http://", "https://")):
        raise invalid_request("WebDAV 地址需以 http:// 或 https:// 开头")

    config_patch: dict = {}
    if "host" in fields_set:
        config_patch["host"] = (body.host or "").strip()
    if "port" in fields_set:
        config_patch["port"] = body.port
    if "share" in fields_set:
        config_patch["share"] = (body.share or "").strip()
    if "url" in fields_set:
        config_patch["url"] = (body.url or "").strip()
    if "path" in fields_set and mtype != "local":
        config_patch["path"] = (body.path or "").strip()
    if "username" in fields_set:
        config_patch["username"] = (body.username or "").strip()
    if "probe_mode" in fields_set and body.probe_mode:
        config_patch["probe_mode"] = body.probe_mode

    return state.mounts.update(
        session, mount_id, label=body.label, read_only=body.read_only,
        active=body.active,
        path=body.path if ("path" in fields_set and mtype == "local") else None,
        config_patch=config_patch or None,
        password=body.password if "password" in fields_set else None,
    )


@router.delete("/media-mounts/{mount_id}")
def admin_mount_delete(mount_id: str, request: Request,
                       session: Session = Depends(get_db),
                       _admin=Depends(require_admin_write)):
    """删除来源：注销挂载并清除其入库资源（文件保留，自动备份数据库）。
    广播 sync.required 让在线 TV 清空已加载的库数据（§4.2）。"""
    state = get_state(request)
    out = state.mounts.delete(session, mount_id)
    for dev in session.query(Device).filter(Device.status == "active").all():
        state.realtime.emit(dev.id, "sync.required", {})
    return out


@router.post("/media-mounts/test")
def admin_mount_test(request: Request, body: MountCreateBody,
                     _admin=Depends(require_admin_write)):
    """添加前的连接测试（不落库）：本地=目录存在性；网络=真实连通校验，
    失败返回可执行的修正提示（405→/dav 端点、401→凭据、404→路径）。
    注意注册在 /{mount_id}/scan 之前，避免 "test" 被当路径参数吞掉。"""
    state = get_state(request)
    from ..util import new_id

    if body.mount_type == "local":
        from pathlib import Path

        raw = (body.path or "").strip()
        p = Path(raw)
        if not raw or not p.is_absolute():
            return {"ok": False, "message": "请填服务器/容器内绝对路径（Docker 部署为容器内路径，如 /media）"}
        if not p.is_dir():
            return {"ok": False, "message": f"目录不存在或不可读：{raw}"}
        return {"ok": True, "message": f"目录可用（{raw}）"}
    if body.mount_type not in ("smb", "webdav"):
        raise invalid_request("mount_type 只支持 local|smb|webdav")
    config = {"host": body.host, "port": body.port, "share": body.share,
              "url": body.url, "path": body.net_path, "username": body.username}
    config = {k: v for k, v in config.items() if v not in (None, "")}
    secret = {"password": body.password} if body.password else {}
    provider = state.mounts._build_network_provider(f"test-{new_id()[:8]}", body.mount_type,
                                                    config, secret)
    try:
        provider.check_connectivity()
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "连接成功"}


@router.post("/media-mounts/{mount_id}/scan")
def admin_scan(mount_id: str, request: Request, session: Session = Depends(get_db),
               force_full: bool = False,
               _admin=Depends(require_admin_write)):
    state = get_state(request)
    from ..models import MediaMount

    target = mount_id
    row = session.get(MediaMount, mount_id)
    if row is not None:  # 挂载行 id → 存储 id（page-<id> 或收养根原 id）
        target = state.mounts.resolve_mount_id(row)
    try:
        job_id = state.scanner.start_job(target, force_full=force_full)
    except KeyError:
        raise not_found(f"挂载 {mount_id} 不存在或未激活") from None
    return {"job_id": job_id, "state": "queued", "storage_mount_id": target,
            "force_full": force_full}


@router.get("/media-mounts/health")
def admin_mounts_health(request: Request, _admin=Depends(require_admin_read)):
    """全部扫描目标的健康探测。网络源是真实连接检查（可能秒级超时），
    并行执行且整体 4s 截断——独立于挂载列表端点，避免离线 NAS 拖死页面。"""
    import threading as _threading
    import time as _time

    state = get_state(request)
    mount_ids = state.storage.mount_ids
    results: dict[str, dict] = {}

    def probe_one(mid: str) -> None:
        try:
            results[mid] = state.storage.get(mid).health()
        except Exception as exc:  # 健康检查失败 ≠ 服务器错误
            results[mid] = {"mount_id": mid, "healthy": False, "error": str(exc)[:200]}

    workers = [
        _threading.Thread(target=probe_one, args=(mid,), daemon=True) for mid in mount_ids
    ]
    for w in workers:
        w.start()
    deadline = _time.monotonic() + 4.0
    for w in workers:
        w.join(timeout=max(0.0, deadline - _time.monotonic()))
    for mid in mount_ids:  # 超时未返回的按 unhealthy 收敛
        if mid not in results:
            results[mid] = {"mount_id": mid, "healthy": False, "error": "健康检查超时"}
    return {"mounts": [results[mid] for mid in mount_ids]}


@router.get("/scan-jobs")
def admin_scan_jobs(request: Request, limit: int = 20,
                    _admin=Depends(require_admin_read)):
    """最近扫描任务（挂载页“扫描历史”）。state: queued|running|done|failed|interrupted。"""
    state = get_state(request)
    limit = max(1, min(limit, 100))
    labels = _mount_labels(state)
    return {
        "jobs": [
            {
                "id": j.id, "mount_id": j.mount_id,
                "label": labels.get(j.mount_id, j.mount_id),
                "state": j.state, "progress": j.progress,
                "error_summary": j.error_summary,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in state.scanner.latest_jobs(limit)
        ]
    }


@router.get("/scan-jobs/{job_id}")
def admin_scan_job(job_id: str, request: Request, session: Session = Depends(get_db),
                   _admin=Depends(require_admin_read)):
    state = get_state(request)
    job = state.scanner.get_job(job_id)
    if job is None:
        raise not_found("扫描任务不存在")
    return {
        "id": job.id, "mount_id": job.mount_id, "state": job.state, "progress": job.progress,
        "error_summary": job.error_summary,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/media")
def admin_media(request: Request, session: Session = Depends(get_db),
                type: str | None = None, language: str | None = None,
                tag: str | None = None, series_id: str | None = None,
                course_id: str | None = None,
                cursor: str | None = None, limit: int = 20,
                sort: str = "added",
                _admin=Depends(require_admin_read)):
    limit = max(1, min(limit, 100))
    sort = sort if sort in ("added", "title") else "added"
    rows, next_cursor = list_media(
        session, media_type=type, language=language, tag=tag, series_id=series_id,
        course_id=course_id, cursor=cursor, limit=limit, include_missing=True,
        sort=sort,
    )
    ids = [m.id for m in rows]
    series_of = media_series_map(session, ids)
    course_of = media_course_map(session, ids)
    mount_labels = _mount_labels(get_state(request))
    # 库内实际存在的类型分布（筛选项按内容派生，不再出现空类型）
    from sqlalchemy import func as _f

    type_counts: dict[str, int] = {
        r[0]: r[1] for r in (
            session.query(Media.media_type, _f.count(Media.id))
            .filter(Media.missing.is_(False))
            .group_by(Media.media_type).all())
    }
    # v0.3 内容目录维度（badge/筛选）：经 source_media_id 挂到叶子实体
    entities = {
        e.source_media_id: e for e in (
            session.query(ContentEntity)
            .filter(ContentEntity.source_media_id.in_(ids)).all())
        if e.source_media_id
    }
    return {
        "type_counts": type_counts,
        "items": [
            {
                "media_id": m.id, "title": m.title, "media_type": m.media_type,
                "mount_id": m.mount_id, "path_key": m.path_key,
                "duration_ms": m.duration_ms, "language": m.language,
                "age_band": m.age_band, "tags": m.tags_json or {},
                "playable": m.playable, "missing": m.missing,
                "metadata_version": m.metadata_version,
                "parent_edited": bool(m.parent_edited_json),
                "has_poster": m.has_poster,
                "size_bytes": m.size_bytes,
                "auto_grouped": m.auto_series_key is not None,
                "series": series_of.get(m.id),
                "course": course_of.get(m.id),
                "entity_id": entities[m.id].id if m.id in entities else None,
                "content_class": entities[m.id].content_class if m.id in entities else None,
                "modality": entities[m.id].modality if m.id in entities else None,
                "mount_label": mount_labels.get(m.mount_id, m.mount_id),
            }
            for m in rows
        ],
        "next_cursor": next_cursor,
    }


@router.get("/media/{media_id}/poster")
def admin_media_poster(media_id: str, request: Request, session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    """扫描期生成的缩略海报（/data/cache/posters/{id}.jpg，§13.2）。
    无真实海报时回退系列实体海报 → 默认海报（2026-08-27 与 TV 端点
    同一语义：MED-013 海报来源一致，集级无图用系列图）。"""
    from ..media.content_catalog import series_poster_file
    from ..media.posters import default_poster

    media = session.get(Media, media_id)
    if media is None:
        raise not_found("媒体不存在")
    if poster_ready(get_state(request).config, media_id):
        path = poster_path(get_state(request).config, media_id)
    else:
        path = series_poster_file(
            session, get_state(request).config.data_dir, media_id)
        if path is None:
            try:
                path = default_poster(get_state(request).config, seed=media_id)
            except FileNotFoundError:
                raise not_found("该媒体暂无海报") from None
    return FileResponse(
        path, media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/collections")
def admin_collections_route(request: Request, session: Session = Depends(get_db),
                           _admin=Depends(require_admin_read)):
    """系列/课程聚合（媒体库“按合集浏览”视图）。"""
    return admin_collections(session)


class AutoGroupBody(BaseModel):
    # 省略/为空 = 全部挂载；只按已入库 path_key 本地推导，不触发存储访问
    mount_id: str | None = Field(default=None, max_length=64)


@router.post("/media/auto-group")
def admin_media_auto_group(request: Request, body: AutoGroupBody,
                           session: Session = Depends(get_db),
                           _admin=Depends(require_admin_write)):
    """本地重算自动归组（存量回填入口）：按目录结构推导系列/集号/media_type，
    sidecar 与家长修正建立的归组不受影响。网络源重扫=整树枚举，此端点零网络。"""
    stats = rebuild_auto_groups(session, body.mount_id or None)
    return {
        **stats,
        "note": "grouped/rebound=新归组或改绑，released=目录变化后解除，"
                "cleared=让位给 sidecar/家长修正，kept=无变化",
    }


# ---------- 海报刮削（2026-08-21 PRD 修订：轻量 TMDB 刮削） ----------


def _scrape_service(request: Request):
    state = get_state(request)
    return state._extra["scrape"]


@router.get("/scrape/config")
def scrape_config_get(request: Request, session: Session = Depends(get_db),
                      _admin=Depends(require_admin_read)):
    return _scrape_service(request).get_config(session)


class ScrapeConfigBody(BaseModel):
    base_url: str | None = Field(default=None, max_length=256)
    image_base_url: str | None = Field(default=None, max_length=256)
    language: str | None = Field(default=None, max_length=16)
    api_key: str | None = Field(default=None, max_length=256)  # 只写不回显


@router.put("/scrape/config")
def scrape_config_put(request: Request, body: ScrapeConfigBody,
                      session: Session = Depends(get_db),
                      _admin=Depends(require_admin_write)):
    return _scrape_service(request).save_config(
        session, base_url=body.base_url, image_base_url=body.image_base_url,
        language=body.language, api_key=body.api_key,
    )


class ScrapeRunBody(BaseModel):
    force: bool = False  # 忽略已刮削标记重查（未命中/命中都会重试）


@router.post("/scrape/run")
def scrape_run(request: Request, body: ScrapeRunBody | None = None,
               _admin=Depends(require_admin_write)):
    from ..errors import conflict

    started, status = _scrape_service(request).start(
        force=bool(body.force) if body else False,
    )
    if not started:
        raise conflict("刮削任务正在运行")
    return status


@router.get("/scrape/status")
def scrape_status(request: Request, _admin=Depends(require_admin_read)):
    return _scrape_service(request).status.snapshot()


# ---------- 身份匹配管理（v0.3 决策三，ADM-012） ----------

@router.get("/match/overview")
def match_overview(request: Request, session: Session = Depends(get_db),
                   _admin=Depends(require_admin_read)):
    from sqlalchemy import func

    from ..models import ContentEntity

    rows = (
        session.query(ContentEntity.entity_type, ContentEntity.match_status,
                      func.count(ContentEntity.id))
        .filter(ContentEntity.entity_type.in_(("series", "movie")))
        .group_by(ContentEntity.entity_type, ContentEntity.match_status)
        .all()
    )
    counts: dict[str, int] = {}
    for _t, status, n in rows:
        counts[status] = counts.get(status, 0) + n
    pending_entities = (
        session.query(ContentEntity)
        .filter(ContentEntity.entity_type.in_(("series", "movie")),
                ContentEntity.candidates_json.isnot(None))
        .order_by(ContentEntity.title)
        .limit(200)
        .all()
    )
    # 已检索但无候选（pending_saved 决策、无缓存候选）：页面可见并支持标记
    # 无匹配——标记后不再进入刮削目标（等价旧 no_hit 防重查）
    from ..models import MatchDecision

    no_candidate_ids = [
        r[0] for r in (
            session.query(MatchDecision.entity_id)
            .filter(MatchDecision.decision == "pending_saved",
                    MatchDecision.confidence == "none")
            .distinct().limit(300).all())
    ]
    no_candidates = (
        session.query(ContentEntity)
        .filter(ContentEntity.id.in_(no_candidate_ids or ["-"]),
                ContentEntity.entity_type.in_(("series", "movie")),
                ContentEntity.match_status == "none",
                ContentEntity.candidates_json.is_(None))
        .order_by(ContentEntity.title)
        .limit(200)
        .all()) if no_candidate_ids else []
    return {
        "counts": counts,
        "pending": [
            {
                "entity_id": e.id, "entity_type": e.entity_type, "title": e.title,
                "match_status": e.match_status, "candidates": e.candidates_json or [],
            }
            for e in pending_entities
        ],
        "no_candidates": [
            {"entity_id": e.id, "entity_type": e.entity_type, "title": e.title}
            for e in no_candidates
        ],
    }


class MatchSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=128)
    entity_id: str | None = None


@router.post("/match/search")
def match_search(request: Request, body: MatchSearchBody,
                 session: Session = Depends(get_db),
                 _admin=Depends(require_admin_write)):
    """家长手动检索候选（即查即显，不落库）。"""
    from ..media.matcher import score_candidates, search_tmdb
    from ..media.scrape import _make_client  # 测试注入锚点

    svc = _scrape_service(request)
    cfg = svc.get_config(session)
    if not cfg["api_key_configured"]:
        raise invalid_request("未配置 TMDB API Key")
    kind = "movie"
    if body.entity_id:
        ent = session.get(ContentEntity, body.entity_id)
        if ent is None:
            raise not_found("内容不存在")
        kind = "tv" if ent.entity_type == "series" else "movie"
    client = _make_client(cfg["base_url"])
    try:
        cands = search_tmdb(client, svc._api_key(), kind, body.query, cfg["language"])
    finally:
        client.close()
    scored = score_candidates(body.query, cands)
    return {"candidates": [
        {**c.to_json(), "confidence": conf} for c, conf in scored[:10]
    ]}


class MatchConfirmBody(BaseModel):
    provider: str = "tmdb"
    ref_id: str | None = None
    title: str = ""
    first_air_date: str = ""
    poster_path: str = ""
    no_match: bool = False
    apply_details: bool = True


@router.post("/content/{entity_id}/match")
def match_confirm(entity_id: str, request: Request, body: MatchConfirmBody,
                  session: Session = Depends(get_db),
                  _admin=Depends(require_admin_write)):
    """家长确认匹配 / 标记无匹配（confirmed 与 no_match 永不被 refresh 覆盖）。"""
    from ..media.artwork import upsert_artwork
    from ..media.matcher import confirm_match, mark_no_match
    from ..media.metadata import fetch_tmdb_details, normalize_provider_details
    from ..media.scrape import _make_client

    entity = session.get(ContentEntity, entity_id)
    if entity is None or entity.entity_type not in ("series", "movie"):
        raise not_found("内容不存在")
    if body.no_match:
        mark_no_match(session, entity)
        session.commit()
        return {"entity_id": entity_id, "match_status": "no_match"}
    if not body.ref_id:
        raise invalid_request("缺少 ref_id")
    confirm_match(session, entity, body.ref_id, body.title,
                  body.first_air_date, body.poster_path)
    # 确认后立即拉取详情与海报（confirmed 级合并）
    if body.apply_details:
        svc = _scrape_service(request)
        cfg = svc.get_config(session)
        if cfg["api_key_configured"]:
            client = _make_client(cfg["base_url"])
            try:
                kind = "tv" if entity.entity_type == "series" else "movie"
                details = fetch_tmdb_details(client, svc._api_key(), kind,
                                             body.ref_id, cfg["language"])
                normalize_provider_details(entity, details, confirmed=True)
                if details.poster_path:
                    img = client.get(f"{cfg['image_base_url']}/w500{details.poster_path}")
                    if img.status_code == 200 and img.content:
                        upsert_artwork(session, request.app.state.kindo.config,
                                       entity.id, "poster", "provider", img.content)
            except Exception as exc:  # 详情失败不阻塞确认本身
                logger.warning("确认后详情拉取失败 %s: %s", entity.title, exc)
            finally:
                client.close()
    session.commit()
    return {"entity_id": entity_id, "match_status": entity.match_status}


@router.get("/content/{entity_id}/match/decisions")
def match_decisions(entity_id: str, request: Request,
                    session: Session = Depends(get_db),
                    _admin=Depends(require_admin_read)):
    from ..models import MatchDecision

    rows = (session.query(MatchDecision)
            .filter(MatchDecision.entity_id == entity_id)
            .order_by(MatchDecision.created_at.desc())
            .limit(50).all())
    return {"decisions": [
        {"provider": d.provider, "candidate": d.candidate_json,
         "confidence": d.confidence, "decision": d.decision,
         "decided_by": d.decided_by, "created_at": d.created_at.isoformat()}
        for d in rows
    ]}


@router.get("/match/decisions/recent")
def match_decisions_recent(request: Request,
                           session: Session = Depends(get_db),
                           limit: int = 50, _admin=Depends(require_admin_read)):
    """全局决策时间线（ADM-012 审计视图）：最近的确认/无匹配/自动绑定记录。"""
    from ..models import ContentEntity, MatchDecision

    limit = max(1, min(limit, 200))
    rows = (session.query(MatchDecision, ContentEntity.title, ContentEntity.entity_type)
            .outerjoin(ContentEntity, ContentEntity.id == MatchDecision.entity_id)
            .order_by(MatchDecision.created_at.desc())
            .limit(limit).all())
    return {"decisions": [
        {"entity_id": d.entity_id, "entity_title": title or d.entity_id,
         "entity_type": etype or "",
         "provider": d.provider, "candidate": d.candidate_json,
         "confidence": d.confidence, "decision": d.decision,
         "decided_by": d.decided_by, "created_at": d.created_at.isoformat()}
        for d, title, etype in rows
    ]}


# ---------- 多版本文件 / 首选版本（PLY-009） ----------

@router.get("/content/{entity_id}/assets")
def entity_assets(entity_id: str, request: Request,
                  session: Session = Depends(get_db),
                  _admin=Depends(require_admin_read)):
    """实体关联的全部文件版本（一集多版本）：role=PRIMARY_VIDEO 为首选。"""
    from ..models import EntityAsset

    entity = session.get(ContentEntity, entity_id)
    if entity is None:
        raise not_found(f"实体 {entity_id} 不存在")
    rows = (session.query(EntityAsset, Media)
            .join(Media, Media.id == EntityAsset.asset_id)
            .filter(EntityAsset.entity_id == entity_id)
            .order_by(EntityAsset.role.asc(), Media.size_bytes.desc())
            .all())
    return {"entity_id": entity_id, "entity_title": entity.title, "assets": [
        {"asset_id": ea.asset_id, "media_id": ea.asset_id,  # 兼容期同 id
         "role": ea.role, "title": m.title, "path_key": m.path_key,
         "size_bytes": m.size_bytes, "duration_ms": m.duration_ms,
         "playable": m.playable, "missing": m.missing}
        for ea, m in rows
    ]}


class PreferredAssetBody(BaseModel):
    asset_id: str


@router.put("/content/{entity_id}/preferred-asset")
def set_preferred_asset(entity_id: str, request: Request, body: PreferredAssetBody,
                        session: Session = Depends(get_db),
                        _admin=Depends(require_admin_write)):
    """家长设首选版本（PLY-009）：选定 asset → PRIMARY_VIDEO，其余降为
    ALTERNATE_VIDEO（浏览/检索/播放默认走首选；密级与元数据不受影响）。
    重建关联行而非原地换 role——(entity,asset,role) 唯一约束下两行互换
    必然瞬态冲突。"""
    from ..models import EntityAsset

    entity = session.get(ContentEntity, entity_id)
    if entity is None:
        raise not_found(f"实体 {entity_id} 不存在")
    links = session.query(EntityAsset).filter(EntityAsset.entity_id == entity_id).all()
    if not links:
        raise invalid_request("该实体没有关联文件版本")
    if not any(lnk.asset_id == body.asset_id for lnk in links):
        raise invalid_request("asset_id 不属于该实体")
    for lnk in links:
        session.delete(lnk)
    session.flush()
    for lnk in links:
        session.add(EntityAsset(
            id=new_id(), entity_id=entity_id, asset_id=lnk.asset_id,
            role="PRIMARY_VIDEO" if lnk.asset_id == body.asset_id else "ALTERNATE_VIDEO",
            sequence=lnk.sequence))
    session.commit()
    return {"entity_id": entity_id, "preferred_asset_id": body.asset_id}


# ---------- Canonical 元数据编辑（v0.3 决策四，ADM-003） ----------

# 实体结构化字段：值可编辑 + 来源/锁定可展示（meta_provenance_json）
CANONICAL_FIELDS = (
    "title", "language", "content_class", "modality", "age_min", "age_max",
    "overview", "release_date", "difficulty", "sequence_no", "repeatable",
)
PROVENANCE_LABELS = {
    "parent_locked": "家长锁定", "parent": "家长", "sidecar": "Sidecar",
    "provider_confirmed": "Provider（已确认）", "provider": "Provider",
    "parser": "路径推断", "": "未设置",
}


def _entity_canonical(session: Session, entity: ContentEntity) -> dict:
    from ..models import ContentCharacter, ContentTopic, EntityCharacter, EntityTopic

    topics = [
        r[0] for r in (
            session.query(ContentTopic.name)
            .join(EntityTopic, EntityTopic.topic_id == ContentTopic.id)
            .filter(EntityTopic.entity_id == entity.id)
            .order_by(ContentTopic.name).all())
    ]
    characters = [
        r[0] for r in (
            session.query(ContentCharacter.name)
            .join(EntityCharacter, EntityCharacter.character_id == ContentCharacter.id)
            .filter(EntityCharacter.entity_id == entity.id)
            .order_by(ContentCharacter.name).all())
    ]
    prov = entity.meta_provenance_json or {}
    fields = {}
    for f in CANONICAL_FIELDS:
        raw = prov.get(f) or {}
        source = raw.get("source") or ""
        fields[f] = {
            "value": getattr(entity, f),
            "source": source,
            "source_label": PROVENANCE_LABELS.get(source, source),
            "locked": bool(raw.get("locked")),
            "updated_at": raw.get("updated_at"),
        }
    for f in ("topics", "characters"):
        raw = prov.get(f) or {}
        source = raw.get("source") or ""
        fields[f] = {
            "value": topics if f == "topics" else characters,
            "source": source,
            "source_label": PROVENANCE_LABELS.get(source, source),
            "locked": bool(raw.get("locked")),
            "updated_at": raw.get("updated_at"),
        }
    parent_title = None
    if entity.parent_id is not None:
        p = session.get(ContentEntity, entity.parent_id)
        parent_title = p.title if p else None
    return {
        "entity_id": entity.id, "entity_type": entity.entity_type,
        "parent_id": entity.parent_id, "parent_title": parent_title,
        "match_status": entity.match_status,
        "ordering": entity.ordering,
        "duration_ms": entity.duration_ms,
        "fields": fields,
        "provenance_levels": [
            "parent_locked > parent > sidecar > provider_confirmed > provider > parser"],
        "note": "锁定字段永不被 Provider Refresh 或重扫覆盖（硬性约束 15）",
    }


def _entity_or_404(session: Session, entity_id: str) -> ContentEntity:
    entity = session.get(ContentEntity, entity_id)
    if entity is None:
        raise not_found("内容实体不存在")
    return entity


@router.get("/content/by-media/{media_id}")
def admin_content_by_media(media_id: str, request: Request,
                           session: Session = Depends(get_db),
                           _admin=Depends(require_admin_read)):
    """媒体 → 内容实体（媒体详情抽屉的 Canonical 面板入口）。"""
    entity = (session.query(ContentEntity)
              .filter(ContentEntity.source_media_id == media_id)
              .first())
    if entity is None:
        return {"entity": None}
    return {"entity": _entity_canonical(session, entity)}


@router.get("/content/{entity_id}")
def admin_content_get(entity_id: str, request: Request,
                      session: Session = Depends(get_db),
                      _admin=Depends(require_admin_read)):
    return _entity_canonical(session, _entity_or_404(session, entity_id))


class CanonicalFieldValue(BaseModel):
    # value 省略 = 只改锁定；显式 null = 清空该字段（家长明确写入 None）
    value: list[str] | bool | int | str | None = None
    locked: bool | None = None
    has_value: bool = False  # 显式标记“提交了 value”（区分 null 与未提交）


class CanonicalPatchBody(BaseModel):
    # {field: {value?, locked?, has_value?}}；topics/characters 为字符串数组
    fields: dict[str, CanonicalFieldValue] = Field(min_length=1, max_length=16)


def _set_entity_topics(session: Session, entity: ContentEntity, names: list[str]) -> None:
    """替换实体主题关联（≤32 个；家长级）。叶子实体同步写回 media.tags 以保
    持重扫防覆盖语义（sync_tags 由 media.tags_json 派生）。"""
    from ..media.content_catalog import sync_tags
    from ..models import ContentTopic, EntityTopic

    names = sorted({n.strip() for n in names if n.strip()})[:32]
    session.query(EntityTopic).filter(EntityTopic.entity_id == entity.id).delete()
    for name in names:
        topic = session.query(ContentTopic).filter(ContentTopic.name == name).first()
        if topic is None:
            topic = ContentTopic(id=new_id(), name=name)
            session.add(topic)
            session.flush()
        session.add(EntityTopic(entity_id=entity.id, topic_id=topic.id))
    if entity.source_media_id:
        media = session.get(Media, entity.source_media_id)
        if media is not None:
            edited = dict(media.parent_edited_json or {})
            merged = dict(edited.get("tags") or {})
            merged["themes"] = names
            edited["tags"] = merged
            media.parent_edited_json = edited
            media.tags_json = {**(media.tags_json or {}), "themes": names}
            sync_tags(session, entity.id, media.tags_json or {})


def _set_entity_characters(session: Session, entity: ContentEntity,
                           names: list[str]) -> None:
    from ..media.content_catalog import sync_tags
    from ..models import ContentCharacter, EntityCharacter

    names = sorted({n.strip() for n in names if n.strip()})[:32]
    session.query(EntityCharacter).filter(
        EntityCharacter.entity_id == entity.id).delete()
    for name in names:
        ch = session.query(ContentCharacter).filter(
            ContentCharacter.name == name).first()
        if ch is None:
            ch = ContentCharacter(id=new_id(), name=name)
            session.add(ch)
            session.flush()
        session.add(EntityCharacter(entity_id=entity.id, character_id=ch.id))
    if entity.source_media_id:
        media = session.get(Media, entity.source_media_id)
        if media is not None:
            edited = dict(media.parent_edited_json or {})
            merged = dict(edited.get("tags") or {})
            merged["characters"] = names
            edited["tags"] = merged
            media.parent_edited_json = edited
            media.tags_json = {**(media.tags_json or {}), "characters": names}
            sync_tags(session, entity.id, media.tags_json or {})


@router.patch("/content/{entity_id}")
def admin_content_patch(entity_id: str, request: Request, body: CanonicalPatchBody,
                        session: Session = Depends(get_db),
                        _admin=Depends(require_admin_write)):
    """Canonical 字段家长编辑（PARENT_EXPLICIT / locked=PARENT_LOCKED）。
    分类字段（content_class）变更即分类事实来源变更，家长可锁定防漂移（约束 12/15）。"""
    from ..media.metadata import set_field_parent

    entity = _entity_or_404(session, entity_id)
    applied: list[str] = []
    for field, spec in body.fields.items():
        if field in ("topics", "characters"):
            names = (spec.value if isinstance(spec.value, list) else [])
            if field == "topics":
                _set_entity_topics(session, entity, names)
            else:
                _set_entity_characters(session, entity, names)
            prov = dict(entity.meta_provenance_json or {})
            old = prov.get(field) or {}
            new_locked = (bool(spec.locked) if spec.locked is not None
                          else bool(old.get("locked")))
            prov[field] = {"source": "parent", "updated_at": now_iso(),
                           "locked": new_locked}
            entity.meta_provenance_json = prov
            applied.append(field)
            continue
        if field not in CANONICAL_FIELDS:
            raise invalid_request(f"未知 Canonical 字段: {field}")
        if field == "content_class" and spec.has_value and spec.value not in (
                "ENTERTAINMENT", "LEARNING", "STORY", "MUSIC", "OTHER", None):
            raise invalid_request("content_class 取值非法")
        if field == "modality" and spec.has_value and spec.value not in (
                "VIDEO", "AUDIO", "AI_VOICE", "OFFSCREEN", None):
            raise invalid_request("modality 取值非法")
        locked = spec.locked
        if spec.has_value:
            value = spec.value
            if field in ("age_min", "age_max", "sequence_no"):
                value = None if value is None else int(value)  # type: ignore[arg-type]
            elif field == "repeatable":
                value = bool(value)
            set_field_parent(session, entity, field, value,
                             locked=bool(locked) if locked is not None else False)
        elif locked is not None:
            # 只切换锁定：保持当前值，来源升级/保持 parent
            set_field_parent(session, entity, field, getattr(entity, field),
                             locked=locked)
        applied.append(field)
    session.commit()
    return {"entity_id": entity.id, "applied": applied,
            "fields": _entity_canonical(session, entity)["fields"]}


# ---------- Artwork 管理（v0.3 决策八，ADM-013） ----------

ARTWORK_KINDS = ("poster", "backdrop", "thumbnail", "logo")


@router.get("/content/{entity_id}/artwork")
def admin_artwork_list(entity_id: str, request: Request,
                       session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    from ..models import ArtworkAsset

    _entity_or_404(session, entity_id)
    rows = {r.kind: r for r in (
        session.query(ArtworkAsset)
        .filter(ArtworkAsset.entity_id == entity_id).all())}
    items = []
    for kind in ARTWORK_KINDS:
        row = rows.get(kind)
        items.append({
            "kind": kind,
            "exists": row is not None,
            "source": row.source if row else None,
            "locked": bool(row.locked) if row else False,
            "updated_at": row.updated_at.isoformat() if row else None,
        })
    return {"items": items}


class ArtworkLockBody(BaseModel):
    locked: bool


@router.patch("/content/{entity_id}/artwork/{kind}")
async def admin_artwork_lock(entity_id: str, kind: str, body: ArtworkLockBody,
                             request: Request, session: Session = Depends(get_db),
                             _admin=Depends(require_admin_write)):
    from ..models import ArtworkAsset

    if kind not in ARTWORK_KINDS:
        raise invalid_request("kind 取值非法")
    _entity_or_404(session, entity_id)
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
           .one_or_none())
    if row is None:
        raise not_found("该类型暂无图，先上传")
    row.locked = body.locked
    session.commit()
    return {"kind": kind, "locked": row.locked}


@router.delete("/content/{entity_id}/artwork/{kind}")
def admin_artwork_delete(entity_id: str, kind: str, request: Request,
                         session: Session = Depends(get_db),
                         _admin=Depends(require_admin_write)):
    from pathlib import Path

    from ..models import ArtworkAsset

    if kind not in ARTWORK_KINDS:
        raise invalid_request("kind 取值非法")
    _entity_or_404(session, entity_id)
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
           .one_or_none())
    if row is None:
        raise not_found("该类型暂无图")
    config = get_state(request).config
    try:
        (Path(config.data_dir) / row.file_path).unlink(missing_ok=True)
    except OSError:
        pass
    session.delete(row)
    session.commit()
    return {"kind": kind, "deleted": True}


@router.post("/content/{entity_id}/artwork")
async def admin_artwork_upload(entity_id: str, request: Request,
                               session: Session = Depends(get_db),
                               _admin=Depends(require_admin_write)):
    """multipart 上传/换图：kind + locked + file。家长上传永不被刮削覆盖。"""
    from ..media.artwork import artwork_path, generate_from_bytes
    from ..models import ArtworkAsset

    _entity_or_404(session, entity_id)
    form = await request.form()
    kind = str(form.get("kind") or "")
    locked_raw = form.get("locked")
    locked = str(locked_raw).lower() in ("1", "true", "on") if locked_raw else True
    upload = form.get("file")
    if kind not in ARTWORK_KINDS:
        raise invalid_request("kind 取值非法")
    # 属性判定（starlette/fastapi UploadFile 类Identity 不稳定，不上 isinstance）
    if upload is None or isinstance(upload, str):
        raise invalid_request("缺少 file")
    data = await upload.read()
    if len(data) > 8 * 1024 * 1024:
        raise invalid_request("图片过大（>8MB）")
    config = get_state(request).config
    if not generate_from_bytes(config, data, entity_id, kind):
        raise invalid_request("图片处理失败（仅支持 jpg/png/webp）")
    rel = str(artwork_path(config, entity_id, kind).relative_to(config.data_dir))
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
           .one_or_none())
    if row is None:
        row = ArtworkAsset(id=new_id(), entity_id=entity_id, kind=kind)
        session.add(row)
    row.source = "parent"
    row.file_path = rel
    row.locked = locked
    session.commit()
    return {"kind": kind, "source": "parent", "locked": row.locked}


@router.get("/content/{entity_id}/artwork/{kind}/image")
def admin_artwork_image(entity_id: str, kind: str, request: Request,
                        session: Session = Depends(get_db),
                        _admin=Depends(require_admin_read)):
    from pathlib import Path

    from ..models import ArtworkAsset

    if kind not in ARTWORK_KINDS:
        raise invalid_request("kind 取值非法")
    row = (session.query(ArtworkAsset)
           .filter(ArtworkAsset.entity_id == entity_id, ArtworkAsset.kind == kind)
           .one_or_none())
    if row is None:
        raise not_found("该类型暂无图")
    path = Path(get_state(request).config.data_dir) / row.file_path
    if not path.is_file():
        raise not_found("图片文件缺失")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=60"})


class SeriesEditBody(BaseModel):
    # name=None 且显式提交 series 字段 = 解除归组；省略 series 字段 = 不变
    name: str | None = Field(default=None, max_length=256)
    season_no: int | None = Field(default=None, ge=1)
    episode_no: int | None = Field(default=None, ge=1)


class CourseEditBody(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    chapter_no: int | None = Field(default=None, ge=1)
    lesson_no: int | None = Field(default=None, ge=1)


class MediaPatchBody(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    language: str | None = Field(default=None, max_length=32)
    age_band: str | None = Field(default=None, max_length=32)
    media_type: str | None = Field(default=None, pattern="^(episode|movie|lesson)$")
    characters: list[str] | None = Field(default=None, max_length=50)
    themes: list[str] | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=100)
    # 归组（家长修正通道，重扫不覆盖；网络源无 sidecar 时的归组入口）
    series: SeriesEditBody | None = None
    course: CourseEditBody | None = None


EDITABLE_SCALARS = ("title", "language", "age_band", "media_type")


@router.patch("/media/{media_id}")
def admin_media_patch(media_id: str, request: Request, body: MediaPatchBody,
                      session: Session = Depends(get_db), _admin=Depends(require_admin_write)):
    media = session.get(Media, media_id)
    if media is None:
        raise not_found("媒体不存在")
    edited: dict = dict(media.parent_edited_json or {})
    changed = False
    for field in EDITABLE_SCALARS:
        value = getattr(body, field)
        if value is not None:
            edited[field] = value
            setattr(media, field, value)
            changed = True
    tag_groups = {"characters": body.characters, "themes": body.themes, "tags": body.tags}
    if any(v is not None for v in tag_groups.values()):
        merged_tags = dict(edited.get("tags") or {})
        current = dict(media.tags_json or {})
        for group, value in tag_groups.items():
            if value is not None:
                merged_tags[group] = value
                current[group] = value
                changed = True
        edited["tags"] = merged_tags
        media.tags_json = current

    # 系列/课程归组：两者互斥；显式提交 name=None 解除归组（家长修正，重扫不覆盖）
    fields_set = body.model_fields_set
    if "series" in fields_set and "course" in fields_set:
        raise invalid_request("series 与 course 互斥，一次只能归组一种")
    if "series" in fields_set:
        s_spec: SeriesEditBody | None = body.series
        media.auto_series_key = None  # 家长修正接管归组，自动归组标记作废
        if s_spec is None or s_spec.name is None:
            remove_episode(session, media)
            edited["series"] = None
            if media.media_type == "episode":
                media.media_type = "movie"
                edited["media_type"] = "movie"
        else:
            upsert_episode(session, media, s_spec.name, s_spec.season_no, s_spec.episode_no)
            edited["series"] = {"name": s_spec.name}
            if s_spec.season_no is not None:
                edited["series"]["season_no"] = s_spec.season_no
            if s_spec.episode_no is not None:
                edited["series"]["episode_no"] = s_spec.episode_no
            if media.media_type != "episode":
                media.media_type = "episode"
                edited["media_type"] = "episode"
        changed = True
    if "course" in fields_set:
        c_spec: CourseEditBody | None = body.course
        media.auto_series_key = None  # 家长修正接管归组，自动归组标记作废
        if c_spec is None or c_spec.name is None:
            remove_lesson(session, media)
            edited["course"] = None
            if media.media_type == "lesson":
                media.media_type = "movie"
                edited["media_type"] = "movie"
        else:
            upsert_lesson(session, media, c_spec.name, c_spec.chapter_no, c_spec.lesson_no)
            edited["course"] = {"name": c_spec.name}
            if c_spec.chapter_no is not None:
                edited["course"]["chapter_no"] = c_spec.chapter_no
            if c_spec.lesson_no is not None:
                edited["course"]["lesson_no"] = c_spec.lesson_no
            if media.media_type != "lesson":
                media.media_type = "lesson"
                edited["media_type"] = "lesson"
        changed = True

    if changed:
        media.parent_edited_json = edited  # 家长修正成为事实来源，重扫不覆盖（§7.4）
        media.metadata_version += 1
        # v0.3 统一内容目录同步（阶段 1c）：家长修正后 entity 树随之更新
        from ..media.content_catalog import sync_media_entity

        sync_media_entity(session, media)
        session.commit()
    return {
        "media_id": media.id, "metadata_version": media.metadata_version,
        "parent_edited_fields": sorted(edited.keys()),
    }


# ---------- Policy ----------

@router.get("/policy")
def admin_policy_get(request: Request, session: Session = Depends(get_db),
                     _admin=Depends(require_admin_read)):
    state = get_state(request)
    rules, version = state.policy.current(session)
    return {"version": version, "rules": rules.to_json()}


@router.put("/policy")
def admin_policy_put(request: Request, body: dict, session: Session = Depends(get_db),
                     _admin=Depends(require_admin_write)):
    """Body 即规则 JSON（dict 参数经 FastAPI/Pydantic 校验：非法 JSON → 400 invalid_request）。
    保存后 version+1，撤销受影响 Grant 并推送 stop/deny（立即生效）。"""
    state = get_state(request)
    if not isinstance(body, dict):
        raise invalid_request("规则必须是 JSON 对象")
    try:
        rules, version = state.policy.save(session, body)
    except (TypeError, ValueError) as exc:
        raise invalid_request(f"规则不合法: {exc}") from exc
    revoked = state.playback.on_policy_saved(session, version)
    session.commit()
    return {
        "version": version, "rules": rules.to_json(),
        "revoked_playbacks": revoked,
        "note": "规则已保存并立即生效；进行中播放如被硬性限制将收到 stop/deny",
    }


# ---------- Providers / active model ----------

@router.get("/providers")
def admin_providers(request: Request, session: Session = Depends(get_db),
                    _admin=Depends(require_admin_read)):
    """全部来源（config|page）的非敏感状态；api_key 仅 configured/masked_hint（写-only）。"""
    state = get_state(request)
    active_id, _ = state.active_model()
    items = []
    for v in state.provider_registry.all():
        item = v.public()
        item["base_url_configured"] = bool(v.base_url)
        item["active"] = v.id == active_id
        items.append(item)
    return {"providers": items, "active_provider_id": active_id}


class ProviderBody(BaseModel):
    id: str | None = None
    display_name: str
    protocol: str = "openai_chat_completions"
    base_url: str
    model: str
    api_key: str | None = None  # 写-only：留空/缺省表示不修改
    enabled: bool | None = None  # 停用开关：None=不修改（停用保留密钥，区别于删除）


def _validate_provider_body(body: ProviderBody) -> None:
    if body.protocol != "openai_chat_completions":
        raise invalid_request("V0.1 仅支持 openai_chat_completions 协议")
    if not body.base_url.startswith(("http://", "https://")):
        raise invalid_request("base_url 必须以 http(s):// 开头")


@router.post("/providers")
def admin_provider_create(request: Request, body: ProviderBody,
                          session: Session = Depends(get_db),
                          _admin=Depends(require_admin_write)):
    state = get_state(request)
    _validate_provider_body(body)
    provider_id = (body.id or "").strip()
    if not provider_id:  # 省略 id 时自动生成（显示名可为中文，不作 id）
        from ..util import new_id

        provider_id = "p-" + new_id()[:12]
    if any(c not in SLUG_SAFE for c in provider_id):
        raise invalid_request("Provider id 仅允许字母/数字/-/_（可省略，由系统生成）")
    from ..models import LlmProviderRow

    if session.get(LlmProviderRow, provider_id) is not None:
        raise conflict(f"页面 Provider {provider_id} 已存在")
    # 同 id 覆盖配置文件来源是允许的（架构 A-12：页面配置优先）
    row = LlmProviderRow(
        id=provider_id, display_name=body.display_name, protocol=body.protocol,
        base_url=body.base_url.rstrip("/"), model=body.model,
        api_key=secretbox.encrypt_str(body.api_key or ""),
        enabled=body.enabled if body.enabled is not None else True,
    )
    session.add(row)
    session.commit()
    state.provider_registry.reload()  # 立即生效（技术方案 v0.2.1 §12.3）
    out = state.provider_registry.get(provider_id)
    return out.public() if out else {"provider_id": provider_id}


@router.patch("/providers/{provider_id}")
def admin_provider_patch(provider_id: str, request: Request, body: ProviderBody,
                         session: Session = Depends(get_db),
                         _admin=Depends(require_admin_write)):
    """编辑非敏感参数；api_key 仅在显式提交非空值时更换（写-only，不回显）。"""
    from ..models import LlmProviderRow

    state = get_state(request)
    _validate_provider_body(body)
    row = session.get(LlmProviderRow, provider_id)
    if row is None:
        raise not_found(
            f"页面 Provider {provider_id} 不存在（配置文件来源的 Provider 不可经页面编辑）")
    row.display_name = body.display_name
    row.protocol = body.protocol
    row.base_url = body.base_url.rstrip("/")
    row.model = body.model
    if body.api_key:  # 空/缺省 = 保持不变
        row.api_key = secretbox.encrypt_str(body.api_key)
    if body.enabled is not None:
        row.enabled = body.enabled
        if not body.enabled:
            # 停用当前激活的 Provider → 清空激活（与删除同语义；TV ai_available 随之变化）
            active_id, _ = state.active_model()
            if active_id == provider_id:
                setting = session.get(AppSetting, "active_model")
                if setting is not None:
                    session.delete(setting)
    session.commit()
    state.provider_registry.reload()
    out = state.provider_registry.get(provider_id)
    return out.public() if out else {"provider_id": provider_id}


@router.delete("/providers/{provider_id}")
def admin_provider_delete(provider_id: str, request: Request,
                          session: Session = Depends(get_db),
                          _admin=Depends(require_admin_write)):
    from ..models import LlmProviderRow

    state = get_state(request)
    row = session.get(LlmProviderRow, provider_id)
    if row is None:
        raise not_found(f"Provider {provider_id} 不存在")
    active_id, _ = state.active_model()
    if active_id == provider_id:
        # 删除当前模型 → 自动清空激活（下一会话前需另选，删除不再被卡死）
        from ..models import AppSetting

        setting = session.get(AppSetting, "active_model")
        if setting is not None:
            session.delete(setting)
    session.delete(row)
    session.commit()
    state.provider_registry.reload()
    return {"provider_id": provider_id, "deleted": True}


@router.post("/providers/{provider_id}/test")
async def admin_provider_test(provider_id: str, request: Request,
                              session: Session = Depends(get_db),
                              _admin=Depends(require_admin_write)):
    """连通性检查：向 chat/completions 发送最小非流式请求（§3.3）。"""
    import httpx

    state = get_state(request)
    v = state.provider_registry.get(provider_id)
    if v is None:
        raise not_found("Provider 不存在")
    url = v.base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if v.api_key:
        headers["Authorization"] = f"Bearer {v.api_key}"
    payload = {
        "model": v.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1, "stream": False, "store": False,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception as exc:
        return {"provider_id": provider_id, "result": "unreachable", "detail": str(exc)[:150]}
    if resp.status_code in (200, 201):
        return {"provider_id": provider_id, "result": "ok"}
    detail = resp.text[:150]
    if resp.status_code in (401, 403):
        return {"provider_id": provider_id, "result": "auth_failed",
                "detail": "API Key 无效或无权限", "http": resp.status_code}
    return {"provider_id": provider_id, "result": "error",
            "detail": detail, "http": resp.status_code}


class ActiveModelBody(BaseModel):
    provider_id: str


@router.post("/active-model")
def admin_active_model(request: Request, body: ActiveModelBody,
                       session: Session = Depends(get_db), _admin=Depends(require_admin_write)):
    state = get_state(request)
    if state.provider_registry.get(body.provider_id) is None:
        raise not_found("Provider 不存在（未在配置声明，也未由页面添加）")
    session.merge(AppSetting(key="active_model", value_json={"provider_id": body.provider_id}))
    session.commit()
    return {
        "active_provider_id": body.provider_id,
        "note": "仅影响新 Conversation Session；现有会话继续使用原模型（§7.2）",
    }


# ---------- ASR 热词（ASR-005 从内容元数据自动构建） ----------

@router.get("/asr/hotwords")
def asr_hotwords_status(request: Request, _admin=Depends(require_admin_read)):
    from .. import asr_words

    state = get_state(request)
    info = asr_words.hotwords_status(state.config)
    info["note"] = ("kindo-asr 的 KINDO_ASR_HOTWORDS_FILE 需指向同一文件，"
                    "修改后重启 kindo-asr 容器生效")
    return info


@router.post("/asr/hotwords/rebuild")
def asr_hotwords_rebuild(request: Request, session: Session = Depends(get_db),
                         _admin=Depends(require_admin_write)):
    """从媒体库重建热词表（系列/课程/作品名 + 角色/主题）；手工补写行保留。"""
    from .. import asr_words

    state = get_state(request)
    out = asr_words.write_hotwords(state.config, session)
    out["note"] = "重建完成；重启 kindo-asr 容器后生效（热词在识别器初始化时加载）"
    return out


# ---------- Analytics ----------

def _parse_custom_range(start: str | None, end: str | None, tz):
    """YYYY-MM-DD → (start_utc, end_utc含当日全天)；无效返回 None。"""
    from datetime import UTC, datetime, time, timedelta

    if not start or not end:
        raise invalid_request("自定义范围需同时提供 start 与 end（YYYY-MM-DD）")
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise invalid_request("start/end 格式为 YYYY-MM-DD") from exc
    if s > e:
        raise invalid_request("start 不得晚于 end")
    if (e - s).days > 366:
        raise invalid_request("自定义范围最长 366 天")
    start_dt = datetime.combine(s, time.min, tzinfo=tz).astimezone(UTC)
    end_dt = (datetime.combine(e, time.min, tzinfo=tz)
              + timedelta(days=1) - timedelta(milliseconds=1)).astimezone(UTC)
    return start_dt, end_dt


@router.get("/analytics")
def admin_analytics(request: Request, period: str = "day",
                    start: str | None = None, end: str | None = None,
                    session: Session = Depends(get_db),
                    _admin=Depends(require_admin_read)):
    from datetime import datetime

    if period not in ("day", "week", "custom"):
        raise invalid_request("period 只支持 day|week|custom")
    state = get_state(request)
    profile_id = state.playback.default_profile_id(session)
    custom = None
    if period == "custom":
        custom = _parse_custom_range(start, end, state.history.tz)
    data = state.history.analytics(session, profile_id, period, datetime.now(UTC),
                                   custom=custom)
    data["note"] = "统计仅描述可观察行为，不产生任何心理/能力/医学推断（ANA-005）"
    return data


@router.get("/analytics/interest")
def admin_interest_signals(request: Request, period: str = "week",
                           start: str | None = None, end: str | None = None,
                           session: Session = Depends(get_db),
                           _admin=Depends(require_admin_read)):
    """兴趣信号与接力观测（v0.3 ANA-007/008）：只读客观行为，无推断结论。
    period=custom 时按 start/end（YYYY-MM-DD）过滤。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func

    from ..models import ContentEntity, ContentTopic, InterestSignal, TransitionSession

    state = get_state(request)
    profile_id = state.playback.default_profile_id(session)
    if period == "custom":
        cs, ce = _parse_custom_range(start, end, state.history.tz)
        since, until = cs, ce
    else:
        days = 7 if period == "week" else 1
        since = datetime.now(UTC) - timedelta(days=days)
        until = datetime.now(UTC)

    def _sig_filters():
        return (InterestSignal.profile_id == profile_id,
                InterestSignal.created_at >= since,
                InterestSignal.created_at <= until)

    by_type = {
        r[0]: r[1] for r in (
            session.query(InterestSignal.signal_type, func.count(InterestSignal.id))
            .filter(*_sig_filters())
            .group_by(InterestSignal.signal_type).all())
    }
    by_source = {
        r[0]: r[1] for r in (
            session.query(InterestSignal.source, func.count(InterestSignal.id))
            .filter(*_sig_filters())
            .group_by(InterestSignal.source).all())
    }
    topic_rows = (
        session.query(ContentTopic.name, func.count(InterestSignal.id),
                      func.max(InterestSignal.created_at))
        .join(InterestSignal, InterestSignal.topic_id == ContentTopic.id)
        .filter(*_sig_filters())
        .group_by(ContentTopic.name)
        .order_by(func.count(InterestSignal.id).desc())
        .limit(10).all())
    entity_rows = (
        session.query(ContentEntity.title, func.count(InterestSignal.id),
                      func.max(InterestSignal.created_at))
        .join(InterestSignal, InterestSignal.entity_id == ContentEntity.id)
        .filter(*_sig_filters())
        .group_by(ContentEntity.title)
        .order_by(func.count(InterestSignal.id).desc())
        .limit(10).all())
    ts_rows = (
        session.query(TransitionSession)
        .filter(TransitionSession.profile_id == profile_id,
                TransitionSession.created_at >= since,
                TransitionSession.created_at <= until)
        .order_by(TransitionSession.created_at.desc())
        .limit(100).all())
    accepted = [t for t in ts_rows if t.accepted]
    rejected = [t for t in ts_rows if t.rejected]
    by_end: dict[str, int] = {}
    for t in ts_rows:
        if t.ended_reason:
            by_end[t.ended_reason] = by_end.get(t.ended_reason, 0) + 1
    return {
        "period": period,
        "signal_counts_by_type": by_type,
        "signal_counts_by_source": by_source,
        "top_topics": [
            {"topic": r[0], "count": r[1], "last_at": r[2].isoformat()}
            for r in topic_rows],
        "top_entities": [
            {"title": r[0], "count": r[1], "last_at": r[2].isoformat()}
            for r in entity_rows],
        "transition": {
            "total": len(ts_rows),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "avg_ai_voice_seconds": (
                sum(t.ai_voice_ms for t in accepted) // 1000) if accepted else 0,
            "ended_reasons": by_end,
        },
    }


# ---------- Devices ----------

@router.get("/devices")
def admin_devices(request: Request, session: Session = Depends(get_db),
                  _admin=Depends(require_admin_read)):
    state = get_state(request)
    devices = session.query(Device).all()
    # 在线优先 + 最近活跃在前（与概览页一致）
    devices.sort(key=lambda d: (
        not state.realtime.is_online(d.id),
        -(d.last_seen_at.timestamp() if d.last_seen_at else 0),
    ))
    return {
        "devices": [
            {
                "device_id": d.id, "name": d.name, "status": d.status,
                "capabilities": d.capabilities_json,
                "paired_at": d.paired_at.isoformat(),
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                "online": state.realtime.is_online(d.id),
            }
            for d in devices
        ]
    }


@router.post("/devices/{device_id}/revoke")
def admin_device_revoke(device_id: str, request: Request, session: Session = Depends(get_db),
                        _admin=Depends(require_admin_write)):
    state = get_state(request)
    device = session.get(Device, device_id)
    if device is None:
        raise not_found("设备不存在")
    device.status = "revoked"
    session.commit()
    # 级联：撤销 active playback + Grant + 断开 Realtime（§3.2）
    state.playback.on_device_revoked(session, device_id)
    state.realtime.close_device(device_id)
    return {"device_id": device_id, "status": "revoked"}


class DeviceCleanupBody(BaseModel):
    revoked: bool = True  # 清理已撤销的设备
    offline_days: int = Field(default=7, ge=1, le=365)  # 清理 N 天未活跃的设备


@router.post("/devices/cleanup")
def admin_devices_cleanup(request: Request, body: DeviceCleanupBody,
                          session: Session = Depends(get_db),
                          _admin=Depends(require_admin_write)):
    """批量清理测试残留/废弃设备（硬删除配对记录）：已撤销 + 超过 N 天未活跃。
    在线设备永不清理；被清理设备想再次使用需重新配对。"""
    from datetime import timedelta

    state = get_state(request)
    now = datetime.now(UTC)
    threshold = now - timedelta(days=body.offline_days)
    doomed: list[Device] = []
    for d in session.query(Device).all():
        if state.realtime.is_online(d.id):
            continue
        if body.revoked and d.status == "revoked":
            doomed.append(d)
            continue
        last = d.last_seen_at or d.paired_at  # 从未活跃的按配对时间算
        if last is not None and last < threshold:
            doomed.append(d)
    names = [d.name for d in doomed]
    for d in doomed:
        session.delete(d)
    session.commit()
    return {"deleted": len(doomed), "devices": names,
            "note": "在线设备不会被清理；被清理设备需重新配对"}

# ---------- 活动库管理（v0.3 决策七 7.3，ADM-014） ----------

@router.get("/activities")
def admin_activities(request: Request, session: Session = Depends(get_db),
                     _admin=Depends(require_admin_read)):
    from ..models import TransitionActivity

    rows = (session.query(TransitionActivity)
            .order_by(TransitionActivity.created_at.desc()).limit(200).all())
    return {"items": [
        {"id": a.id, "title": a.title, "summary": a.summary,
         "topics_json": a.topics_json or [], "age_min": a.age_min,
         "age_max": a.age_max, "source": a.source, "status": a.status}
        for a in rows
    ]}


class ActivityBody(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=2000)
    topics: list[str] = Field(default_factory=list, max_length=8)
    age_min: int | None = Field(default=None, ge=0, le=18)
    age_max: int | None = Field(default=None, ge=0, le=18)


@router.post("/activities")
def admin_activity_create(request: Request, body: ActivityBody,
                          session: Session = Depends(get_db),
                          _admin=Depends(require_admin_write)):
    from ..models import TransitionActivity

    row = TransitionActivity(
        id=new_id(), title=body.title, summary=body.summary,
        topics_json=body.topics, age_min=body.age_min, age_max=body.age_max,
        source="parent", status="published", created_by="admin",
        created_at=datetime.now(UTC))
    session.add(row)
    session.commit()
    return {"id": row.id, "status": row.status}


@router.post("/activities/{activity_id}/publish")
def admin_activity_publish(activity_id: str, request: Request,
                           session: Session = Depends(get_db),
                           _admin=Depends(require_admin_write)):
    """draft → published：家长确认后进入接力推荐池。"""
    from ..models import TransitionActivity

    row = session.get(TransitionActivity, activity_id)
    if row is None:
        raise not_found("活动不存在")
    if row.status == "draft":
        row.status = "published"
        session.commit()
    return {"id": row.id, "status": row.status}


class ActivityPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=2000)
    topics: list[str] | None = Field(default=None, max_length=8)
    age_min: int | None = Field(default=None, ge=0, le=18)
    age_max: int | None = Field(default=None, ge=0, le=18)


@router.patch("/activities/{activity_id}")
def admin_activity_patch(activity_id: str, request: Request, body: ActivityPatchBody,
                         session: Session = Depends(get_db),
                         _admin=Depends(require_admin_write)):
    """编辑活动（草稿修订 / 家长自建修正；builtin 模板只读防误删基础池）。"""
    from ..models import TransitionActivity

    row = session.get(TransitionActivity, activity_id)
    if row is None:
        raise not_found("活动不存在")
    if row.source == "builtin":
        raise invalid_request("内置模板不可编辑（可复制为自建活动）")
    for field in ("title", "summary", "age_min", "age_max"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.topics is not None:
        row.topics_json = body.topics
    session.commit()
    return {"id": row.id, "status": row.status}


@router.delete("/activities/{activity_id}")
def admin_activity_delete(activity_id: str, request: Request,
                          session: Session = Depends(get_db),
                          _admin=Depends(require_admin_write)):
    from ..models import TransitionActivity

    row = session.get(TransitionActivity, activity_id)
    if row is None:
        raise not_found("活动不存在")
    if row.source == "builtin":
        raise invalid_request("内置模板不可删除")
    session.delete(row)
    session.commit()
    return {"id": activity_id, "deleted": True}


# ---------- Policy 今日剩余预览（交互 §8.1 / §9“保存预览今日各维度剩余”） ----------

@router.get("/policy/usage")
def admin_policy_usage(request: Request, session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    """今日各维度剩余量（保存前后对照预览；与判定引擎同一计量口径）。"""
    from datetime import datetime

    state = get_state(request)
    profile_id = state.playback.default_profile_id(session)
    rules, version = state.policy.current(session)
    now = datetime.now(UTC)

    def _dim(dims: tuple[str | None, str | None], keys: list[str]) -> dict:
        remaining = state.policy.budget_remaining(
            session, profile_id, rules, now, dims=dims)
        return {k: remaining.get(k) for k in keys}

    video_ent = _dim(("ENTERTAINMENT", "VIDEO"),
                     ["screen_total_seconds", "video_class_seconds"])
    video_learn = _dim(("LEARNING", "VIDEO"), ["video_class_seconds"])
    audio = _dim((None, "AUDIO"), ["audio_seconds"])
    ai_voice = _dim((None, "AI_VOICE"), ["ai_voice_seconds"])
    # 今日接力发起次数（与频控同口径：started_at 非空）
    from ..models import TransitionSession

    day_start, _end = state.policy._local_day_bounds(now)
    offered = (session.query(TransitionSession)
               .filter(TransitionSession.profile_id == profile_id,
                       TransitionSession.created_at >= day_start,
                       TransitionSession.started_at.isnot(None))
               .count())
    return {
        "policy_version": version,
        "video_entertainment": video_ent,
        "video_learning": video_learn,
        "audio": audio,
        "ai_voice": ai_voice,
        "transition_offered_today": offered,
        "transition_daily_limit": rules.transition_daily_offer_limit(),
        "note": "与 TV 端判定同源（budget_remaining）；软限制不打断进行中的当前集",
    }


# ---------- 家长 AI 助手（PRD 8.14 AIA / 技术方案 §19；实施计划 S1：Curator） ----------

class AiJobCreateBody(BaseModel):
    job_type: str = Field(min_length=3, max_length=32)


class AiBatchApplyBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    # 清单式一次确认（交互 v0.3.3）：界面完整呈现每条前后值后一次确认高影响建议
    allow_high: bool = False


def _ai_jobs(request: Request):
    return get_state(request)._extra["ai_jobs"]


def _ai_proposals(request: Request):
    return get_state(request)._extra["ai_proposals"]


def _ai_job_view(job) -> dict:
    return {
        "job_id": job.id, "job_type": job.job_type, "state": job.state,
        "progress": job.progress, "result_summary": job.result_summary,
        "error_summary": job.error_summary,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _ai_proposal_view(session: Session, row) -> dict:
    payload = row.payload_json or {}
    entity = session.get(ContentEntity, payload.get("entity_id") or "")
    # POLICY 建议：服务端事实核对的变更前后值（交互 §8.2.1 高影响示例；
    # 不信任 LLM 自述文案——S2 评审 M-1 修复）
    policy_diff: list[str] | None = None
    if row.proposal_type == "POLICY":
        from ..ai.proposals import policy_diff_lines
        from ..models import PolicyConfig
        from ..policy.engine import PolicyRules

        pc = (session.query(PolicyConfig)
              .order_by(PolicyConfig.version.desc()).first())
        current_json = (pc.rules_json if pc is not None and pc.rules_json
                        else PolicyRules.parse(None).to_json())  # 全新库无行 → 默认规则
        policy_diff = policy_diff_lines(current_json,
                                        payload.get("rules_patch") or {})
    return {
        "proposal_id": row.id, "proposal_type": row.proposal_type,
        "impact_level": row.impact_level, "status": row.status,
        "profile": row.profile, "job_id": row.job_id,
        "summary": row.summary,
        "summary_parts": payload.get("summary") or {},
        "changes": {k: v for k, v in payload.items()
                    if k not in ("basis", "summary")},
        "policy_diff": policy_diff,
        "entity_id": payload.get("entity_id"),
        "entity_title": entity.title if entity else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
    }


@router.post("/ai/jobs")
def admin_ai_job_create(body: AiJobCreateBody, request: Request,
                        session: Session = Depends(get_db),
                        _admin=Depends(require_admin_write)):
    """创建 AI 分析任务（家长显式触发；同类单飞 409；LLM 未就绪 503）。"""
    job_id = _ai_jobs(request).start(body.job_type)
    return {"job_id": job_id, "state": "queued"}


@router.get("/ai/jobs")
def admin_ai_jobs(request: Request, job_type: str | None = None,
                  status: str | None = None, limit: int = 20,
                  session: Session = Depends(get_db),
                  _admin=Depends(require_admin_read)):
    """任务历史与结果。概览卡片数据 = job_type=USAGE_SUMMARY&status=done&limit=1
    取最近 result_summary（不设独立 summary 端点）。"""
    rows = _ai_jobs(request).list_jobs(job_type=job_type, status=status, limit=limit)
    return {"items": [_ai_job_view(j) for j in rows]}


@router.get("/ai/jobs/{job_id}")
def admin_ai_job_get(job_id: str, request: Request,
                     session: Session = Depends(get_db),
                     _admin=Depends(require_admin_read)):
    job = _ai_jobs(request).get_job(job_id)
    if job is None:
        raise not_found("AI 任务不存在")
    return _ai_job_view(job)


@router.get("/ai/proposals")
def admin_ai_proposals(request: Request,
                       status: str = "PENDING", impact_level: str | None = None,
                       proposal_type: str | None = None,
                       job_id: str | None = None, limit: int = 200,
                       session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    from ..models import AiProposal

    q = session.query(AiProposal).filter(AiProposal.status == status)
    if impact_level:
        q = q.filter(AiProposal.impact_level == impact_level)
    if proposal_type:
        q = q.filter(AiProposal.proposal_type == proposal_type)
    if job_id:
        q = q.filter(AiProposal.job_id == job_id)
    rows = q.order_by(AiProposal.created_at.desc()).limit(limit).all()
    return {"items": [_ai_proposal_view(session, r) for r in rows]}


@router.post("/ai/proposals/{proposal_id}/reject")
def admin_ai_proposal_reject(proposal_id: str, request: Request,
                             session: Session = Depends(get_db),
                             _admin=Depends(require_admin_write)):
    """拒绝/忽略（AIA-008：同建议不再重复提醒）。"""
    row = _ai_proposals(request).reject(session, proposal_id)
    session.commit()
    return {"proposal_id": row.id, "status": row.status}


@router.post("/ai/proposals/{proposal_id}/apply")
def admin_ai_proposal_apply(proposal_id: str, request: Request,
                            session: Session = Depends(get_db),
                            _admin=Depends(require_admin_write)):
    """应用单条建议（HIGH 单决策；执行前重读事实，过期 → EXPIRED）。"""
    result = _ai_proposals(request).apply_one(session, proposal_id)
    session.commit()
    return result


@router.post("/ai/proposals/batch-apply")
def admin_ai_proposals_batch_apply(body: AiBatchApplyBody, request: Request,
                                   session: Session = Depends(get_db),
                                   _admin=Depends(require_admin_write)):
    """批量一次应用（AIA-002）：默认仅 LOW；allow_high=true 为清单式一次确认
    （高影响建议在界面完整呈现前后值后一次确认）；混入已处理/方向性建议 → 400
    整体不执行；逐条独立事务，部分 EXPIRED 不影响其余。"""
    results = _ai_proposals(request).batch_apply(body.ids, allow_high=body.allow_high)
    return {"results": results,
            "note": "建议已批量应用（含清单式确认的高影响项）"}

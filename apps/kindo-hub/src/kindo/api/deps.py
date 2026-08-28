"""API 依赖与鉴权（技术方案 §2.2 身份矩阵）。"""
from __future__ import annotations

from fastapi import Depends, Request, WebSocket

from ..errors import unauthorized_admin, unauthorized_device
from ..models import Device
from ..pairing import authenticate_device


def get_state(request: Request):
    return request.app.state.kindo


def get_db(request: Request):
    state = get_state(request)
    session = state.db.session()
    try:
        yield session
    finally:
        session.close()


def device_from_request(request: Request, session=Depends(get_db)) -> Device:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise unauthorized_device("缺少 Device Token")
    return authenticate_device(session, auth[7:].strip())


def ws_device_token(ws: WebSocket) -> str | None:
    auth = ws.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ws.query_params.get("token")


ADMIN_COOKIE = "kindo_admin_session"


def admin_session_id(request: Request) -> str:
    sid = request.cookies.get(ADMIN_COOKIE)
    if not sid:
        raise unauthorized_admin("未登录")
    return sid


def require_admin_read(request: Request, session=Depends(get_db)):
    state = get_state(request)
    sid = admin_session_id(request)
    return state.admin_auth.authenticate(session, sid)


def require_admin_write(request: Request, session=Depends(get_db)):
    state = get_state(request)
    sid = admin_session_id(request)
    row = state.admin_auth.authenticate(session, sid)
    state.admin_auth.verify_csrf(row, request.headers.get("X-CSRF-Token"))
    return row


class RangeUnsatisfiable(Exception):
    """Range 超出资源长度（HTTP 416）。"""


def parse_range_header(header: str | None, size: int) -> tuple[int, int | None] | None:
    """单 Range 解析（§9.4）。返回 (start, end_inclusive|None)；无/非法 Range 返回 None（按 200 处理）。"""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[6:].strip()
    if "," in spec:
        return None  # V0.1 不实现 multipart/byteranges
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        # suffix range: bytes=-N
        if not end_s.isdigit():
            return None
        n = int(end_s)
        if n <= 0 or size == 0:
            raise RangeUnsatisfiable()
        start = max(0, size - n)
        return (start, size - 1)
    if not start_s.isdigit():
        return None
    start = int(start_s)
    if start >= size:
        raise RangeUnsatisfiable()
    if end_s == "":
        return (start, None)
    if not end_s.isdigit():
        return None
    end = int(end_s)
    if end < start:
        return None
    return (start, min(end, size - 1))

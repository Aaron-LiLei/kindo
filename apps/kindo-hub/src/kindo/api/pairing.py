"""Pairing 接口（技术方案 §3.2）。未配对设备只能访问 pairing/info 与 pairing/requests。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..util import now_iso
from .deps import get_db, get_state, require_admin_read, require_admin_write

router = APIRouter(prefix="/api/v1", tags=["pairing"])


@router.get("/pairing/info")
def pairing_info(request: Request, session: Session = Depends(get_db)):
    state = get_state(request)
    return {
        "instance_id": state.instance_id,
        "display_name": state.config.instance_display_name,
        "api_version": 1,
        "pairing_enabled": True,
        "server_time": now_iso(),
    }


class PairingRequestBody(BaseModel):
    device_name: str
    app_instance_id: str
    capabilities: dict | None = None


@router.post("/pairing/requests")
def pairing_create(request: Request, body: PairingRequestBody,
                   session: Session = Depends(get_db)):
    state = get_state(request)
    return state.pairing.create_request(
        session, body.device_name, body.app_instance_id, body.capabilities or {}
    )


@router.get("/pairing/requests/{pairing_id}")
def pairing_status(pairing_id: str, request: Request, pairing_secret: str = "",
                   session: Session = Depends(get_db)):
    state = get_state(request)
    secret = pairing_secret or request.headers.get("X-Pairing-Secret", "")
    return state.pairing.get_status(session, pairing_id, secret)


# ---------- Admin 侧 ----------

@router.get("/admin/pairing/requests")
def admin_pairing_list(request: Request, session: Session = Depends(get_db),
                       _admin=Depends(require_admin_read)):
    state = get_state(request)
    return {"pending": state.pairing.pending(session)}


class ApproveBody(BaseModel):
    confirm_code: str | None = None


@router.post("/admin/pairing/requests/{pairing_id}/approve")
def admin_pairing_approve(pairing_id: str, request: Request, body: ApproveBody | None = None,
                          session: Session = Depends(get_db), _admin=Depends(require_admin_write)):
    state = get_state(request)
    return state.pairing.approve(session, pairing_id, (body.confirm_code if body else None))


@router.post("/admin/pairing/requests/{pairing_id}/deny")
def admin_pairing_deny(pairing_id: str, request: Request,
                       session: Session = Depends(get_db), _admin=Depends(require_admin_write)):
    state = get_state(request)
    return state.pairing.deny(session, pairing_id)

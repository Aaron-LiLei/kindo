"""Device Pairing（技术方案 §3.2 / §14.2）。

未配对设备只能访问 pairing/info 与 pairing/requests；display_code 6 位、
5 分钟有效；家长核对 display_code 后批准；device_token 仅在批准后首次
GET status 时返回一次（库存 hash）。撤销设备级联撤销 Realtime/Grant/active playback。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .errors import invalid_request, not_found, unauthorized_device
from .models import Device, PairingRequest
from .security import constant_time_eq, new_device_token, sha256_hex
from .util import new_id, random_digits

logger = logging.getLogger("kindo.pairing")

PAIRING_TTL_SECONDS = 300


class PairingService:
    def create_request(self, session: Session, device_name: str, app_instance_id: str,
                       capabilities: dict) -> dict:
        if not device_name or not app_instance_id:
            raise invalid_request("device_name 与 app_instance_id 必填")
        pairing_secret = new_device_token()[:32]
        row = PairingRequest(
            id=new_id(),
            device_name=device_name[:120],
            app_instance_id=app_instance_id[:120],
            capabilities_json=capabilities or {},
            display_code=random_digits(6),
            secret_hash=sha256_hex(pairing_secret),
            state="pending",
            expires_at=datetime.now(UTC) + timedelta(seconds=PAIRING_TTL_SECONDS),
        )
        session.add(row)
        session.commit()
        return {
            "pairing_id": row.id,
            "display_code": row.display_code,
            "pairing_secret": pairing_secret,  # 仅本次返回
            "expires_in_seconds": PAIRING_TTL_SECONDS,
        }

    def get_status(self, session: Session, pairing_id: str, secret: str) -> dict:
        row = session.get(PairingRequest, pairing_id)
        if row is None:
            raise not_found("配对请求不存在")
        if not constant_time_eq(sha256_hex(secret), row.secret_hash):
            raise unauthorized_device("pairing_secret 不正确")
        if row.state == "pending" and row.expires_at < datetime.now(UTC):
            row.state = "expired"
            session.commit()
        out: dict = {
            "pairing_id": row.id,
            "state": row.state,
            "device_name": row.device_name,
        }
        # 明文 device_token 仅一次返回（§14.2）
        if row.state == "approved" and row.pending_token:
            out["device_token"] = row.pending_token
            row.pending_token = None
            session.commit()
        return out

    def approve(self, session: Session, pairing_id: str,
                confirm_code: str | None = None) -> dict:
        row = session.get(PairingRequest, pairing_id)
        if row is None:
            raise not_found("配对请求不存在")
        if row.state != "pending":
            raise invalid_request(f"配对请求状态为 {row.state}，不可批准")
        if row.expires_at < datetime.now(UTC):
            row.state = "expired"
            session.commit()
            raise invalid_request("配对请求已过期，请让设备重新发起")
        if confirm_code is not None and confirm_code != row.display_code:
            raise invalid_request("display_code 不匹配")
        token = new_device_token()
        device = Device(
            id=new_id(),
            name=row.device_name,
            token_hash=sha256_hex(token),
            status="active",
            capabilities_json=row.capabilities_json,
        )
        session.add(device)
        row.state = "approved"
        row.approved_device_id = device.id
        row.pending_token = token  # 等 TV 首次拉取
        session.commit()
        logger.info("设备配对批准 device=%s name=%s", device.id, device.name)
        return {"pairing_id": row.id, "device_id": device.id, "display_code": row.display_code}

    def deny(self, session: Session, pairing_id: str) -> dict:
        row = session.get(PairingRequest, pairing_id)
        if row is None:
            raise not_found("配对请求不存在")
        row.state = "denied"
        session.commit()
        return {"pairing_id": row.id, "state": row.state}

    def pending(self, session: Session) -> list[dict]:
        now = datetime.now(UTC)
        rows = session.query(PairingRequest).filter(PairingRequest.state == "pending").all()
        return [
            {
                "pairing_id": r.id,
                "device_name": r.device_name,
                "app_instance_id": r.app_instance_id,
                "display_code": r.display_code,
                "capabilities": r.capabilities_json,
                "expires_at": r.expires_at.isoformat(),
                "expired": r.expires_at < now,
            }
            for r in rows
        ]


def authenticate_device(session: Session, token: str) -> Device:
    device = (
        session.query(Device)
        .filter(Device.token_hash == sha256_hex(token), Device.status == "active")
        .one_or_none()
    )
    if device is None:
        raise unauthorized_device()
    device.last_seen_at = datetime.now(UTC)
    session.commit()
    return device

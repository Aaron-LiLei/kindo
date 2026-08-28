"""通用工具：UUID、时间、哈希。"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_opaque_token(nbytes: int = 32) -> str:
    raw = secrets.token_bytes(nbytes)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def random_digits(n: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))

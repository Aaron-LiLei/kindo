"""安全原语：密码哈希（Argon2id）、token 生成与校验。"""
from __future__ import annotations

import hmac

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .util import new_opaque_token, sha256_hex

_ph = PasswordHasher()  # 默认 Argon2id


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_device_token() -> str:
    return new_opaque_token(32)


def token_hash(token: str) -> str:
    return sha256_hex(token)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())

"""统一错误模型（技术方案 §2.3 / §15.2）。

面向儿童的文案不直接使用 message；TV 依据 reason_code 做本地映射。
"""
from __future__ import annotations

from typing import Any


class KindoError(Exception):
    def __init__(
        self,
        code: str,
        http_status: int,
        message: str,
        *,
        reason_code: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.reason_code = reason_code
        self.retryable = retryable
        self.details = details or {}
        self.constraints = constraints

    def envelope(self, request_id: str) -> dict[str, Any]:
        err: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.reason_code:
            err["reason_code"] = self.reason_code
        if self.constraints is not None:
            err["constraints"] = self.constraints
        if self.details:
            err["details"] = self.details
        return {"error": err, "request_id": request_id}


def invalid_request(message: str, details: dict[str, Any] | None = None) -> KindoError:
    return KindoError("invalid_request", 400, message, details=details)


def unauthorized_device(message: str = "device token 无效或已撤销") -> KindoError:
    return KindoError("unauthorized_device", 401, message)


def unauthorized_admin(message: str = "管理会话无效或已过期") -> KindoError:
    return KindoError("unauthorized_admin", 401, message)


def forbidden_admin(message: str = "需要管理员权限或 CSRF 校验失败") -> KindoError:
    return KindoError("forbidden_admin", 403, message)


def policy_denied(reason_code: str, constraints: dict[str, Any], message: str) -> KindoError:
    return KindoError(
        "policy_denied", 403, message, reason_code=reason_code,
        retryable=False, constraints=constraints,
    )


def grant_invalid(message: str = "Playback Grant 已失效") -> KindoError:
    return KindoError(
        "grant_invalid", 401, message,
        reason_code="grant_invalid",
        details={"playback_state_sync": True},
    )


def grant_mismatch() -> KindoError:
    return KindoError("grant_mismatch", 403, "Grant 与 device/media/playback 不匹配")


def not_found(message: str = "资源不存在") -> KindoError:
    return KindoError("not_found", 404, message)


def conflict(message: str, details: dict[str, Any] | None = None) -> KindoError:
    return KindoError("conflict", 409, message, details=details)


def provider_unavailable(message: str, retryable: bool = True) -> KindoError:
    return KindoError("provider_unavailable", 503, message, retryable=retryable)

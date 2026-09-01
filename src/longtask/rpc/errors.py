"""错误码全集（DESIGN §11.7）。

统一错误对象：{code, message, retryable, details}。
新增错误码只能追加，不得改既有语义；客户端必须把未知错误码
按 INTERNAL 处理并展示原文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """线协议错误码（DESIGN §11.7 四族）。"""

    # 校验族
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNKNOWN_CONTRACT = "UNKNOWN_CONTRACT"
    UNKNOWN_ATTEMPT = "UNKNOWN_ATTEMPT"
    UNKNOWN_EXECUTOR = "UNKNOWN_EXECUTOR"

    # 并发与租约族
    REVISION_CONFLICT = "REVISION_CONFLICT"
    LEASE_FENCED = "LEASE_FENCED"
    LEASE_HELD = "LEASE_HELD"
    PARTITION_CONFLICT = "PARTITION_CONFLICT"

    # 能力与约束族
    CONSTRAINT_UNTRANSLATABLE = "CONSTRAINT_UNTRANSLATABLE"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    CONTEXT_CAPACITY_REFUSED = "CONTEXT_CAPACITY_REFUSED"
    CONTEXT_STALE = "CONTEXT_STALE"

    # 资源与状态族
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    STATE_FORBIDDEN = "STATE_FORBIDDEN"
    STORE_TAMPERED = "STORE_TAMPERED"
    IDEMPOTENCY_REPLAY_MISMATCH = "IDEMPOTENCY_REPLAY_MISMATCH"
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"


# 服务端如实填写的 retryable 默认表（DESIGN §11.7）。
RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.VALIDATION_FAILED: False,
    ErrorCode.UNKNOWN_CONTRACT: False,
    ErrorCode.UNKNOWN_ATTEMPT: False,
    ErrorCode.UNKNOWN_EXECUTOR: False,
    ErrorCode.REVISION_CONFLICT: True,  # 重读后重试
    ErrorCode.LEASE_FENCED: False,
    ErrorCode.LEASE_HELD: True,
    ErrorCode.PARTITION_CONFLICT: False,
    ErrorCode.CONSTRAINT_UNTRANSLATABLE: False,
    ErrorCode.CAPABILITY_MISSING: False,
    ErrorCode.CONTEXT_CAPACITY_REFUSED: False,
    ErrorCode.CONTEXT_STALE: True,
    ErrorCode.BUDGET_EXHAUSTED: False,
    ErrorCode.STATE_FORBIDDEN: False,
    ErrorCode.STORE_TAMPERED: False,
    ErrorCode.IDEMPOTENCY_REPLAY_MISMATCH: False,
    ErrorCode.AUTH_FAILED: False,
    ErrorCode.AUTH_REQUIRED: False,
    ErrorCode.RATE_LIMITED: True,
    ErrorCode.INTERNAL: True,
}


@dataclass(frozen=True, slots=True)
class RpcError(Exception):
    """结构化错误（DESIGN §11.2 统一错误对象）。

    异常与线协议错误同体：服务端 handler 抛出，传输层序列化为
    {"ok": false, "error": {...}}。
    """

    code: ErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataclass 生成的 __init__ 不走 Exception 初始化；
        # 显式补上，保证 str(err) 与日志中的异常信息不丢 message。
        Exception.__init__(self, self.message)

    @property
    def retryable(self) -> bool:
        return RETRYABLE[self.code]

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }

    @staticmethod
    def from_payload(payload: dict[str, Any]) -> RpcError:
        """客户端侧解析；未知错误码按 INTERNAL 处理（DESIGN §11.7）。"""
        error = payload.get("error", {})
        raw_code = str(error.get("code", ""))
        try:
            code = ErrorCode(raw_code)
        except ValueError:
            code = ErrorCode.INTERNAL
        return RpcError(
            code=code,
            message=str(error.get("message", raw_code or "unknown error")),
            details=dict(error.get("details", {})),
        )

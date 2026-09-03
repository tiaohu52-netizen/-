"""executor/* handler：执行器注册表控制面。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lhgp.adapters.registry import ExecutorRegistry, RegistryEntry
from lhgp.rpc.errors import ErrorCode, RpcError

if TYPE_CHECKING:
    from lhgp.rpc.server import RequestEnvelope


def handle_executor_list(
    envelope: RequestEnvelope,
    *,
    registry: ExecutorRegistry | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """查询执行器注册表列表。"""
    reg = registry or ExecutorRegistry()
    enabled_only = bool(envelope.params.get("enabled_only", False))
    entries = reg.list_entries(enabled_only=enabled_only)
    return {
        "ok": True,
        "result": {"executors": [entry.to_dict() for entry in entries], "total": len(entries)},
    }


def _require_executor_id(params: dict[str, Any]) -> str:
    executor_id = str(params.get("executor_id", "")).strip()
    if not executor_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="executor_id is required")
    return executor_id


def _lookup(reg: ExecutorRegistry, executor_id: str) -> RegistryEntry:
    entry = reg.get(executor_id)
    if entry is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_EXECUTOR,
            message=f"executor '{executor_id}' not found",
        )
    return entry


def handle_executor_enable(
    envelope: RequestEnvelope,
    *,
    registry: ExecutorRegistry | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """开启指定执行器。"""
    reg = registry or ExecutorRegistry()
    executor_id = _require_executor_id(envelope.params)
    _lookup(reg, executor_id)
    reg.set_enabled(executor_id, True)
    updated = reg.get(executor_id)
    return {
        "ok": True,
        "result": {"executor": updated.to_dict() if updated else None, "enabled": True},
    }


def handle_executor_disable(
    envelope: RequestEnvelope,
    *,
    registry: ExecutorRegistry | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """关闭指定执行器。"""
    reg = registry or ExecutorRegistry()
    executor_id = _require_executor_id(envelope.params)
    _lookup(reg, executor_id)
    reg.set_enabled(executor_id, False)
    updated = reg.get(executor_id)
    return {
        "ok": True,
        "result": {"executor": updated.to_dict() if updated else None, "enabled": False},
    }


def handle_executor_health(
    envelope: RequestEnvelope,
    *,
    registry: ExecutorRegistry | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """检查指定执行器健康与配置状态。"""
    reg = registry or ExecutorRegistry()
    executor_id = _require_executor_id(envelope.params)
    entry = _lookup(reg, executor_id)
    return {
        "ok": True,
        "result": {
            "executor_id": entry.id,
            "healthy": True,
            "enabled": entry.enabled,
            "kind": entry.kind,
            "cost_hint": entry.cost_hint.value,
            "capabilities": entry.to_dict()["capabilities"],
        },
    }


__all__ = [
    "handle_executor_disable",
    "handle_executor_enable",
    "handle_executor_health",
    "handle_executor_list",
]

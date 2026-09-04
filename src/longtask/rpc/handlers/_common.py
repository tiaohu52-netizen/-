"""JSON-RPC 控制面 handler 共享小工具（DESIGN §11.2、§11.3、§11.7）。

集中以下重复模式，避免每个 handler 各写一份：

- 服务端可信 actor 派生（C4 修复）：actor 由 envelope.client_id 映射，
  params["actor"] 仅作审计标签，不再覆盖派生值（不变式 #2）。
- 合同 ID 入参校验：空白抛 VALIDATION_FAILED。
- request_id 幂等重放快速返回：找到已有事件则直接返回当前视图，
  不重复执行副作用（DESIGN §11.3）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lhgp.contracts.budget import DEFAULT_VERIFICATION_RESERVED
from longtask.acceptance.checks import parse_check
from longtask.contracts.attention import from_dict as attention_from_dict
from longtask.contracts.authority import from_dict as authority_from_dict
from longtask.contracts.continuity import from_dict as continuity_from_dict
from longtask.contracts.schema import (
    Acceptance,
    Budget,
    ContractDraft,
)
from longtask.persistence.store import (
    get_contract,
    get_events_by_request_id,
)
from longtask.rpc.errors import ErrorCode, RpcError

if TYPE_CHECKING:
    from longtask.rpc.server import RequestEnvelope


# C4 修复（P1）：actor 服务端派生，不再从 params.actor 取（不变式 #2）。
# envelope.client_id 是稳定受信源（longtask-cli=用户，mcp=模型，executor=执行者）。
_TRUSTED_CLIENT_ACTORS: dict[str, str] = {
    "longtask-cli": "user",
    "cli": "user",  # 本地开发/测试客户端别名
    "cli-test": "user",  # 测试夹具使用的固定客户端 ID
    "mcp": "model",
    "executor": "executor",
    "verifier": "verifier",
    "daemon": "daemon",
    "system": "system",
}


def resolve_actor(envelope: RequestEnvelope, params: dict[str, Any]) -> str:
    """从 envelope 派生服务端可信 actor（C4 修复）。

    params["actor"] 仅作审计标签追加（不再覆盖派生值），避免模型端塞
    actor=user 冒充用户。
    """
    actor = _TRUSTED_CLIENT_ACTORS.get(envelope.client_id)
    if actor is None:
        raise RpcError(
            code=ErrorCode.AUTH_FAILED,
            message=f"unknown client_id: {envelope.client_id}",
        )
    return actor


def parse_contract_draft(params: dict[str, Any]) -> ContractDraft:
    """从请求入参解析并校验 ContractDraft（DESIGN §4、§11.6）。"""
    draft_data: dict[str, Any] = params.get("draft", params)
    try:
        title = str(draft_data["title"])
        objective = str(draft_data["objective"])
        raw_deadline = draft_data["deadline_at"]
        if hasattr(raw_deadline, "tzinfo"):  # datetime-like
            deadline_at = raw_deadline
        else:
            deadline_at = datetime.fromisoformat(str(raw_deadline))

        hard_constraints = dict(draft_data["hard_constraints"])
        acc_raw = draft_data["acceptance"]
        acceptance = Acceptance(
            standard=str(acc_raw["standard"]),
            checks=tuple(parse_check(c) for c in acc_raw["checks"]),
            verifier=str(acc_raw.get("verifier", "cross_check")),
        )

        workload_estimate = draft_data.get("workload_estimate")
        if workload_estimate is not None and isinstance(workload_estimate, dict):
            workload_initial_hours = float(workload_estimate["initial_hours"])
        elif "workload_initial_hours" in draft_data:
            workload_initial_hours = float(draft_data["workload_initial_hours"])
        else:
            raise KeyError("workload_estimate.initial_hours")

        budget_raw = draft_data["budget"]
        budget = Budget(
            max_dispatches=int(budget_raw["max_dispatches"]),
            max_escalations=int(budget_raw["max_escalations"]),
            max_concurrent_attempts=int(budget_raw["max_concurrent_attempts"]),
            max_attempt_minutes=int(budget_raw["max_attempt_minutes"]),
            max_output_bytes=int(budget_raw["max_output_bytes"]),
            verification_attempts_reserved=int(
                budget_raw.get("verification_attempts_reserved", DEFAULT_VERIFICATION_RESERVED)
            ),
        )

        soft_guidance = dict(draft_data.get("soft_guidance", {}))
        context = dict(draft_data.get("context", {}))
        execution = dict(draft_data.get("execution", {}))
        client_meta = dict(draft_data.get("client_meta", {}))
        authority = authority_from_dict(draft_data.get("authority"))
        attention = attention_from_dict(draft_data.get("attention"))
        continuity = continuity_from_dict(draft_data.get("continuity"))
    except (KeyError, TypeError, ValueError) as exc:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"malformed contract draft parameter: {exc}",
        ) from exc

    draft = ContractDraft(
        title=title,
        objective=objective,
        deadline_at=deadline_at,
        hard_constraints=hard_constraints,
        acceptance=acceptance,
        workload_initial_hours=workload_initial_hours,
        budget=budget,
        soft_guidance=soft_guidance,
        context=context,
        execution=execution,
        client_meta=client_meta,
        authority=authority,
        attention=attention,
        continuity=continuity,
    )
    errors = draft.validate()
    if errors:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="; ".join(errors),
            details={"errors": errors},
        )
    return draft


def require_contract_id(params: dict[str, Any]) -> str:
    """入参 contract_id 必填且非空（trim 后）。"""
    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="contract_id is required")
    return contract_id


def idempotent_replay(
    conn: sqlite3.Connection,
    envelope: RequestEnvelope,
    contract_id: str,
) -> dict[str, Any] | None:
    """幂等重放快速返回（DESIGN §11.3）。

    若 envelope.request_id 已在 events 表里有事件，直接返回当前合同视图，
    不重复执行副作用。返回 None 表示这不是重放，调用方继续正常路径。
    """
    if not envelope.request_id:
        return None
    if not get_events_by_request_id(conn, envelope.request_id):
        return None
    existing = get_contract(conn, contract_id)
    if existing is None:
        return None
    return {"ok": True, "result": existing.to_dict()}


def _parse_iso(s: str) -> datetime:
    """从 ISO 字符串解析为带 tz 的 datetime（公开助手，方便测试）。"""
    return datetime.fromisoformat(s)


__all__ = [
    "_parse_iso",
    "idempotent_replay",
    "parse_contract_draft",
    "require_contract_id",
    "resolve_actor",
]

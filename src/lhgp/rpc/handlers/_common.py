"""Canonical shared RPC handler guards.

Authentication and identifier validation live here so canonical handlers do not
need to import the legacy package.  Contract parsing and replay lookup remain
temporarily delegated until their persistence dependencies are migrated.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lhgp.acceptance.checks import parse_check
from lhgp.contracts.attention import from_dict as attention_from_dict
from lhgp.contracts.authority import from_dict as authority_from_dict
from lhgp.contracts.budget import DEFAULT_VERIFICATION_RESERVED
from lhgp.contracts.continuity import from_dict as continuity_from_dict
from lhgp.contracts.schema import Acceptance, Budget, ContractDraft
from lhgp.persistence.store import get_contract, get_events_by_request_id
from lhgp.rpc.errors import ErrorCode, RpcError

if TYPE_CHECKING:
    from lhgp.rpc.server import RequestEnvelope

_TRUSTED_CLIENT_ACTORS: dict[str, str] = {
    "longtask-cli": "user",
    "cli": "user",
    "cli-test": "user",
    "mcp": "model",
    "executor": "executor",
    "verifier": "verifier",
    "daemon": "daemon",
    "daemon-wakeup": "daemon",
    "system": "system",
}


def _parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp for handler-side input normalization."""
    return datetime.fromisoformat(value)


def resolve_actor(envelope: RequestEnvelope, params: dict[str, Any]) -> str:
    """从受信 client_id 派生 actor，拒绝未知客户端。"""
    actor = _TRUSTED_CLIENT_ACTORS.get(envelope.client_id)
    if actor is None:
        raise RpcError(
            code=ErrorCode.AUTH_FAILED,
            message=f"unknown client_id: {envelope.client_id}",
        )
    return actor


def require_contract_id(params: dict[str, Any]) -> str:
    """校验 contract_id 必填且 trim 后非空。"""
    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="contract_id is required")
    return contract_id


def parse_contract_draft(params: dict[str, Any]) -> ContractDraft:
    """解析并验证合同草稿，统一 canonical contracts 组件。"""
    draft_data: dict[str, Any] = params.get("draft", params)
    try:
        title = str(draft_data["title"])
        objective = str(draft_data["objective"])
        raw_deadline = draft_data["deadline_at"]
        deadline_at = (
            raw_deadline
            if hasattr(raw_deadline, "tzinfo")
            else datetime.fromisoformat(str(raw_deadline))
        )
        acc_raw = draft_data["acceptance"]
        acceptance = Acceptance(
            standard=str(acc_raw["standard"]),
            checks=tuple(parse_check(item) for item in acc_raw["checks"]),
            verifier=str(acc_raw.get("verifier", "cross_check")),
        )
        workload_estimate = draft_data.get("workload_estimate")
        if isinstance(workload_estimate, dict):
            workload_initial_hours = float(workload_estimate["initial_hours"])
        else:
            workload_initial_hours = float(draft_data["workload_initial_hours"])
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
        draft = ContractDraft(
            title=title,
            objective=objective,
            deadline_at=deadline_at,
            hard_constraints=dict(draft_data["hard_constraints"]),
            acceptance=acceptance,
            workload_initial_hours=workload_initial_hours,
            budget=budget,
            soft_guidance=dict(draft_data.get("soft_guidance", {})),
            context=dict(draft_data.get("context", {})),
            execution=dict(draft_data.get("execution", {})),
            client_meta=dict(draft_data.get("client_meta", {})),
            authority=authority_from_dict(draft_data.get("authority")),
            attention=attention_from_dict(draft_data.get("attention")),
            continuity=continuity_from_dict(draft_data.get("continuity")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"malformed contract draft parameter: {exc}",
        ) from exc
    errors = draft.validate()
    if errors:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="; ".join(errors),
            details={"errors": errors},
        )
    return draft


def idempotent_replay(
    conn: sqlite3.Connection,
    envelope: RequestEnvelope,
    contract_id: str,
) -> dict[str, Any] | None:
    """检测 request_id 重放并返回已有合同快照，不重复执行副作用。"""
    if not envelope.request_id or not get_events_by_request_id(conn, envelope.request_id):
        return None
    existing = get_contract(conn, contract_id)
    return {"ok": True, "result": existing.to_dict()} if existing is not None else None


__all__ = [
    "_parse_iso",
    "idempotent_replay",
    "parse_contract_draft",
    "require_contract_id",
    "resolve_actor",
]

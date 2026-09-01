"""contract/* 方法 handler：合同生命周期控制面（DESIGN §4、§5、§11.2）。

9 个方法覆盖完整生命周期：
- contract/prepare    起草（drafted）
- contract/approve    批准（drafted → active）
- contract/get        查单份
- contract/list       分页查
- contract/patch      修订可改字段
- contract/pause      暂停（active → paused）
- contract/resume     恢复（paused / blocked → active）
- contract/cancel     用户主动终止
- contract/arbitrate  人工裁决（expired / blocked → complete/archived/active）
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from longtask.contracts.schema import (
    FROZEN_FIELDS,
    Acceptance,
    ContractState,
)
from longtask.contracts.state_machine import (
    is_terminal_state,
    is_valid_transition,
)
from longtask.persistence.events import EventType
from longtask.persistence.store import (
    IdempotencyMismatchError,
    RevisionConflictError,
    StoreError,
    StoreTamperedError,
    get_contract,
    list_contracts,
    patch_contract,
    save_contract,
    update_contract_state,
)
from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.handlers._common import (
    idempotent_replay,
    parse_contract_draft,
    require_contract_id,
    resolve_actor,
)

if TYPE_CHECKING:
    from longtask.rpc.server import RequestEnvelope


_CONTRACT_ID_RE = re.compile(r"^lt-[0-9]{8}-[0-9a-zA-Z_-]+$")


def handle_contract_prepare(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """起草/创建合同（DESIGN §4、§5、§11.2、§11.6）。"""
    params = envelope.params
    draft = parse_contract_draft(params)

    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        date_prefix = now.strftime("%Y%m%d")
        contract_id = f"lt-{date_prefix}-{now.strftime('%H%M%S%f')[:8]}"
    elif not _CONTRACT_ID_RE.match(contract_id):
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=(f"contract_id '{contract_id}' violates format {_CONTRACT_ID_RE.pattern!r}"),
        )

    actor = resolve_actor(envelope, params)
    try:
        view = save_contract(
            conn,
            draft=draft,
            contract_id=contract_id,
            now=now,
            request_id=envelope.request_id,
            actor=actor,
        )
    except StoreTamperedError as exc:
        raise RpcError(code=ErrorCode.STORE_TAMPERED, message=str(exc)) from exc
    except IdempotencyMismatchError as exc:
        raise RpcError(code=ErrorCode.IDEMPOTENCY_REPLAY_MISMATCH, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc

    return {"ok": True, "result": view.to_dict()}


def handle_contract_approve(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """用户批准合同：drafted → active（DESIGN §5、§11.2）。"""
    params = envelope.params
    contract_id = require_contract_id(params)

    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")

    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if (
        not is_valid_transition(current.state, ContractState.ACTIVE)
        or current.state != ContractState.DRAFTED
    ):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"cannot approve contract in state '{current.state.value}'; "
                "approve is only valid from 'drafted'"
            ),
        )

    actor = resolve_actor(envelope, params)
    try:
        updated = update_contract_state(
            conn,
            contract_id=contract_id,
            new_state=ContractState.ACTIVE,
            now=now,
            expected_revision=expected_revision,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


def handle_contract_get(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """查询单份合同权威视图（DESIGN §11.2、§11.6）。"""
    contract_id = require_contract_id(envelope.params)
    contract = get_contract(conn, contract_id)
    if contract is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    return {"ok": True, "result": contract.to_dict()}


def handle_contract_list(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """分页查询合同列表（DESIGN §11.2、§11.6）。"""
    params = envelope.params
    filter_state: ContractState | None = None
    raw_state = params.get("state")
    if raw_state is not None:
        try:
            filter_state = ContractState(str(raw_state))
        except ValueError as exc:
            raise RpcError(
                code=ErrorCode.VALIDATION_FAILED,
                message=f"unknown contract state '{raw_state}'",
            ) from exc

    after_contract_id: str | None = params.get("cursor", params.get("after_contract_id"))
    limit = _coerce_int(params.get("limit", 20), "limit")
    if limit is None or limit <= 0:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="limit must be positive")

    try:
        contracts = list_contracts(
            conn,
            state=filter_state,
            after_contract_id=after_contract_id,
            limit=limit,
        )
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc

    return {
        "ok": True,
        "result": {
            "contracts": [c.to_dict() for c in contracts],
            "next_cursor": contracts[-1].contract_id if contracts else after_contract_id,
            "has_more": len(contracts) == limit,
        },
    }


def handle_contract_patch(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """修订合同可修改字段（DESIGN §4、§11.2、§11.7）。

    仅允许修改 soft_guidance / acceptance / workload_initial_hours；
    冻结区字段禁止修改；终态合同禁止修订。
    """
    params = envelope.params
    contract_id = require_contract_id(params)

    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")
    if expected_revision is None:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="expected_revision is required for contract/patch",
        )

    patch_data: dict[str, Any] = params.get("patch", {})
    if not patch_data:
        patch_data = {
            k: v
            for k, v in params.items()
            if k not in ("contract_id", "expected_revision", "actor")
        }

    forbidden_frozen = set(patch_data.keys()) & FROZEN_FIELDS
    if forbidden_frozen:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"cannot modify frozen fields in patch: {sorted(forbidden_frozen)}",
            details={"frozen_fields": list(forbidden_frozen)},
        )

    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if is_terminal_state(current.state):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=f"cannot patch contract in terminal state '{current.state.value}'",
        )

    soft_guidance: dict[str, Any] | None = (
        dict(patch_data["soft_guidance"]) if "soft_guidance" in patch_data else None
    )
    acceptance: Acceptance | None = None
    if "acceptance" in patch_data:
        acc_raw = patch_data["acceptance"]
        acceptance = Acceptance(
            standard=str(acc_raw["standard"]),
            checks=tuple(str(c) for c in acc_raw["checks"]),
            verifier=str(acc_raw.get("verifier", "cross_check")),
        )
        acc_errors = acceptance.validate()
        if acc_errors:
            raise RpcError(
                code=ErrorCode.VALIDATION_FAILED,
                message="; ".join(acc_errors),
                details={"errors": acc_errors},
            )

    workload_initial_hours: float | None = None
    if "workload_estimate" in patch_data and isinstance(patch_data["workload_estimate"], dict):
        workload_initial_hours = float(patch_data["workload_estimate"]["initial_hours"])
    elif "workload_initial_hours" in patch_data:
        workload_initial_hours = float(patch_data["workload_initial_hours"])

    if workload_initial_hours is not None and workload_initial_hours <= 0:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="workload_initial_hours must be positive",
        )

    actor = resolve_actor(envelope, params)
    try:
        updated = patch_contract(
            conn,
            contract_id=contract_id,
            expected_revision=expected_revision,
            now=now,
            soft_guidance=soft_guidance,
            acceptance=acceptance,
            workload_initial_hours=workload_initial_hours,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


def handle_contract_pause(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """用户主动暂停：active → paused（DESIGN §5、§11.2）。"""
    params = envelope.params
    contract_id = require_contract_id(params)
    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")
    reason = params.get("reason")

    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if not is_valid_transition(current.state, ContractState.PAUSED):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"cannot pause contract in state '{current.state.value}'; "
                "pause is only valid from 'active'"
            ),
        )

    actor = resolve_actor(envelope, params)
    event_payload = {"reason": str(reason)} if reason else None
    try:
        updated = update_contract_state(
            conn,
            contract_id=contract_id,
            new_state=ContractState.PAUSED,
            now=now,
            expected_revision=expected_revision,
            event_type=EventType.CONTRACT_PAUSED,
            event_payload=event_payload,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


def handle_contract_resume(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """恢复执行：paused / blocked → active（DESIGN §5、§11.2）。"""
    params = envelope.params
    contract_id = require_contract_id(params)
    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")
    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if current.state not in (
        ContractState.PAUSED,
        ContractState.BLOCKED,
    ) or not is_valid_transition(current.state, ContractState.ACTIVE):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"cannot resume contract in state '{current.state.value}'; "
                "resume is only valid from 'paused' or 'blocked'"
            ),
        )

    actor = resolve_actor(envelope, params)
    try:
        updated = update_contract_state(
            conn,
            contract_id=contract_id,
            new_state=ContractState.ACTIVE,
            now=now,
            expected_revision=expected_revision,
            event_type=EventType.CONTRACT_RESUMED,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


def handle_contract_cancel(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """用户主动终止：任意非终态 → cancelled（DESIGN §5、§11.2）。"""
    params = envelope.params
    contract_id = require_contract_id(params)
    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")
    reason = params.get("reason")

    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if is_terminal_state(current.state) or not is_valid_transition(
        current.state, ContractState.CANCELLED
    ):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"cannot cancel contract in terminal state '{current.state.value}'; "
                "cancel is only allowed from non-terminal states"
            ),
        )

    actor = resolve_actor(envelope, params)
    event_payload = {"reason": str(reason)} if reason else None
    try:
        updated = update_contract_state(
            conn,
            contract_id=contract_id,
            new_state=ContractState.CANCELLED,
            now=now,
            expected_revision=expected_revision,
            event_type=EventType.CONTRACT_CANCELLED,
            event_payload=event_payload,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


_ARBITRATION_DECISIONS: dict[str, ContractState] = {
    "complete": ContractState.COMPLETE,
    "accepted": ContractState.COMPLETE,
    "archived": ContractState.ARCHIVED,
    "discard": ContractState.ARCHIVED,
    "active": ContractState.ACTIVE,
    "extend": ContractState.ACTIVE,
    "resume": ContractState.ACTIVE,
}


def handle_contract_arbitrate(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    **kwargs: Any,
) -> dict[str, Any]:
    """Deadline / blocked / expired 人工裁决（DESIGN §5、§11.2、§11.5 时序 C）。

    支持裁决目标：complete（采纳部分成果）、archived（作废）、active（延期续跑）。
    """
    params = envelope.params
    contract_id = require_contract_id(params)
    if (replay := idempotent_replay(conn, envelope, contract_id)) is not None:
        return replay

    decision_raw = str(params.get("decision", params.get("target_state", ""))).strip().lower()
    if decision_raw not in _ARBITRATION_DECISIONS:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"invalid arbitration decision '{decision_raw}'; "
                "must be one of: complete, archived, active "
                "(or alias: accepted, discard, extend, resume)"
            ),
        )
    target_state = _ARBITRATION_DECISIONS[decision_raw]
    expected_revision = _coerce_int(params.get("expected_revision"), "expected_revision")
    note = params.get("note", params.get("reason"))

    current = get_contract(conn, contract_id)
    if current is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )
    if current.state not in (ContractState.EXPIRED, ContractState.BLOCKED):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"cannot arbitrate contract in state '{current.state.value}'; "
                "arbitrate is only allowed for 'expired' or 'blocked' contracts"
            ),
        )
    if not is_valid_transition(current.state, target_state):
        raise RpcError(
            code=ErrorCode.STATE_FORBIDDEN,
            message=(
                f"invalid arbitration transition from '{current.state.value}' "
                f"to '{target_state.value}'"
            ),
        )

    actor = resolve_actor(envelope, params)
    event_payload: dict[str, Any] = {
        "decision": decision_raw,
        "target_state": target_state.value,
    }
    if note:
        event_payload["note"] = str(note)

    try:
        updated = update_contract_state(
            conn,
            contract_id=contract_id,
            new_state=target_state,
            now=now,
            expected_revision=expected_revision,
            event_type=EventType.CONTRACT_ARBITRATED,
            event_payload=event_payload,
            request_id=envelope.request_id,
            actor=actor,
        )
    except RevisionConflictError as exc:
        raise RpcError(code=ErrorCode.REVISION_CONFLICT, message=str(exc)) from exc
    except StoreError as exc:
        raise RpcError(code=ErrorCode.INTERNAL, message=str(exc)) from exc
    return {"ok": True, "result": updated.to_dict()}


def _coerce_int(raw: Any, name: str) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"{name} must be an integer: {exc}",
        ) from exc


__all__ = [
    "handle_contract_approve",
    "handle_contract_arbitrate",
    "handle_contract_cancel",
    "handle_contract_get",
    "handle_contract_list",
    "handle_contract_patch",
    "handle_contract_pause",
    "handle_contract_prepare",
    "handle_contract_resume",
]

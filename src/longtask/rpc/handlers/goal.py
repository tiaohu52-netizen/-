"""goal/* 方法 handler（DESIGN §10.4、§11.2、§6）。

goal/* 是合同/尝试之上的"对外承诺"语义：调用 goal/prepare 不直接落
副作用，而是先返回一份 admission offer，列示当前可用的执行器、被
拒的执行器与原因、forecast 与可声明的 guarantee。SPEC §10.4 要求
goal/prepare 返回 7 类信息：eligible/rejected executors、
acceptance_executable、forecast p50/p90/confidence、
verification_reserve_sufficient、safe_start_by、
uncontrolled_risks、declared_guarantees。

P2 范围：
- goal/prepare 走单一 validator（contracts.validation.validate_draft）
- 调用 admission/eligibility.evaluate 给每个候选做 7 条件判定
- 落库与 contract/prepare 同样 save_contract（不改 draft），不拒接；
  拒接留给 goal/admission_check 这个只读方法
- 返回 result = {"contract": <view>, "admission": <offer.to_dict()>}

P3 之后再做：uncontrolled_risks / declared_guarantees 从 continuity/
authority/attention 字段聚合（目前留空元组）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from longtask.admission.eligibility import (
    CandidateFacts,
)
from longtask.admission.eligibility import (
    evaluate as evaluate_eligibility,
)
from longtask.admission.offer import (
    ExecutorCandidateView,
    Offer,
)
from longtask.contracts.authority import binding_for_executor
from longtask.contracts.contract_draft import from_dict
from longtask.contracts.validation import validate_draft
from longtask.persistence.events import EventType
from longtask.persistence.store import (
    get_contract,
    get_events_by_request_id,
    get_goal,
    list_goals,
    save_contract,
)
from longtask.rpc.errors import ErrorCode, RpcError
from longtask.rpc.handlers._common import (
    _parse_iso,
    resolve_actor,
)

if TYPE_CHECKING:
    from longtask.rpc.server import RequestEnvelope


def _build_admission_offer(
    *,
    draft_dict: dict[str, Any],
    registry_view: list[dict[str, Any]] | None,
) -> Offer:
    """根据 draft 与（可选的）执行器注册表快照构造一份 admission offer。

    不直接执行 prepare；只把 draft 通过 validate_draft 走结构校验，
    然后逐候选跑 evaluate（）。本函数不知道 budget 当前已消耗数——
    P4 之前 `budget_available` 给定 True；P4 由 caller 注入。
    """
    draft = from_dict(draft_dict)
    eligible: list[ExecutorCandidateView] = []
    rejected: list[ExecutorCandidateView] = []
    for cand in registry_view or []:
        facts = CandidateFacts(
            executor_id=str(cand.get("executor_id") or ""),
            executor_enabled_globally=bool(cand.get("enabled", False)),
            executor_concurrency_available=bool(cand.get("concurrency_available", True)),
            capability_satisfied=bool(cand.get("capability_satisfied", True)),
            constraint_enforcement_proven=bool(cand.get("constraint_enforcement_proven", True)),
            budget_available=bool(cand.get("budget_available", True)),
            verifier_independent=bool(cand.get("verifier_independent", True)),
        )
        requested_role = str(cand.get("requested_role", "executor"))
        raw_models = cand.get("models") or [cand.get("requested_model", "*")]
        models = tuple(str(model) for model in raw_models if str(model).strip()) or ("*",)
        if "*" in models:
            binding = binding_for_executor(draft.authority, facts.executor_id)
            if binding is not None and binding.models != ("*",):
                models = binding.models

        verdict = None
        requested_model = models[0]
        for model in models:
            candidate_verdict = evaluate_eligibility(
                authority=draft.authority,
                facts=facts,
                requested_model=model,
                requested_role=requested_role,
            )
            if candidate_verdict.eligible:
                verdict = candidate_verdict
                requested_model = model
                break
            verdict = candidate_verdict
        if verdict is None:
            continue
        view = ExecutorCandidateView(
            executor_id=facts.executor_id,
            models=(requested_model,),
            reason=(
                "all 7 conditions satisfied"
                if verdict.eligible
                else f"failed: {','.join(verdict.failed)}"
            ),
        )
        if verdict is not None and verdict.eligible:
            eligible.append(view)
        else:
            rejected.append(view)

    return Offer(
        eligible_executors=tuple(eligible),
        rejected_executors=tuple(rejected),
        acceptance_executable=bool(draft.acceptance.checks),
        forecast_p50_minutes=None,  # P4 之前不计算
        forecast_p90_minutes=None,
        forecast_confidence=None,
        verification_reserve_sufficient=True,  # P5 之前默认 True
        safe_start_by=None,  # P4 由 forecast 推导
        uncontrolled_risks=(),  # P3 再聚合
        declared_guarantees=(),
    )


def handle_goal_prepare(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    registry: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """goal/prepare：先校验再落库，返回 contract + admission offer。

    与 contract/prepare 区别：goal/prepare 多返回 admission（7 类信息）
    不拒接；contract/prepare 走老路径，单返合同视图。
    """
    params = envelope.params
    draft_data = params.get("draft", params)

    # 单一 validator：CLI / MCP / dataclass 三条路径在此汇合
    errors = validate_draft(draft_data)
    if errors:
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED,
            message="; ".join(errors),
            details={"errors": errors},
        )

    # request_id 幂等：之前已落库则原样返回（附 offer）
    if envelope.request_id and get_events_by_request_id(conn, envelope.request_id):
        cid = str(params.get("contract_id", "")).strip()
        existing = get_contract(conn, cid) if cid else None
        if existing is None:
            # 兜底：找最近一次同 request_id 的 contract_id（事件表上反查）
            from longtask.persistence.store import get_events

            events = get_events(conn)
            for ev in events:
                if ev.request_id == envelope.request_id and ev.contract_id:
                    existing = get_contract(conn, ev.contract_id)
                    if existing is not None:
                        break
        if existing is None:
            raise RpcError(
                code=ErrorCode.UNKNOWN_CONTRACT,
                message="idempotent replay: original contract not found",
            )
        return {
            "ok": True,
            "result": {
                "contract": existing.to_dict(),
                "admission": _build_admission_offer(
                    draft_dict=existing.draft.to_dict(),
                    registry_view=None,
                ).to_dict(),
            },
        }

    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        date_prefix = now.strftime("%Y%m%d")
        contract_id = f"lt-{date_prefix}-{now.strftime('%H%M%S%f')[:8]}"

    draft = from_dict(draft_data)
    actor = resolve_actor(envelope, params)

    view = save_contract(
        conn,
        draft=draft,
        contract_id=contract_id,
        goal_id=(str(params["goal_id"]).strip() if params.get("goal_id") else None),
        now=now,
        request_id=envelope.request_id,
        actor=actor,
    )

    # admission offer：若调用方注入了 registry，提供候选视图
    registry_view = None
    if registry is not None and hasattr(registry, "snapshot_for_admission"):
        registry_view = registry.snapshot_for_admission(contract=draft)
    offer = _build_admission_offer(
        draft_dict=view.draft.to_dict(),
        registry_view=registry_view,
    )

    # 把 admission 概要也写进 contract/prepared 事件 payload（便于审计/回放）
    from longtask.persistence.store import append_event

    append_event(
        conn,
        contract_id=contract_id,
        goal_id=view.goal_id,
        event_type=EventType.CONTRACT_PREPARED,
        payload={
            "goal_prepare": True,
            "eligible_executors": [c.executor_id for c in offer.eligible_executors],
            "rejected_executors": [c.executor_id for c in offer.rejected_executors],
        },
        now=now,
        actor=actor,
    )

    return {
        "ok": True,
        "result": {
            "contract": view.to_dict(),
            "admission": offer.to_dict(),
        },
    }


def handle_goal_admission_check(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    now: datetime,
    registry: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """goal/admission_check：只读——对已存在合同重算 admission offer。

    用于模型侧在续接时再次确认"现在还能不能跑"。不修改合同。
    """
    params = envelope.params
    contract_id = str(params.get("contract_id", "")).strip()
    if not contract_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="contract_id is required")

    existing = get_contract(conn, contract_id)
    if existing is None:
        raise RpcError(
            code=ErrorCode.UNKNOWN_CONTRACT,
            message=f"contract {contract_id} not found",
        )

    registry_view = None
    if registry is not None and hasattr(registry, "snapshot_for_admission"):
        registry_view = registry.snapshot_for_admission(contract=existing.draft)

    offer = _build_admission_offer(
        draft_dict=existing.draft.to_dict(),
        registry_view=registry_view,
    )
    return {"ok": True, "result": {"contract_id": contract_id, "admission": offer.to_dict()}}


def handle_goal_get(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> dict[str, Any]:
    """Read a stable Goal identity and its contract history."""
    goal_id = str(envelope.params.get("goal_id", "")).strip()
    if not goal_id:
        raise RpcError(code=ErrorCode.VALIDATION_FAILED, message="goal_id is required")
    goal = get_goal(conn, goal_id)
    if goal is None:
        raise RpcError(code=ErrorCode.UNKNOWN_CONTRACT, message=f"goal {goal_id} not found")
    return {"ok": True, "result": {"goal": goal}}


def handle_goal_list(
    envelope: RequestEnvelope,
    *,
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> dict[str, Any]:
    """List stable Goals independently from individual contract revisions."""
    raw_limit = envelope.params.get("limit", 20)
    try:
        limit = max(1, min(1000, int(raw_limit)))
    except (TypeError, ValueError):
        raise RpcError(
            code=ErrorCode.VALIDATION_FAILED, message="limit must be an integer"
        ) from None
    return {"ok": True, "result": {"goals": list_goals(conn, limit=limit)}}


__all__ = [
    "_parse_iso",
    "handle_goal_admission_check",
    "handle_goal_get",
    "handle_goal_list",
    "handle_goal_prepare",
]

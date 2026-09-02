"""SQLite/WAL 权威状态存储与事务写入（DESIGN §3.1、§7、§11.3、§13.3、§14）。

P1 起 schema 升到 v2（DESIGN §13.3）：
- contracts 表加 goal_id / deadline_status / acceptance_status / next_decision_at 列
- events 表加 contract_revision / role / payload_schema_version 列
- 新建 contract_revisions（不可变修订表，替换就地 CAS UPDATE）
- 新建 attempts（attempt 实体表，§7 attempt 轴、C1 修复依据）
- 新建 decisions（决策实体表，§6 escalation 轴历史）
- 新建 idempotency（请求幂等表，§11.3）

提供合同状态变更、租约 CAS、幂等去重与 fencing 写回的单事务原子写入实现。
所有时间均通过参数显式注入，不依赖系统墙钟。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from longtask.contracts.attention import from_dict as attention_from_dict
from longtask.contracts.attention import to_dict as attention_to_dict
from longtask.contracts.authority import from_dict as authority_from_dict
from longtask.contracts.authority import to_dict as authority_to_dict
from longtask.contracts.continuity import from_dict as continuity_from_dict
from longtask.contracts.continuity import to_dict as continuity_to_dict
from longtask.contracts.schema import (
    Acceptance,
    AcceptanceStatus,
    BlockReason,
    Budget,
    ContractDraft,
    ContractState,
    ContractView,
    DeadlineStatus,
)
from longtask.persistence.errors import (
    IdempotencyMismatchError,
    LeaseCASError,
    LeaseFencedError,
    RevisionConflictError,
    StoreError,
    StoreTamperedError,
)
from longtask.persistence.events import EventType
from longtask.persistence.events_query import (
    append_event,
    get_events,
    get_events_by_request_id,
)
from longtask.persistence.leases import (
    acquire_lease,
    get_lease,
    reclaim_lease,
    release_lease,
    renew_lease,
)
from longtask.persistence.notifications import enqueue_notification
from longtask.persistence.schema import (
    STORE_SCHEMA_VERSION,
    connect,
    ensure_schema,
    transaction,
)
from longtask.persistence.types import (
    EventInput,
    StoreConfig,
    StoredEvent,
    StoredLease,
    WriteBackResult,
)

# STORE_SCHEMA_VERSION re-export via schema.py；本地常量移除以避免双源。

# 显式 re-export，让 `from longtask.persistence.store import StoreConfig` 走 typecheck
# （拆分自 errors.py / types.py 后必须显式列出，否则 mypy 视为私有再导出失败）。
__all__ = [
    "STORE_SCHEMA_VERSION",
    # types
    "EventInput",
    # errors
    "IdempotencyMismatchError",
    "LeaseCASError",
    "LeaseFencedError",
    "RevisionConflictError",
    "StoreConfig",
    "StoreError",
    "StoreTamperedError",
    "StoredEvent",
    "StoredLease",
    "WriteBackResult",
    # functions（按字母序，函数体下方定义）
    "acquire_lease",
    "append_event",
    "connect",
    "ensure_schema",
    "get_contract",
    "get_events",
    "get_events_by_request_id",
    "get_lease",
    "list_contracts",
    "patch_contract",
    "reclaim_lease",
    "release_lease",
    "renew_lease",
    "save_contract",
    "transaction",
    "update_contract_state",
    "write_back",
]


def _row_to_contract_view(row: sqlite3.Row | tuple[Any, ...]) -> ContractView:
    """数据库记录转 ContractView（DESIGN §4、§11.6、§7 四轴）。

    P1：从 contracts 表读 goal_id / deadline_status / acceptance_status / next_decision_at，
    四个字段必须非空（迁移已兜底）。
    """
    (
        contract_id,
        goal_id,
        revision,
        state_str,
        deadline_status_str,
        acceptance_status_str,
        blocked_reason_str,
        title,
        objective,
        deadline_at_str,
        hard_constraints_json,
        acceptance_json,
        workload_initial_hours,
        budget_json,
        soft_guidance_json,
        context_json,
        execution_json,
        client_meta_json,
        authority_json,
        attention_json,
        continuity_json,
        created_at_str,
        updated_at_str,
        next_wakeup_at_str,
        next_decision_at_str,
        _schema_version,
    ) = row

    acceptance_dict = json.loads(acceptance_json)
    acceptance = Acceptance(
        standard=acceptance_dict["standard"],
        checks=tuple(acceptance_dict["checks"]),
        verifier=acceptance_dict.get("verifier", "cross_check"),
    )
    budget_dict = json.loads(budget_json)
    budget = Budget(
        max_dispatches=budget_dict["max_dispatches"],
        max_escalations=budget_dict["max_escalations"],
        max_concurrent_attempts=budget_dict["max_concurrent_attempts"],
        max_attempt_minutes=budget_dict["max_attempt_minutes"],
        max_output_bytes=budget_dict["max_output_bytes"],
        # P5 验证预算：老库存 JSON 无此字段 → 兜底 1（至少一次 reverify）
        verification_attempts_reserved=int(budget_dict.get("verification_attempts_reserved", 1)),
    )
    draft = ContractDraft(
        title=title,
        objective=objective,
        deadline_at=datetime.fromisoformat(deadline_at_str),
        hard_constraints=json.loads(hard_constraints_json),
        acceptance=acceptance,
        workload_initial_hours=float(workload_initial_hours),
        budget=budget,
        soft_guidance=json.loads(soft_guidance_json),
        context=json.loads(context_json),
        execution=json.loads(execution_json),
        client_meta=json.loads(client_meta_json),
        authority=authority_from_dict(json.loads(authority_json)),
        attention=attention_from_dict(json.loads(attention_json)),
        continuity=continuity_from_dict(json.loads(continuity_json)),
    )

    try:
        deadline_status = DeadlineStatus(deadline_status_str)
    except ValueError:
        # 兼容迁移前写入的 on_track 等旧值；未知值不应让整个守护进程
        # 因一行损坏数据崩溃，按最保守的 not_due 读取并由 doctor 报告。
        deadline_status = DeadlineStatus.NOT_DUE
    return ContractView(
        draft=draft,
        contract_id=contract_id,
        goal_id=goal_id or contract_id,
        revision=int(revision),
        state=ContractState(state_str),
        deadline_status=deadline_status,
        acceptance_status=AcceptanceStatus(acceptance_status_str),
        created_at=datetime.fromisoformat(created_at_str),
        updated_at=datetime.fromisoformat(updated_at_str),
        next_wakeup_at=(datetime.fromisoformat(next_wakeup_at_str) if next_wakeup_at_str else None),
        next_decision_at=(
            datetime.fromisoformat(next_decision_at_str) if next_decision_at_str else None
        ),
        blocked_reason=BlockReason(blocked_reason_str) if blocked_reason_str else None,
    )


def get_contract(conn: sqlite3.Connection, contract_id: str) -> ContractView | None:
    """获取合同当前权威视图（DESIGN §3.1、§11.6、§7 四轴）。"""
    row = conn.execute(
        """
        SELECT contract_id, goal_id, revision, state,
               deadline_status, acceptance_status, blocked_reason,
               title, objective, deadline_at, hard_constraints_json,
               acceptance_json, workload_initial_hours, budget_json,
               soft_guidance_json, context_json, execution_json,
               client_meta_json, authority_json, attention_json,
               continuity_json, created_at, updated_at, next_wakeup_at,
               next_decision_at, schema_version
        FROM contracts
        WHERE contract_id = ?
        """,
        (contract_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_contract_view(row)


def list_contracts(
    conn: sqlite3.Connection,
    *,
    state: ContractState | str | None = None,
    after_contract_id: str | None = None,
    limit: int = 20,
) -> list[ContractView]:
    """查询合同列表，支持按状态过滤与 cursor 分页（DESIGN §11.2、§11.6）。"""
    query = (
        "SELECT contract_id, goal_id, revision, state, "
        "deadline_status, acceptance_status, blocked_reason, "
        "title, objective, deadline_at, hard_constraints_json, "
        "acceptance_json, workload_initial_hours, budget_json, "
        "soft_guidance_json, context_json, execution_json, "
        "client_meta_json, authority_json, attention_json, "
        "continuity_json, created_at, updated_at, next_wakeup_at, "
        "next_decision_at, schema_version FROM contracts WHERE 1=1"
    )
    params: list[Any] = []
    if state is not None:
        state_str = state.value if isinstance(state, ContractState) else str(state)
        query += " AND state = ?"
        params.append(state_str)
    if after_contract_id is not None:
        query += " AND contract_id > ?"
        params.append(after_contract_id)
    query += " ORDER BY contract_id ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [_row_to_contract_view(r) for r in rows]


def save_contract(
    conn: sqlite3.Connection,
    draft: ContractDraft,
    contract_id: str,
    now: datetime,
    *,
    state: ContractState = ContractState.DRAFTED,
    revision: int = 1,
    next_wakeup_at: datetime | None = None,
    blocked_reason: BlockReason | None = None,
    deadline_status: DeadlineStatus = DeadlineStatus.NOT_DUE,  # SPEC §7.2
    acceptance_status: AcceptanceStatus = AcceptanceStatus.PENDING,  # SPEC §7.3
    next_decision_at: datetime | None = None,  # P4 预留
    request_id: str | None = None,
    actor: str = "user",
    schema_version: int = STORE_SCHEMA_VERSION,
) -> ContractView:
    """起草/创建合同并在同一事务追加 contract/prepared 事件（DESIGN §5、§11.3、§7 四轴）。

    - P1：同步写入 contract_revisions 第一份不可变快照（§13.3 修订不可变）。
    - 幂等：若 request_id 已存在，直接返回已存合同视图，不重复插入。
    """
    with transaction(conn):
        if request_id:
            existing_events = get_events_by_request_id(conn, request_id)
            if existing_events:
                existing_contract = get_contract(conn, contract_id)
                if existing_contract is not None:
                    return existing_contract

        conn.execute(
            """
            INSERT INTO contracts (
                contract_id, goal_id, revision, state,
                deadline_status, acceptance_status, blocked_reason,
                title, objective, deadline_at, hard_constraints_json,
                acceptance_json, workload_initial_hours, budget_json,
                soft_guidance_json, context_json, execution_json,
                client_meta_json, authority_json, attention_json,
                continuity_json, created_at, updated_at, next_wakeup_at,
                next_decision_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_id,
                contract_id,  # P1：goal_id 初值 = contract_id，§7 命名迁移
                revision,
                state.value,
                deadline_status.value,
                acceptance_status.value,
                blocked_reason.value if blocked_reason else None,
                draft.title,
                draft.objective,
                draft.deadline_at.isoformat(),
                json.dumps(draft.hard_constraints, ensure_ascii=False),
                json.dumps(
                    {
                        "standard": draft.acceptance.standard,
                        "checks": list(draft.acceptance.checks),
                        "verifier": draft.acceptance.verifier,
                    },
                    ensure_ascii=False,
                ),
                draft.workload_initial_hours,
                json.dumps(
                    {
                        "max_dispatches": draft.budget.max_dispatches,
                        "max_escalations": draft.budget.max_escalations,
                        "max_concurrent_attempts": draft.budget.max_concurrent_attempts,
                        "max_attempt_minutes": draft.budget.max_attempt_minutes,
                        "max_output_bytes": draft.budget.max_output_bytes,
                        "verification_attempts_reserved": (
                            draft.budget.verification_attempts_reserved
                        ),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(draft.soft_guidance, ensure_ascii=False),
                json.dumps(draft.context, ensure_ascii=False),
                json.dumps(draft.execution, ensure_ascii=False),
                json.dumps(draft.client_meta, ensure_ascii=False),
                json.dumps(authority_to_dict(draft.authority), ensure_ascii=False),
                json.dumps(attention_to_dict(draft.attention), ensure_ascii=False),
                json.dumps(continuity_to_dict(draft.continuity), ensure_ascii=False),
                now.isoformat(),
                now.isoformat(),
                next_wakeup_at.isoformat() if next_wakeup_at else None,
                next_decision_at.isoformat() if next_decision_at else None,
                schema_version,
            ),
        )

        # P1：写入首版不可变修订快照（DESIGN §13.3、§7 命名迁移）
        _write_revision_snapshot(
            conn,
            contract_id=contract_id,
            revision=revision,
            draft=draft,
            state=state,
            deadline_status=deadline_status,
            acceptance_status=acceptance_status,
            blocked_reason=blocked_reason,
            recorded_at=now,
            recorded_by=actor,
            change_reason="contract/prepared",
        )

        append_event(
            conn,
            contract_id=contract_id,
            event_type=EventType.CONTRACT_PREPARED,
            payload={
                "actor": actor,
                "title": draft.title,
                "objective": draft.objective,
                "state": state.value,
                "goal_id": contract_id,
                "deadline_status": deadline_status.value,
                "acceptance_status": acceptance_status.value,
                "revision": revision,
            },
            now=now,
            request_id=request_id,
            actor=actor,
            schema_version=schema_version,
            goal_id=contract_id,
            contract_revision=revision,
            role="user",
            payload_schema_version=schema_version,
        )

    view = get_contract(conn, contract_id)
    if view is None:
        raise StoreError(f"contract {contract_id} could not be retrieved after save")
    return view


def _write_revision_snapshot(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    revision: int,
    draft: ContractDraft,
    state: ContractState,
    deadline_status: DeadlineStatus,
    acceptance_status: AcceptanceStatus,
    blocked_reason: BlockReason | None,
    recorded_at: datetime,
    recorded_by: str,
    change_reason: str | None,
) -> None:
    """写入一份不可变 contract_revisions 行（DESIGN §13.3、§7 四轴）。

    主键 (contract_id, revision) 保证同一修订只能写入一次；新一次修订只能 revision+1。
    PRIMARY KEY 冲突时直接抛 IntegrityError——调用方必须先校准 revision。
    """
    conn.execute(
        """
        INSERT INTO contract_revisions (
            contract_id, revision, state, deadline_status, acceptance_status,
            blocked_reason, title, objective, deadline_at, hard_constraints_json,
            acceptance_json, workload_initial_hours, budget_json,
            soft_guidance_json, context_json, execution_json, client_meta_json,
            authority_json, attention_json, continuity_json,
            recorded_at, recorded_by, change_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            contract_id,
            revision,
            state.value,
            deadline_status.value,
            acceptance_status.value,
            blocked_reason.value if blocked_reason else None,
            draft.title,
            draft.objective,
            draft.deadline_at.isoformat(),
            json.dumps(draft.hard_constraints, ensure_ascii=False),
            json.dumps(
                {
                    "standard": draft.acceptance.standard,
                    "checks": list(draft.acceptance.checks),
                    "verifier": draft.acceptance.verifier,
                },
                ensure_ascii=False,
            ),
            draft.workload_initial_hours,
            json.dumps(
                {
                    "max_dispatches": draft.budget.max_dispatches,
                    "max_escalations": draft.budget.max_escalations,
                    "max_concurrent_attempts": draft.budget.max_concurrent_attempts,
                    "max_attempt_minutes": draft.budget.max_attempt_minutes,
                    "max_output_bytes": draft.budget.max_output_bytes,
                    "verification_attempts_reserved": (draft.budget.verification_attempts_reserved),
                },
                ensure_ascii=False,
            ),
            json.dumps(draft.soft_guidance, ensure_ascii=False),
            json.dumps(draft.context, ensure_ascii=False),
            json.dumps(draft.execution, ensure_ascii=False),
            json.dumps(draft.client_meta, ensure_ascii=False),
            json.dumps(authority_to_dict(draft.authority), ensure_ascii=False),
            json.dumps(attention_to_dict(draft.attention), ensure_ascii=False),
            json.dumps(continuity_to_dict(draft.continuity), ensure_ascii=False),
            recorded_at.isoformat(),
            recorded_by,
            change_reason,
        ),
    )


_STATE_TO_EVENT: dict[ContractState, EventType] = {
    ContractState.DRAFTED: EventType.CONTRACT_PREPARED,
    ContractState.ACTIVE: EventType.CONTRACT_APPROVED,
    ContractState.PAUSED: EventType.CONTRACT_PAUSED,
    ContractState.BLOCKED: EventType.CONTRACT_BLOCKED,
    ContractState.COMPLETE: EventType.CONTRACT_COMPLETED,
    ContractState.CANCELLED: EventType.CONTRACT_CANCELLED,
    ContractState.EXPIRED: EventType.CONTRACT_EXPIRED,
    ContractState.ARCHIVED: EventType.CONTRACT_ARBITRATED,
}


def update_contract_state(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    new_state: ContractState,
    now: datetime,
    expected_revision: int | None = None,
    blocked_reason: BlockReason | None = None,
    next_wakeup_at: datetime | None = None,
    deadline_status: DeadlineStatus | None = None,  # P1
    acceptance_status: AcceptanceStatus | None = None,  # P1
    next_decision_at: datetime | None = None,  # P4 预留
    event_type: EventType | str | None = None,
    event_payload: dict[str, Any] | None = None,
    request_id: str | None = None,
    actor: str = "daemon",
    schema_version: int = STORE_SCHEMA_VERSION,
) -> ContractView:
    """原子更新合同状态并追加对应事件（DESIGN §5、§11.3、§11.7、§7 四轴）。

    - revision CAS：若指定 expected_revision 且不符，抛 RevisionConflictError。
    - P1：同步写一份新的 contract_revisions 不可变快照，并落四轴字段。
    - 幂等：若 request_id 已存在，直接返回当前合同状态，不重复递增 revision。
    """
    with transaction(conn):
        if request_id:
            existing_events = get_events_by_request_id(conn, request_id)
            if existing_events:
                existing_contract = get_contract(conn, contract_id)
                if existing_contract is not None:
                    return existing_contract

        current = get_contract(conn, contract_id)
        if current is None:
            raise StoreError(f"contract {contract_id} not found")

        if expected_revision is not None and current.revision != expected_revision:
            raise RevisionConflictError(
                f"revision conflict on contract {contract_id}: "
                f"expected {expected_revision}, got {current.revision}"
            )

        new_revision = current.revision + 1
        new_deadline_status = (
            deadline_status if deadline_status is not None else current.deadline_status
        )
        new_acceptance_status = (
            acceptance_status if acceptance_status is not None else current.acceptance_status
        )

        # next_wakeup_at/next_decision_at 未显式给出时保留现值：
        # 两者是调度簿记（P4 决策点由 set_next_decision_at 轻量维护），
        # 状态迁移不得顺手清空它们。
        new_next_wakeup = next_wakeup_at if next_wakeup_at is not None else current.next_wakeup_at
        new_next_decision = (
            next_decision_at if next_decision_at is not None else current.next_decision_at
        )
        conn.execute(
            """
            UPDATE contracts
            SET revision = ?,
                state = ?,
                deadline_status = ?,
                acceptance_status = ?,
                blocked_reason = ?,
                updated_at = ?,
                next_wakeup_at = ?,
                next_decision_at = ?
            WHERE contract_id = ?
            """,
            (
                new_revision,
                new_state.value,
                new_deadline_status.value,
                new_acceptance_status.value,
                blocked_reason.value if blocked_reason else None,
                now.isoformat(),
                new_next_wakeup.isoformat() if new_next_wakeup else None,
                new_next_decision.isoformat() if new_next_decision else None,
                contract_id,
            ),
        )

        # P1：写入新一份不可变修订快照（基于当前最新 draft 字段；后续 patch 会改 draft）
        _write_revision_snapshot(
            conn,
            contract_id=contract_id,
            revision=new_revision,
            draft=current.draft,
            state=new_state,
            deadline_status=new_deadline_status,
            acceptance_status=new_acceptance_status,
            blocked_reason=blocked_reason,
            recorded_at=now,
            recorded_by=actor,
            change_reason=(
                event_type.value if isinstance(event_type, EventType) else str(event_type)
            )
            if event_type
            else f"transition -> {new_state.value}",
        )

        chosen_event_type = event_type or _STATE_TO_EVENT.get(new_state, EventType.CONTRACT_PATCHED)
        payload = {
            "actor": actor,
            "previous_state": current.state.value,
            "new_state": new_state.value,
            "revision": new_revision,
            "deadline_status": new_deadline_status.value,
            "acceptance_status": new_acceptance_status.value,
            **(event_payload or {}),
        }
        if blocked_reason:
            payload["blocked_reason"] = blocked_reason.value

        event = append_event(
            conn,
            contract_id=contract_id,
            event_type=chosen_event_type,
            payload=payload,
            now=now,
            request_id=request_id,
            actor=actor,
            schema_version=schema_version,
            goal_id=current.goal_id,
            contract_revision=new_revision,
            role=actor,
            payload_schema_version=schema_version,
        )
        notify_kind = _notification_kind(chosen_event_type, new_state)
        if notify_kind in current.draft.attention.notify_on:
            enqueue_notification(
                conn,
                idempotency_key=f"{contract_id}:event:{event.event_id}",
                goal_id=current.goal_id,
                event_type=notify_kind,
                channel="local",
                payload={"contract_id": contract_id, "event_id": event.event_id, **payload},
                now=now,
            )

    updated = get_contract(conn, contract_id)
    if updated is None:
        raise StoreError(f"contract {contract_id} disappeared after update")
    return updated


def _notification_kind(event_type: EventType | str, state: ContractState) -> str | None:
    """把状态事件映射为 attention.notify_on 的稳定类别。"""
    raw = event_type.value if isinstance(event_type, EventType) else str(event_type)
    if state == ContractState.EXPIRED or raw == EventType.CONTRACT_EXPIRED.value:
        return "missed"
    if state == ContractState.COMPLETE or raw in {
        EventType.CONTRACT_COMPLETED.value,
        EventType.CONTRACT_SATISFIED.value,
    }:
        return "satisfied"
    if state == ContractState.BLOCKED or raw == EventType.ESCALATION_HANDED_TO_USER.value:
        return "need_user"
    return None


def patch_contract(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    expected_revision: int,
    now: datetime,
    soft_guidance: dict[str, Any] | None = None,
    acceptance: Acceptance | None = None,
    workload_initial_hours: float | None = None,
    request_id: str | None = None,
    actor: str = "user",
    schema_version: int = STORE_SCHEMA_VERSION,
) -> ContractView:
    """原子修订合同可修改字段并追加 contract/patched 事件（DESIGN §4、§11.2、§11.7、§13.3）。

    - 仅允许修订 soft_guidance / acceptance / workload_initial_hours；
    - revision CAS：expected_revision 不符抛 RevisionConflictError；
    - P1：写一份新 contract_revisions 不可变快照（state/deadline/acceptance 与当前一致）；
    - 幂等：若 request_id 已存在，直接返回当前合同状态。
    """
    with transaction(conn):
        if request_id:
            existing_events = get_events_by_request_id(conn, request_id)
            if existing_events:
                existing_contract = get_contract(conn, contract_id)
                if existing_contract is not None:
                    return existing_contract

        current = get_contract(conn, contract_id)
        if current is None:
            raise StoreError(f"contract {contract_id} not found")

        if current.revision != expected_revision:
            raise RevisionConflictError(
                f"revision conflict on contract {contract_id}: "
                f"expected {expected_revision}, got {current.revision}"
            )

        new_revision = current.revision + 1
        new_soft_guidance = (
            soft_guidance if soft_guidance is not None else current.draft.soft_guidance
        )
        new_acceptance = acceptance if acceptance is not None else current.draft.acceptance
        new_workload = (
            workload_initial_hours
            if workload_initial_hours is not None
            else current.draft.workload_initial_hours
        )

        # 构造一份 patch 后的 draft 视图用于写新快照
        patched_draft = ContractDraft(
            title=current.draft.title,
            objective=current.draft.objective,
            deadline_at=current.draft.deadline_at,
            hard_constraints=current.draft.hard_constraints,
            acceptance=new_acceptance,
            workload_initial_hours=new_workload,
            budget=current.draft.budget,
            soft_guidance=new_soft_guidance,
            context=current.draft.context,
            execution=current.draft.execution,
            client_meta=current.draft.client_meta,
            authority=current.draft.authority,
            attention=current.draft.attention,
            continuity=current.draft.continuity,
        )

        conn.execute(
            """
            UPDATE contracts
            SET revision = ?,
                soft_guidance_json = ?,
                acceptance_json = ?,
                workload_initial_hours = ?,
                updated_at = ?
            WHERE contract_id = ?
            """,
            (
                new_revision,
                json.dumps(new_soft_guidance, ensure_ascii=False),
                json.dumps(
                    {
                        "standard": new_acceptance.standard,
                        "checks": list(new_acceptance.checks),
                        "verifier": new_acceptance.verifier,
                    },
                    ensure_ascii=False,
                ),
                new_workload,
                now.isoformat(),
                contract_id,
            ),
        )

        # P1：写新一份不可变快照（state/deadline/acceptance 与当前一致，draft 字段用 patched）
        _write_revision_snapshot(
            conn,
            contract_id=contract_id,
            revision=new_revision,
            draft=patched_draft,
            state=current.state,
            deadline_status=current.deadline_status,
            acceptance_status=current.acceptance_status,
            blocked_reason=current.blocked_reason,
            recorded_at=now,
            recorded_by=actor,
            change_reason="contract/patched",
        )

        patch_details: dict[str, Any] = {"actor": actor, "revision": new_revision}
        if soft_guidance is not None:
            patch_details["soft_guidance"] = soft_guidance
        if acceptance is not None:
            patch_details["acceptance"] = {
                "standard": acceptance.standard,
                "checks": list(acceptance.checks),
                "verifier": acceptance.verifier,
            }
        if workload_initial_hours is not None:
            patch_details["workload_initial_hours"] = workload_initial_hours

        append_event(
            conn,
            contract_id=contract_id,
            event_type=EventType.CONTRACT_PATCHED,
            payload=patch_details,
            now=now,
            request_id=request_id,
            actor=actor,
            schema_version=schema_version,
            goal_id=current.goal_id,
            contract_revision=new_revision,
            role=actor,
            payload_schema_version=schema_version,
        )

    updated = get_contract(conn, contract_id)
    if updated is None:
        raise StoreError(f"contract {contract_id} disappeared after patch")
    return updated


def write_back(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    attempt_id: str,
    write_generation: int,
    now: datetime,
    partition_id: str | None = None,
    events: Sequence[EventInput] = (),
    contract_state: ContractState | None = None,
    expected_revision: int | None = None,
    blocked_reason: BlockReason | None = None,
    next_wakeup_at: datetime | None = None,
    request_id: str | None = None,
    fence_checker: Callable[[Any, int, str], None] | None = None,
    actor: str = "model",
    schema_version: int = STORE_SCHEMA_VERSION,
    role: str | None = None,  # P1
    contract_revision: int | None = None,  # P1
    goal_id: str | None = None,  # P1
    model_id: str | None = None,
) -> WriteBackResult:
    """带 generation fencing 的执行结果写回（DESIGN §7、§11.3、§14.1）。

    - 校验 write_generation 与 attempt_id：不符抛 LeaseFencedError；
    - 支持可选注入 fence_checker 回调（如 promoter 校验逻辑），避免破坏分层依赖；
    - 单事务原子提交：合同状态变更 + 所有事件追加；
    - 幂等：若 request_id 已存在，返回原写回结果，不产生新事件。
    """
    with transaction(conn):
        if request_id:
            existing_events = get_events_by_request_id(conn, request_id)
            if existing_events:
                contract = get_contract(conn, contract_id)
                return WriteBackResult(
                    contract_id=contract_id,
                    attempt_id=attempt_id,
                    lease_generation=write_generation,
                    event_ids=tuple(e.event_id for e in existing_events),
                    revision=contract.revision if contract else None,
                )

        lease = get_lease(conn, contract_id, partition_id)
        if lease is None:
            raise LeaseFencedError(f"write fenced on contract {contract_id}: no active lease found")

        if fence_checker is not None:
            fence_checker(lease, write_generation, attempt_id)
        else:
            if write_generation != lease.generation:
                raise LeaseFencedError(
                    f"write generation {write_generation} fenced by lease generation "
                    f"{lease.generation} (contract {contract_id})"
                )
            if attempt_id != lease.holder_attempt_id:
                raise LeaseFencedError(
                    f"write attempt {attempt_id} is not lease holder "
                    f"{lease.holder_attempt_id} (contract {contract_id})"
                )

        # 将执行者自报的实际模型与写回一并持久化，确保重启后审计视图
        # 与终态事件中的 model_id 保持一致。仅接受非空值，避免普通进度
        # 写回意外清空调度阶段已选模型。
        if model_id and str(model_id).strip():
            conn.execute(
                "UPDATE attempts SET model_id = ?, updated_at = ? WHERE attempt_id = ?",
                (str(model_id).strip(), now.isoformat(), attempt_id),
            )

        new_revision: int | None = None
        if contract_state is not None:
            updated_view = update_contract_state(
                conn,
                contract_id=contract_id,
                new_state=contract_state,
                now=now,
                expected_revision=expected_revision,
                blocked_reason=blocked_reason,
                next_wakeup_at=next_wakeup_at,
                actor=actor,
                schema_version=schema_version,
            )
            new_revision = updated_view.revision

        appended_ids: list[int] = []
        for inp in events:
            evt = append_event(
                conn,
                contract_id=contract_id,
                event_type=inp.event_type,
                payload=inp.payload,
                now=now,
                attempt_id=inp.attempt_id or attempt_id,
                lease_generation=inp.lease_generation or write_generation,
                request_id=inp.request_id or request_id,
                actor=inp.actor or actor,
                schema_version=schema_version,
                goal_id=(inp.goal_id or goal_id or contract_id),
                contract_revision=(inp.contract_revision or contract_revision),
                role=(inp.role or role or actor),
                payload_schema_version=(inp.payload_schema_version or schema_version),
            )
            appended_ids.append(evt.event_id)

    return WriteBackResult(
        contract_id=contract_id,
        attempt_id=attempt_id,
        lease_generation=write_generation,
        event_ids=tuple(appended_ids),
        revision=new_revision,
    )

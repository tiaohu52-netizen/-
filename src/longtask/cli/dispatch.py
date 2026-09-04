"""逐候选执行器分发（DESIGN §8.3、§9、§10）。

prepare 探针先于租约 CAS（§10 时序：prepare → 租约 CAS → spawn）；
拒接记录 dispatch/refused 事件并换下一个（§9，绝不降级）。
依赖执行桥接层（cli/runner.py）构造 AttemptInput，故属 cli 层。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from longtask.adapters.base import ExecutorAdapter, PrepareRefusedError
from longtask.adapters.registry import RegistryEntry
from longtask.cli.runner import build_attempt_input
from longtask.contracts.schema import ContractView
from longtask.persistence.events import EventType
from longtask.persistence.projections import rebuild_projection
from longtask.persistence.store import acquire_lease, append_event, get_lease, reclaim_lease
from longtask.promoter.records import _record_attempt
from longtask.promoter.urgency import UrgencyTier


def _dispatch_attempt(
    *,
    root: Path,
    conn: sqlite3.Connection,
    contract: ContractView,
    candidates: list[RegistryEntry],
    now: datetime,
    tier: UrgencyTier,
    attempt_seq: str,
    adapter_factory: Callable[[RegistryEntry], ExecutorAdapter | None],
    emit: Callable[[str], None],
) -> dict[str, str] | None:
    """逐候选分发（DESIGN §8.3、§9）：prepare 兑现才占租约，拒接记录事件换下一个。

    prepare 探针在租约获取之前（DESIGN §10 时序：prepare → 租约 CAS → spawn）；
    成功返回 {"contract_id", "attempt_id", "executor_id"} 供执行桥接层拉起，
    全部候选耗尽（含拒接）返回 None，由调用方转 blocked(no-executor)。
    存在心跳已断的旧租约时走回收路径（lease/reclaimed，DESIGN §7）。
    """
    cid = contract.contract_id
    draft = contract.draft
    attempt_id = f"att-{now.strftime('%Y%m%d%H%M%S')}-{attempt_seq}"
    active_lease = get_lease(conn, cid)
    expected_gen = active_lease.generation if active_lease else 0
    probe_input = build_attempt_input(
        root, conn, contract, attempt_id, now, with_context=False
    )  # 探针不物化快照：租约未占，§10 时序

    def _record_refusal(executor_id: str, reason: str) -> None:
        append_event(
            conn,
            contract_id=cid,
            event_type=EventType.DISPATCH_REFUSED,
            payload={"executor_id": executor_id, "reason": reason},
            now=now,
            actor="daemon",
            goal_id=contract.goal_id,
            contract_revision=contract.revision,
            role="promoter",
        )
        rebuild_projection(root, cid, conn)
        emit(f"promoter/dispatch-refused:{cid}:{executor_id}")

    for entry in candidates:
        adapter = adapter_factory(entry)
        if adapter is None:
            _record_refusal(entry.id, f"注册表 kind={entry.kind!r} 没有可构造的适配器")
            continue
        try:
            adapter.prepare(probe_input)
        except PrepareRefusedError as exc:
            _record_refusal(entry.id, str(exc))
            continue
        selected_model = next((model for model in entry.models if model != "*"), "*")
        lease_payload = {
            "executor_id": entry.id,
            "model": selected_model,
            "urgency_tier": int(tier),
        }
        if active_lease is not None:
            # 心跳已断的旧租约：先回收再接管（lease/reclaimed，DESIGN §7）
            reclaim_lease(
                conn,
                contract_id=cid,
                expected_generation=expected_gen,
                heartbeat_at=now,
                timeout=timedelta(minutes=draft.budget.max_attempt_minutes),
                new_holder_attempt_id=attempt_id,
                actor="daemon",
                reason="heartbeat timeout before redispatch",
                payload=lease_payload,
                role="promoter",
                contract_revision=contract.revision,
            )
        else:
            acquire_lease(
                conn,
                contract_id=cid,
                holder_attempt_id=attempt_id,
                expected_generation=expected_gen,
                heartbeat_at=now,
                timeout=timedelta(minutes=draft.budget.max_attempt_minutes),
                actor="daemon",
                payload=lease_payload,
                role="promoter",
                contract_revision=contract.revision,
            )
        append_event(
            conn,
            contract_id=cid,
            attempt_id=attempt_id,
            event_type=EventType.ATTEMPT_STARTED,
            payload={
                "executor_id": entry.id,
                "model": selected_model,
                "tier": int(tier),
                "role": "executor",
                "contract_revision": contract.revision,
            },
            now=now,
            actor="daemon",
            goal_id=contract.goal_id,
            contract_revision=contract.revision,
            role="executor",
        )
        # P1：写入 attempts 实体行（DESIGN §7 attempt 轴、C1/C3 修复依据）
        _record_attempt(
            conn,
            goal_id=contract.goal_id,
            contract_id=contract.contract_id,
            attempt_id=attempt_id,
            contract_revision=contract.revision,
            role="executor",
            executor_id=entry.id,
            model_id=selected_model,
            state="admitted",
            admitted_at=now,
            updated_at=now,
        )
        rebuild_projection(root, cid, conn)
        emit(f"promoter/dispatched:{cid}:{entry.id}")
        return {
            "contract_id": cid,
            "attempt_id": attempt_id,
            "executor_id": entry.id,
            "model": selected_model,
        }
    return None

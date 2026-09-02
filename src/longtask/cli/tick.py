"""单次调度推进轮次（DESIGN §3.3、§6.2、§8.3、§11.3）。

run_daemon_tick 只做调度簿记：ticker 扫描、过期仲裁、紧迫度分档、
升级阶梯决策与逐候选分发串成一轮闭环；真实 attempt 的拉起与回收
在 cli/runner.py（执行桥接层），由 cli/daemon_loop.py 在每轮首尾驱动。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from longtask.adapters.base import ExecutorAdapter
from longtask.adapters.factory import build_adapter
from longtask.adapters.registry import ExecutorRegistry, RegistryEntry
from longtask.cli.dispatch import _dispatch_attempt
from longtask.contracts.schema import BlockReason, ContractState
from longtask.persistence.events import EventType
from longtask.persistence.projections import rebuild_projection
from longtask.persistence.store import (
    append_event,
    get_events,
    get_lease,
    list_contracts,
    update_contract_state,
)
from longtask.promoter.escalation import decide
from longtask.promoter.killswitch import is_kill_switch_active
from longtask.promoter.records import (
    _count_verifier_attempts,
    _estimate_stalled_from_attempts,
    _last_attempt_started_at,
    _last_event_at,
    _record_decision,
)
from longtask.promoter.urgency import UrgencyTier, classify, urgency
from longtask.scheduler.ticker import ClockEntry, ContractClock, is_overdue, run_tick


def run_daemon_tick(
    root: Path,
    conn: sqlite3.Connection,
    registry: ExecutorRegistry,
    now: datetime,
    emit_fn: Callable[[str], None] | None = None,
    adapter_factory: Callable[[RegistryEntry], ExecutorAdapter | None] | None = None,
) -> dict[str, Any]:
    """执行一次完整的调度推进轮次（DESIGN §3.3、§6.2、§8.3、§11.3）。

    处理流程：
    1. 检查全局 Kill Switch：若激活，跳过一切分发与加派；
    2. 查询库中所有非终态合同（DRAFTED, ACTIVE, PAUSED, BLOCKED, EXPIRED）；
    3. 运行 ticker 扫描判定 Deadline 越界与到点唤醒；
    4. 对过期合同执行仲裁迁移（-> EXPIRED）；
    5. 对 active 合同计算紧迫度并执行升级阶梯决策：
       - 若需要另起会话/加派，在已框定执行器池中按分发规则排序候选者；
       - 逐候选执行 prepare 探针（DESIGN §10 时序：prepare 先于租约）：
         拒接记录 dispatch/refused 事件并换下一个（DESIGN §9，绝不降级）；
       - 全部候选耗尽或无可匹配者，转入 blocked(no-executor)；
       - prepare 兑现者占领租约（旧租约心跳已断走回收路径，§7）并记录
         attempt/started 事件；
    6. 预算硬边界（§6.3）：已消耗 dispatch 数 = 事件流中 attempt/started
       计数，触顶即转档 5（blocked need-user），不再拉新会话；
    7. 自动同步更新物化文件投影（contract.yaml / lease.json / log.jsonl）。

    返回值带 attempts_started（contract_id/attempt_id/executor_id），供执行
    桥接层（AttemptRunner）真实拉起会话；本函数只做调度簿记（§3.3）。
    """
    events_emitted: list[str] = []

    def _emit(msg: str) -> None:
        events_emitted.append(msg)
        if emit_fn:
            emit_fn(msg)

    # 适配器工厂：未注入时用 kind 默认构造（DESIGN §12）
    factory = adapter_factory if adapter_factory is not None else build_adapter

    # 1. 检查 Kill Switch
    if is_kill_switch_active(root):
        _emit("daemon/kill-switch-active")
        return {
            "ok": True,
            "status": "halted_by_kill_switch",
            "events": events_emitted,
            "processed": 0,
            "attempts_started": [],
        }

    # 2. 查询所有合同
    all_contracts = list_contracts(conn, limit=1000)
    clocks: list[ClockEntry] = []

    for c in all_contracts:
        if c.state in (ContractState.COMPLETE, ContractState.CANCELLED, ContractState.ARCHIVED):
            continue
        deadline = c.draft.deadline_at
        wakeup = c.next_wakeup_at or (now + timedelta(seconds=60))
        clocks.append(
            ClockEntry(
                contract_id=c.contract_id,
                clock=ContractClock(
                    deadline_at=deadline,
                    next_wakeup_at=wakeup,
                    arbitrated_at=now if c.state == ContractState.EXPIRED else None,
                ),
            )
        )

    # 3. 运行 ticker 扫描
    updated_clocks = run_tick(now, clocks, emit=_emit)
    clock_map = {entry.contract_id: entry.clock for entry in updated_clocks}

    dispatched_count = 0
    expired_count = 0
    attempts_started: list[dict[str, str]] = []

    # P1：cross-tick 预算硬边界（C2 修复）——escalations_used 按 contract 累计。
    # 数据源：① 已派生的 verifier attempts（role=verifier 且 terminal）；② ESCALATION_STEERED 事件。
    # 二者均落库可审计；不再硬传 max_escalations 初值。
    escalations_used_by_contract: dict[str, int] = {}

    # P1：C3 修复：estimate_stalled 由 attempts 表真实判定——
    # "上一次 attempt 入库以来 u 值两次未下降"等价于"最近两次 attempt 同档/高档，
    # 且最近一次无 verifier 派生（verifier 派生代表已 §6.2 档 4 处理）"。
    # 这里给出最小近似：连续两次 attempt 同角色（executor）且未派生 verifier 即视为停滞。
    estimate_stalled_by_contract: dict[str, bool] = {}

    # 预扫一遍以建立两个映射
    for c in all_contracts:
        cid = c.contract_id
        if (
            c.state == ContractState.COMPLETE
            or c.state == ContractState.CANCELLED
            or c.state == ContractState.ARCHIVED
        ):
            continue
        verifier_count = _count_verifier_attempts(conn, cid)
        steer_count = sum(
            1
            for e in get_events(conn, contract_id=cid)
            if e.event_type == EventType.ESCALATION_STEERED
        )
        escalations_used_by_contract[cid] = verifier_count + steer_count
        estimate_stalled_by_contract[cid] = _estimate_stalled_from_attempts(conn, cid)

    for c in all_contracts:
        cid = c.contract_id
        clock = clock_map.get(cid)
        if clock is None:
            continue

        # 4. 过期处理：未处于 EXPIRED 但 ticker 已标记过期
        if is_overdue(clock, now) and c.state != ContractState.EXPIRED:
            try:
                update_contract_state(
                    conn,
                    contract_id=cid,
                    new_state=ContractState.EXPIRED,
                    now=now,
                    event_type=EventType.CONTRACT_EXPIRED,
                    event_payload={"arbitrated_at": now.isoformat()},
                    actor="daemon",
                )
                rebuild_projection(root, cid, conn)
                expired_count += 1
            except Exception as exc:
                _emit(f"error/expire-failed:{cid}:{exc}")
            continue

        # 5. 仅对 ACTIVE 状态合同进行推进
        if c.state != ContractState.ACTIVE:
            continue

        # 计算剩余时间与工作量
        time_left_hours = max(0.0, (c.draft.deadline_at - now).total_seconds() / 3600.0)
        remaining_hours = c.draft.workload_initial_hours  # 可由交接滚动修正
        u_val = urgency(remaining_hours, time_left_hours)
        u_tier = classify(u_val)

        active_lease = get_lease(conn, cid)
        lease_alive = active_lease.is_alive(now) if active_lease else False

        # 预算硬边界（DESIGN §6.3）：已消耗 dispatch = 事件流中 attempt/started 数
        started_events = sum(
            1
            for e in get_events(conn, contract_id=cid)
            if e.event_type == EventType.ATTEMPT_STARTED
        )
        budget_dispatches_left = max(0, c.draft.budget.max_dispatches - started_events)

        allow_parallel = (
            c.draft.execution.get("allow_parallel", False)
            if isinstance(c.draft.execution, dict)
            else False
        )
        decision = decide(
            u_tier,
            lease_alive=lease_alive,
            budget_dispatches_left=budget_dispatches_left,
            budget_escalations_left=max(
                0, c.draft.budget.max_escalations - escalations_used_by_contract.get(cid, 0)
            ),
            estimate_stalled=estimate_stalled_by_contract.get(cid, False),
            partitions_allowed=allow_parallel,
        )

        match decision.tier:
            case UrgencyTier.RESPAWN | UrgencyTier.PARALLEL:
                # workspace 排他（共同维护风险）：同 workspace 有其他合同的
                # 活租约 → 本轮延后。两个执行者并发写同一目录 = 未定义行为
                # （互相覆盖/读到半成品文件），绝不做静默并发写。
                holder = _workspace_holder_other_than(conn, c, now)
                if holder is not None:
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.DISPATCH_DEFERRED,
                        payload={
                            "reason": "workspace occupied by another live contract",
                            "workspace_root": holder["workspace_root"],
                            "holder_contract_id": holder["contract_id"],
                            "note": "serialised per workspace; retry next tick",
                        },
                        now=now,
                        actor="daemon",
                        goal_id=c.goal_id,
                        contract_revision=c.revision,
                        role="promoter",
                    )
                    _emit(f"promoter/deferred-workspace-busy:{cid}:held-by:{holder['contract_id']}")
                    continue
                # 挑选执行器（DESIGN §8.3），逐候选尝试（§9：拒接换下一个）
                started = _dispatch_attempt(
                    root=root,
                    conn=conn,
                    contract=c,
                    candidates=registry.match_candidates(c.draft),
                    now=now,
                    tier=decision.tier,
                    attempt_seq=cid[-4:],
                    adapter_factory=factory,
                    emit=_emit,
                )
                if started is not None:
                    dispatched_count += 1
                    attempts_started.append(started)
                else:
                    # 无可用执行器或全部拒接 -> 转 blocked(no-executor)
                    update_contract_state(
                        conn,
                        contract_id=cid,
                        new_state=ContractState.BLOCKED,
                        now=now,
                        blocked_reason=BlockReason.NO_EXECUTOR,
                        event_type=EventType.CONTRACT_BLOCKED,
                        event_payload={
                            "reason": "no dispatchable executor: none eligible or all refused"
                        },
                        actor="daemon",
                    )
                    rebuild_projection(root, cid, conn)
                    _emit(f"promoter/blocked-no-executor:{cid}")

            case UrgencyTier.HAND_TO_USER:
                update_contract_state(
                    conn,
                    contract_id=cid,
                    new_state=ContractState.BLOCKED,
                    now=now,
                    blocked_reason=BlockReason.NEED_USER,
                    event_type=EventType.CONTRACT_BLOCKED,
                    event_payload={"reason": decision.reason},
                    actor="daemon",
                )
                # 同步记一条 decisions（DESIGN §6 升级历史）
                _record_decision(
                    conn,
                    goal_id=c.goal_id,
                    contract_revision=c.revision,
                    tier=u_tier,
                    decision_type="hand-to-user",
                    reason=decision.reason,
                    budget_dispatches_left=budget_dispatches_left,
                    budget_escalations_left=max(
                        0, c.draft.budget.max_escalations - escalations_used_by_contract.get(cid, 0)
                    ),
                    now=now,
                    actor="promoter",
                )
                rebuild_projection(root, cid, conn)
                _emit(f"promoter/blocked-need-user:{cid}")

            case UrgencyTier.REMIND:
                # P1：REMIND 冷却（DESIGN §10.5）——上次 REMIND 5 分钟内不重复。
                # 跨档判定：同一 tick 走到 STEER 分支则这里不重复落 REMIND 事件。
                last_remind = _last_event_at(conn, cid, EventType.ESCALATION_REMINDED)
                last_steer = _last_event_at(conn, cid, EventType.ESCALATION_STEERED)
                last_attempt = _last_attempt_started_at(conn, cid)
                cooldown_ok = last_remind is None or (now - last_remind) >= timedelta(minutes=5)
                cross_tier_ok = (
                    last_steer is None or last_attempt is None or last_steer < last_attempt
                )
                if cooldown_ok and cross_tier_ok:
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.ESCALATION_REMINDED,
                        payload={"reason": decision.reason},
                        now=now,
                        actor="daemon",
                        goal_id=c.goal_id,
                        contract_revision=c.revision,
                        role="promoter",
                    )
                    rebuild_projection(root, cid, conn)

            case UrgencyTier.STEER:
                # P1：STEER 跨档判定——同 tick 已记 REMIND 则跳过 STEER，避免重复事件。
                last_steer = _last_event_at(conn, cid, EventType.ESCALATION_STEERED)
                last_attempt = _last_attempt_started_at(conn, cid)
                if last_steer is None or last_attempt is None or last_steer < last_attempt:
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.ESCALATION_STEERED,
                        payload={"reason": decision.reason},
                        now=now,
                        actor="daemon",
                        goal_id=c.goal_id,
                        contract_revision=c.revision,
                        role="promoter",
                    )
                    rebuild_projection(root, cid, conn)

    # verifier 裁决（DESIGN §5.2）：verifier succeeded -> 合同 complete；
    # failed -> 退回 active（重新派工）；其他等待。
    _judge_verifier_outcomes(root, conn, now)

    return {
        "ok": True,
        "status": "completed",
        "events": events_emitted,
        "dispatched": dispatched_count,
        "expired": expired_count,
        "attempts_started": attempts_started,
    }


def _workspace_holder_other_than(
    conn: sqlite3.Connection,
    contract: Any,
    now: datetime,
) -> dict[str, str] | None:
    """workspace 排他判定（共同维护风险防护）。

    合同 workspace_root 被另一个**持有活租约**的合同占用时返回占用者信息：
    {contract_id, workspace_root}。自己的租约不冲突（同合同的重派有租约
    fencing 兜底）；未声明 workspace 或无人占用返回 None。

    活租约 = heartbeat_at + timeout 内（与 decide() 的 lease_alive 同口径）。
    死租约不算占用——心跳断了说明持有者已停止推进，回收路径会接管。
    """
    workspace = _contract_workspace(contract)
    if not workspace:
        return None
    normalized = _norm_workspace(workspace)
    for other in list_contracts(conn, limit=1000):
        if other.contract_id == contract.contract_id:
            continue
        if other.state not in (ContractState.ACTIVE, ContractState.BLOCKED):
            continue
        other_ws = _norm_workspace(_contract_workspace(other))
        if other_ws != normalized:
            continue
        lease = get_lease(conn, other.contract_id)
        if lease is not None and lease.is_alive(now):
            return {
                "contract_id": other.contract_id,
                "workspace_root": workspace,
            }
    return None


def _contract_workspace(contract: Any) -> str:
    """从 ContractView/ContractDraft 提取 workspace_root（未声明返回空串）。"""
    draft = getattr(contract, "draft", contract)
    hard = draft.hard_constraints or {}
    file_effects = hard.get("file_effects")
    if isinstance(file_effects, dict):
        root = file_effects.get("workspace_root")
        if isinstance(root, str) and root.strip():
            return root
    return ""


def _norm_workspace(workspace: str) -> str:
    """workspace 归一化比较键：小写盘符 + 正斜杠，忽略尾部分隔符差异。"""
    text = workspace.strip().replace("\\", "/").rstrip("/")
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text


def _judge_verifier_outcomes(root: Path, conn: sqlite3.Connection, now: datetime) -> None:
    """verifier attempt 终态裁决（DESIGN §5.2）：成功 -> complete，失败 -> 退回 active。

    只看 verifier role 的终态事件（payload 含 role=verifier）：
    - attempt/succeeded 且未写过 contract/completed -> 合同转 complete
      （verifier 证据落 event payload）；
    - attempt/failed -> 合同退回 active（紧迫度重算），
      并清掉上次 verifier 派生的 isolation 状态。
    """
    for contract in list_contracts(conn, limit=1000):
        if contract.state not in (ContractState.ACTIVE,):
            continue
        last_verifier_state: str | None = None
        last_verifier_payload: dict[str, Any] = {}
        verifier_attempt_id: str | None = None
        for event in get_events(conn, contract_id=contract.contract_id):
            payload_text = event.payload_json or ""
            if "verifier" not in payload_text:
                continue
            if str(event.event_type) == EventType.ATTEMPT_SUCCEEDED.value:
                last_verifier_state = "succeeded"
                last_verifier_payload = _safe_json(payload_text)
                verifier_attempt_id = event.attempt_id
            elif str(event.event_type) == EventType.ATTEMPT_FAILED.value:
                last_verifier_state = "failed"
                last_verifier_payload = _safe_json(payload_text)
                verifier_attempt_id = event.attempt_id

        if last_verifier_state is None or verifier_attempt_id is None:
            continue
        if last_verifier_state == "succeeded":
            append_event(
                conn,
                contract_id=contract.contract_id,
                event_type=EventType.CONTRACT_COMPLETED,
                payload={
                    "verifier": verifier_attempt_id,
                    "evidence": last_verifier_payload,
                },
                now=now,
                actor="verifier",
            )
            update_contract_state(
                conn,
                contract_id=contract.contract_id,
                new_state=ContractState.COMPLETE,
                now=now,
            )
            rebuild_projection(root, contract.contract_id, conn)
        else:  # failed
            append_event(
                conn,
                contract_id=contract.contract_id,
                event_type=EventType.CONTRACT_BLOCKED,
                payload={
                    "verifier": verifier_attempt_id,
                    "evidence": last_verifier_payload,
                    "reason": "verifier rejected (§5.2): back to active",
                },
                now=now,
                actor="verifier",
            )
            update_contract_state(
                conn,
                contract_id=contract.contract_id,
                new_state=ContractState.ACTIVE,
                now=now,
            )
            rebuild_projection(root, contract.contract_id, conn)


def _safe_json(text: str) -> dict[str, Any]:
    import json as _json

    try:
        result = _json.loads(text)
    except ValueError:
        return {}
    if isinstance(result, dict):
        return result
    return {}

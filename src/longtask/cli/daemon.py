"""longtaskd 守护进程与调度驱动核心（DESIGN §3.3、§6.2、§8.3、§10、§15.2）。

本模块将 ticker 扫描、紧迫度分档、升级阶梯决策、执行器挑选、租约 CAS 与文件投影
串成完整的推进闭环，支持全局 Kill Switch 紧急熔断与确定性时间注入。
真实 attempt 的拉起与回收在 cli/runner.py（执行桥接层）。
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from longtask.adapters.base import ExecutorAdapter, PrepareRefusedError
from longtask.adapters.factory import build_adapter
from longtask.adapters.registry import ExecutorRegistry, RegistryEntry
from longtask.cli.runner import AttemptRunner, build_attempt_input
from longtask.contracts.schema import BlockReason, ContractState, ContractView
from longtask.persistence.events import EventType
from longtask.persistence.projections import rebuild_projection
from longtask.persistence.store import (
    StoreConfig,
    acquire_lease,
    append_event,
    connect,
    ensure_schema,
    get_events,
    get_lease,
    list_contracts,
    reclaim_lease,
    update_contract_state,
)
from longtask.promoter.escalation import decide
from longtask.promoter.urgency import UrgencyTier, classify, urgency
from longtask.scheduler.ticker import ClockEntry, ContractClock, is_overdue, run_tick
from longtask.scheduler.wakeup import (
    NullSchedulePort,
    PowerPort,
    RtcAlarm,
    SchedulePort,
    SleepGuard,
    WindowsPowerPort,
    guard_needed,
)

KILL_SWITCH_FILE = "KILL_SWITCH"
PID_FILE = "daemon.pid"
TOKEN_FILE = "daemon.token"  # noqa: S105
DAEMON_STOP_FILE = "daemon.stop"
DAEMON_LOG_FILE = "daemon.log"
REGISTRY_FILE = "registry.json"
DEFAULT_TICK_INTERVAL_SECONDS = 60.0
STOP_GRACE_SECONDS = 10.0


def is_kill_switch_active(root: Path) -> bool:
    """检查全局 Kill Switch 是否处于激活状态（DESIGN §15.2）。"""
    return (root / KILL_SWITCH_FILE).is_file()


def set_kill_switch(root: Path, active: bool) -> None:
    """激活或解除全局 Kill Switch（DESIGN §15.2）。"""
    path = root / KILL_SWITCH_FILE
    if active:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat()
        path.write_text(f"kill switch engaged at {ts}\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def get_daemon_status(root: Path) -> dict[str, Any]:
    """获取 daemon 进程与熔断开关状态。"""
    pid_path = root / PID_FILE
    token_path = root / TOKEN_FILE
    ks_active = is_kill_switch_active(root)

    pid: int | None = None
    running = False
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            running = _pid_alive(pid)
        except ValueError:
            pid = None

    token: str | None = None
    if token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()

    return {
        "running": running,
        "pid": pid,
        "token_available": token is not None,
        "kill_switch": ks_active,
    }


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
        lease_payload = {"executor_id": entry.id, "urgency_tier": int(tier)}
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
            attempt_id=attempt_id,
            contract_revision=contract.revision,
            role="executor",
            executor_id=entry.id,
            state="admitted",
            admitted_at=now,
            updated_at=now,
        )
        rebuild_projection(root, cid, conn)
        emit(f"promoter/dispatched:{cid}:{entry.id}")
        return {"contract_id": cid, "attempt_id": attempt_id, "executor_id": entry.id}
    return None


# ── P1 helpers ───────────────────────────────────────────────────────────────


def _record_attempt(
    conn: sqlite3.Connection,
    *,
    goal_id: str,
    attempt_id: str,
    contract_revision: int,
    role: str,
    executor_id: str | None,
    state: str,
    admitted_at: datetime,
    started_at: datetime | None = None,
    terminal_at: datetime | None = None,
    return_code: int | None = None,
    error_class: str | None = None,
    payload: dict[str, Any] | None = None,
    updated_at: datetime,
) -> None:
    """upsert 一行 attempts（DESIGN §7、P1）。

    主键 attempt_id：同一 attempt_id 已存在则 UPDATE；首次创建则 INSERT。
    """
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO attempts (
            attempt_id, goal_id, contract_revision, role,
            executor_id, state, lease_generation, partition_id,
            admitted_at, started_at, terminal_at, return_code, error_class,
            payload_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (attempt_id) DO UPDATE SET
            state = excluded.state,
            lease_generation = excluded.lease_generation,
            started_at = COALESCE(excluded.started_at, attempts.started_at),
            terminal_at = excluded.terminal_at,
            return_code = excluded.return_code,
            error_class = excluded.error_class,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            attempt_id,
            goal_id,
            contract_revision,
            role,
            executor_id,
            state,
            admitted_at.isoformat(),
            started_at.isoformat() if started_at else None,
            terminal_at.isoformat() if terminal_at else None,
            return_code,
            error_class,
            payload_json,
            updated_at.isoformat(),
        ),
    )


def _record_decision(
    conn: sqlite3.Connection,
    *,
    goal_id: str,
    contract_revision: int,
    tier: UrgencyTier | None,
    decision_type: str,
    reason: str,
    budget_dispatches_left: int,
    budget_escalations_left: int,
    now: datetime,
    actor: str,
) -> None:
    """追加 decisions 行（DESIGN §6 升级历史）。"""
    tier_str = None if tier is None else int(tier)
    conn.execute(
        """
        INSERT INTO decisions (
            goal_id, contract_revision, tier, decision_type,
            reason, budget_dispatches_left, budget_escalations_left,
            payload_json, recorded_at, actor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            goal_id,
            contract_revision,
            tier_str,
            decision_type,
            reason,
            budget_dispatches_left,
            budget_escalations_left,
            now.isoformat(),
            actor,
        ),
    )


def _count_verifier_attempts(conn: sqlite3.Connection, contract_id: str) -> int:
    """attempts 表里 role='verifier' 且已 terminal 的数量（DESIGN §6 escalation_used）。"""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM attempts
        WHERE goal_id = ? AND role = 'verifier'
          AND state IN ('succeeded', 'failed', 'cancelled', 'stale', 'orphaned')
        """,
        (contract_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _estimate_stalled_from_attempts(conn: sqlite3.Connection, contract_id: str) -> bool:
    """estimate_stalled 近似：最近两次 executor attempt 同档/高档且无 verifier 派生。

    严格定义见 DESIGN §6.2 档 4 触发：本模块给最小可观测近似
    ——同 tick 观察到连续两条 attempt/started 间隔 < budget.max_attempt_minutes
    且无 verifier 派生，即视为停滞。"""
    rows = conn.execute(
        """
        SELECT a.state, a.admitted_at, a.contract_revision, a.role
        FROM attempts a
        WHERE a.goal_id = ?
        ORDER BY a.admitted_at DESC
        LIMIT 4
        """,
        (contract_id,),
    ).fetchall()
    if len(rows) < 2:
        return False
    recent = [r for r in rows if r[3] == "executor"]
    if len(recent) < 2:
        return False
    last, prev = recent[0], recent[1]
    if last[0] != "running" and last[0] != "admitted":
        return False
    if prev[0] in ("succeeded", "cancelled"):
        return False
    # 两次 attempt 之间无 verifier 派生
    verifier_exists = conn.execute(
        "SELECT 1 FROM attempts WHERE goal_id = ? AND role = 'verifier' LIMIT 1",
        (contract_id,),
    ).fetchone()
    return verifier_exists is None


def _last_event_at(
    conn: sqlite3.Connection, contract_id: str, event_type: EventType
) -> datetime | None:
    """最近一次指定事件的发生时间（用于跨档判定与冷却）。"""
    row = conn.execute(
        """
        SELECT created_at FROM events
        WHERE contract_id = ? AND event_type = ?
        ORDER BY event_id DESC LIMIT 1
        """,
        (contract_id, event_type.value),
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row[0])


def _last_attempt_started_at(conn: sqlite3.Connection, contract_id: str) -> datetime | None:
    """最近一次 attempt/started 事件时间。"""
    return _last_event_at(conn, contract_id, EventType.ATTEMPT_STARTED)


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


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _windows_pid_running(pid: int) -> bool:
    """GetExitCodeProcess 语义的存活检查。

    os.kill(pid, 0) 在 Windows 只证明进程对象存在：已退出但父进程尚未收尸的
    子进程（如 subprocess._active 持有的句柄）会误报存活。退出码 !=
    STILL_ACTIVE 即已退出，与句柄是否收尸无关。
    """
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """进程存活检查（区分「真在跑」与「已退出未收尸」）。"""
    # 经变量间接判断：mypy 平台收窄会把另一分支标记为 unreachable
    is_windows = sys.platform == "win32"
    if is_windows:
        return _windows_pid_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 存在但无权发信号：进程活着
        return True
    except OSError:
        return False
    return True


def _read_log_tail(log_path: Path, limit: int = 2000) -> str | None:
    """读 daemon.log 尾部用于启动失败诊断；读不到如实返回 None。"""
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return None


def spawn_daemon(
    root: Path,
    *,
    interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """启动常驻 longtaskd 后台进程（DESIGN §3.3 常驻 ticker、§15.2 可启动）。

    分离子进程运行 run_daemon_loop，写真实 pid 与一次性 token；
    已在运行、启动后立即退出（附 daemon.log 尾部）均如实报告，不假装成功。
    """
    status = get_daemon_status(root)
    if status["running"] and status["pid"] is not None:
        return {"ok": False, "error": f"daemon already running (pid {status['pid']})"}

    # 清理上次残留的 pid/token（进程已死或文件损坏）
    (root / PID_FILE).unlink(missing_ok=True)
    (root / TOKEN_FILE).unlink(missing_ok=True)
    root.mkdir(parents=True, exist_ok=True)

    log_path = root / DAEMON_LOG_FILE
    log_fh = log_path.open("ab")
    cmd = [
        sys.executable,
        "-m",
        "longtask.cli.main",
        "--data-dir",
        str(root),
        "_daemon-run",
        "--interval",
        str(interval_seconds),
    ]
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": log_fh,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(  # noqa: S603 —— 固定解释器与模块路径，无外部输入拼接
            cmd, **popen_kwargs
        )
    finally:
        log_fh.close()

    # 短暂确认子进程没有立刻死掉（导入错误等）；失败如实带回日志尾部
    for _ in range(40):
        if proc.poll() is not None:
            return {
                "ok": False,
                "error": f"daemon process exited immediately (code {proc.returncode})",
                "log_tail": _read_log_tail(log_path),
            }
        time.sleep(0.05)

    token = secrets.token_hex(16)
    (root / PID_FILE).write_text(f"{proc.pid}\n", encoding="utf-8")
    (root / TOKEN_FILE).write_text(f"{token}\n", encoding="utf-8")
    return {"ok": True, "pid": proc.pid, "interval_seconds": interval_seconds}


def halt_daemon(root: Path, *, grace_seconds: float = STOP_GRACE_SECONDS) -> dict[str, Any]:
    """停止常驻 longtaskd（DESIGN §15.2 可停止）。

    优先优雅路径：写 daemon.stop，循环在下一轮退出；超过宽限期仍存活
    才升级 SIGTERM 强杀。无论哪种路径都清理 pid/token/stop 标记。
    """
    pid_path = root / PID_FILE
    if not pid_path.is_file():
        (root / TOKEN_FILE).unlink(missing_ok=True)
        return {"ok": True, "was_running": False, "forced": False}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        (root / TOKEN_FILE).unlink(missing_ok=True)
        return {"ok": True, "was_running": False, "forced": False, "stale_pid_file": True}

    was_running = _pid_alive(pid)
    forced = False
    if was_running:
        (root / DAEMON_STOP_FILE).write_text(
            f"stop requested at {datetime.now(UTC).isoformat()}\n", encoding="utf-8"
        )
        deadline = time.monotonic() + grace_seconds
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                forced = True
            except OSError as exc:
                # 强杀失败：保留现场（pid/stop 标记），调用方可重试，不假装已停
                return {
                    "ok": False,
                    "was_running": True,
                    "forced": False,
                    "error": f"grace expired and SIGTERM failed: {exc}",
                    "pid": pid,
                }
    pid_path.unlink(missing_ok=True)
    (root / TOKEN_FILE).unlink(missing_ok=True)
    (root / DAEMON_STOP_FILE).unlink(missing_ok=True)
    return {"ok": True, "was_running": was_running, "forced": forced, "pid": pid}


def run_daemon_loop(
    root: Path,
    *,
    interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
    max_cycles: int | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    emit_fn: Callable[[str], None] | None = None,
    power_port: PowerPort | None = None,
    schedule_port: SchedulePort | None = None,
) -> dict[str, Any]:
    """常驻调度主循环（DESIGN §3.3）：循环执行 run_daemon_tick + 执行桥接。

    - 每轮从 registry.json 重载执行器池（用户框定与开关即时生效）；
    - AttemptRunner 在每轮 tick 前观察/回收 attempt、tick 后真实拉起新 attempt
      （§3.3 ticker 只调度不执行，拉起属于推动层执行桥接）；
    - 分层唤醒（§6.4/ADR-0002）：每轮刷新 L1 计划任务注册、按需持有/释放
      L0 电源守卫；端口可注入（测试零副作用），缺省 L0 用 Windows 实现、
      L1 无通道则记 wakeup/degraded 降级，不静默；
    - daemon.stop 存在 → 优雅退出并清理标记（配合 halt_daemon，§15.2）；
    - now_fn/sleep_fn/max_cycles 可注入：测试确定性，无真实墙钟、无真实长睡。
    """
    clock = now_fn if now_fn is not None else (lambda: datetime.now(UTC))
    conn = connect(StoreConfig(db_path=root / "state.db"))
    ensure_schema(conn)
    runner = AttemptRunner(root, conn, ExecutorRegistry(), emit=emit_fn)
    guard = SleepGuard(power_port if power_port is not None else WindowsPowerPort())
    rtc = RtcAlarm(schedule_port if schedule_port is not None else NullSchedulePort())
    total_dispatched = 0
    total_expired = 0
    cycles = 0
    stopped = False
    try:
        while max_cycles is None or cycles < max_cycles:
            if (root / DAEMON_STOP_FILE).is_file():
                stopped = True
                break
            now_val = clock()
            registry = ExecutorRegistry.load_from_file(root / REGISTRY_FILE)
            runner.replace_registry(registry)
            # 先回收上一轮的收尾者并给存活者续心跳，再调度，最后拉起新 attempt
            runner.poll_attempts(now_val)
            # 消费 control/interrupt 请求（用户通过 RPC 打断执行中的 attempt）
            _consume_interrupt_requests(root, conn, runner, now_val)
            res = run_daemon_tick(root, conn, registry, now=now_val, emit_fn=emit_fn)
            total_dispatched += int(res.get("dispatched", 0))
            total_expired += int(res.get("expired", 0))
            for started in res.get("attempts_started", []):
                runner.start_attempt(
                    now_val,
                    contract_id=str(started["contract_id"]),
                    attempt_id=str(started["attempt_id"]),
                    executor_id=str(started["executor_id"]),
                )
            # 分层唤醒：L1 对齐 active 合同的唤醒注册；L0 按需持有/释放电源请求
            rtc.refresh(conn, now=now_val)
            needed, guard_cid = guard_needed(conn, now=now_val)
            if guard_cid is not None:
                guard.update(
                    conn,
                    now=now_val,
                    guard_needed=needed,
                    reason="active lease or urgency >= 1.0 (§6.4 L0)",
                    contract_id=guard_cid,
                )
            elif guard.held:
                # 释放事件挂空合同：全局事件由审计流可见，不属于任何单个合同
                guard.update(
                    conn,
                    now=now_val,
                    guard_needed=False,
                    reason="no active lease and urgency < 1.0 (§6.4 L0)",
                    contract_id="",
                )
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if interval_seconds > 0:
                sleep_fn(interval_seconds)
    finally:
        if stopped:
            (root / DAEMON_STOP_FILE).unlink(missing_ok=True)
        conn.close()
    return {
        "ok": True,
        "cycles": cycles,
        "stopped_by_stop_file": stopped,
        "dispatched": total_dispatched,
        "expired": total_expired,
        "spawned": runner.spawned_count,
        "finished": runner.finished_count,
    }


def _consume_interrupt_requests(
    root: Path,
    conn: sqlite3.Connection,
    runner: AttemptRunner,
    now: datetime,
) -> None:
    """消费 control/interrupt 请求（DESIGN §10 可干涉、§6.4 仲裁时刻语义）。

    RPC handler 只写盘上事件；daemon 每轮 tick 顶部扫描「attempt/cancelled
    via=control/interrupt」事件，调用 AttemptRunner.cancel_attempt 兑现：
    adapter.cancel + attempt/cancelled 保留 + 租约释放 + 停追。

    幂等：cancel_attempt 对非 running attempt 返回 False 且不重复记事件；
    重扫已消费的 interrupt 事件是 no-op（attempt 已从 runner 移除）。
    """
    import json as _json

    for contract in list_contracts(conn, limit=1000):
        for event in get_events(conn, contract_id=contract.contract_id):
            if event.event_type != EventType.ATTEMPT_CANCELLED:
                continue
            try:
                payload = _json.loads(event.payload_json or "{}")
            except ValueError:
                continue
            if payload.get("via") != "control/interrupt":
                continue
            if event.attempt_id is None:
                continue
            runner.cancel_attempt(
                now,
                contract_id=contract.contract_id,
                attempt_id=event.attempt_id,
                reason=str(payload.get("reason", "user interrupt")),
            )
            rebuild_projection(root, contract.contract_id, conn)


def _judge_verifier_outcomes(root: Path, conn: sqlite3.Connection, now: datetime) -> None:
    """verifier attempt 终态裁决（DESIGN §5.2）：成功 -> complete，失败 -> 退回 active。

    只看 verifier role 的终态事件（payload 含 role=verifier）：
    - attempt/succeeded 且未写过 contract/completed -> 合同转 complete
      （verifier 证据落 event payload）；
    - attempt/failed -> 合同退回 active（紧迫度重算），
      并清掉上次 verifier 派生的 isolation 状态。
    """
    from longtask.contracts.schema import BlockReason, ContractState, ContractView

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
        # 兜底占位：ContractView 是 type-only import
        _ = ContractView
        _ = BlockReason


def _safe_json(text: str) -> dict[str, Any]:
    import json as _json

    try:
        result = _json.loads(text)
    except ValueError:
        return {}
    if isinstance(result, dict):
        return result
    return {}

"""常驻调度主循环（DESIGN §3.3、§6.4、§10）。

循环执行 run_daemon_tick + 执行桥接：每轮 tick 前观察/回收 attempt、
tick 后真实拉起新 attempt；分层唤醒（L0 电源守卫 / L1 计划任务）
与 control/interrupt 消费在轮内兑现。
"""

from __future__ import annotations

import json as _json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.daemon_proc import (
    DAEMON_STOP_FILE,
    DEFAULT_TICK_INTERVAL_SECONDS,
    REGISTRY_FILE,
    rpc_socket_path,
)
from longtask.cli.runner import AttemptRunner
from longtask.cli.tick import run_daemon_tick
from longtask.contracts.schema import ContractState
from longtask.persistence.decisions import earliest_next_decision_at
from longtask.persistence.events import EventType
from longtask.persistence.notifications import drain_notifications
from longtask.persistence.projections import rebuild_projection
from longtask.persistence.store import (
    StoreConfig,
    append_event,
    connect,
    ensure_schema,
    get_events,
    list_contracts,
)
from longtask.promoter.reconcile import reconcile_attempts
from longtask.rpc.methods import Method
from longtask.rpc.server import parse_envelope, route
from longtask.rpc.transport import serve_unix_socket
from longtask.scheduler.wakeup import (
    NullSchedulePort,
    PowerPort,
    RtcAlarm,
    SchedulePort,
    SleepGuard,
    WindowsPowerPort,
    guard_needed,
)

# User control-plane mutations must preempt adaptive idle sleep.  A read-only
# RPC may wait for the next decision point; these methods cannot, because their
# effect is specifically to change what the next decision should be.
_WAKE_ON_RPC = frozenset(
    {
        Method.CONTRACT_APPROVE,
        Method.CONTRACT_PATCH,
        Method.CONTRACT_PAUSE,
        Method.CONTRACT_RESUME,
        Method.CONTRACT_CANCEL,
        Method.CONTRACT_ARBITRATE,
        Method.CONTRACT_REQUEST_VERIFICATION,
        Method.CONTROL_NOTIFY,
        Method.CONTROL_FOLLOWUP,
        Method.CONTROL_STEER,
        Method.CONTROL_INTERRUPT,
        Method.CONTROL_SPAWN,
        Method.LEASE_RENEW,
        Method.LEASE_RELEASE,
        Method.ATTEMPT_WRITE_BACK,
    }
)


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
    rpc_stop = threading.Event()
    wake_event = threading.Event()
    fired_tasks: SimpleQueue[str] = SimpleQueue()
    rpc_thread: threading.Thread | None = None
    token_path = root / "daemon.token"
    if token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:

            def dispatch_rpc(raw: dict[str, Any]) -> dict[str, Any]:
                # Use the single canonical parser; re-coercing fields here would
                # silently reintroduce bool/string ambiguity at the daemon edge.
                envelope = parse_envelope(raw)
                rpc_conn = connect(StoreConfig(db_path=root / "state.db"))
                try:
                    ensure_schema(rpc_conn)
                    result = route(
                        envelope,
                        conn=rpc_conn,
                        now=clock(),
                        registry=ExecutorRegistry.load_from_file(root / REGISTRY_FILE),
                    )
                    if envelope.method is Method.DAEMON_WAKE and result.get("ok"):
                        task_id = result.get("result", {}).get("task_id")
                        if isinstance(task_id, str):
                            fired_tasks.put(task_id)
                            wake_event.set()
                    elif envelope.method in _WAKE_ON_RPC and result.get("ok"):
                        wake_event.set()
                    return result
                finally:
                    rpc_conn.close()

            def serve_rpc() -> None:
                try:
                    serve_unix_socket(
                        endpoint=rpc_socket_path(root),
                        token=token,
                        dispatch=dispatch_rpc,
                        stop_event=rpc_stop,
                    )
                except (OSError, RuntimeError) as exc:
                    if emit_fn is not None:
                        emit_fn(f"rpc/degraded: local socket unavailable: {exc}")

            rpc_thread = threading.Thread(
                target=serve_rpc,
                name="lhgp-rpc",
                daemon=True,
            )
            rpc_thread.start()
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
            # §9 步骤 2 / §11.3：先 reconcile 外部 attempt，再谈其它。
            # 本进程仍持有活句柄的 attempt 让给 runner 自己管（locally_tracked）。
            reconcile_attempts(
                root,
                conn,
                now=now_val,
                resolve_adapter=runner.adapter_for,
                locally_tracked=runner.is_tracking,
                emit=emit_fn,
            )
            runner.adopt_reconciled_attempts()
            # 合同进入 cancelled/expired 后，控制面已不再允许继续推进；
            # 在 poll 前终止本进程持有的外部 attempt，避免已终止承诺
            # 继续消耗资源或产生迟到写回。
            _cancel_terminal_contract_attempts(conn, runner, now_val)
            # 先回收上一轮的收尾者并给存活者续心跳，再调度，最后拉起新 attempt
            runner.poll_attempts(now_val)
            # 消费 control/interrupt 请求（用户通过 RPC 打断执行中的 attempt）
            _consume_interrupt_requests(root, conn, runner, now_val)
            # 消费 verification/requested 请求（用户直接请求验收，§12.4）
            _consume_verification_requests(root, conn, runner, now_val)
            # 消费由本机计划任务经 daemon/wake 投递的一次性 fired 信号；
            # 先解除旧登记，再由本轮 tick 计算并重新 arm 下一决策点。
            while True:
                try:
                    rtc_task_id = fired_tasks.get_nowait()
                except Empty:
                    break
                rtc.note_fired(rtc_task_id)
            res = run_daemon_tick(root, conn, registry, now=now_val, emit_fn=emit_fn)
            if emit_fn is not None:
                drain_notifications(
                    conn,
                    now=now_val,
                    deliver=lambda notification: emit_fn(
                        _json.dumps(
                            {
                                "notification": notification.event_type,
                                "channel": notification.channel,
                                "goal_id": notification.goal_id,
                                "payload": notification.payload,
                                "idempotency_key": notification.idempotency_key,
                            },
                            ensure_ascii=False,
                        )
                    ),
                )
            total_dispatched += int(res.get("dispatched", 0))
            total_expired += int(res.get("expired", 0))
            for started in res.get("attempts_started", []):
                runner.start_attempt(
                    now_val,
                    contract_id=str(started["contract_id"]),
                    attempt_id=str(started["attempt_id"]),
                    executor_id=str(started["executor_id"]),
                    model=str(started.get("model", "*")),
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
                # P4：按最早决策点自适应休眠（SPEC §9 next_decision_at）。
                # 有活 attempt 时保底心跳节奏（续约/回收观察不能停）；
                # 全部空闲时睡到「下一个必须回头看」的时刻，不做 60s 盲轮询。
                # 用本轮已取的 now_val 计算时长，不二次调用 clock()（可注入
                # 有限迭代器，多调一次就 StopIteration）。
                sleep_seconds = interval_seconds
                if runner.is_idle():
                    next_at = earliest_next_decision_at(conn, now=now_val)
                    if next_at is not None:
                        until = (next_at - now_val).total_seconds()
                        sleep_seconds = min(interval_seconds, max(0.5, until))
                if sleep_fn is time.sleep:
                    # 真实 daemon 用可被 daemon/wake 唤醒的等待；测试注入的
                    # sleep_fn 保持原有确定性语义，不触碰线程事件。
                    wake_event.wait(sleep_seconds)
                    wake_event.clear()
                else:
                    sleep_fn(sleep_seconds)
    finally:
        rpc_stop.set()
        if rpc_thread is not None:
            rpc_thread.join(timeout=2)
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


def _consume_verification_requests(
    root: Path,
    conn: sqlite3.Connection,
    runner: AttemptRunner,
    now: datetime,
) -> None:
    """消费 verification/requested 事件（SPEC §12.4 用户触发验收）。

    RPC handler（contract/request-verification）只做校验与事件落库；
    daemon 每轮 tick 顶部扫描请求事件，用持有进程表的 AttemptRunner
    派生独立 verifier（spawn 必须由 daemon 做——RPC handler 没有进程表，
    与 control/interrupt 相同的「写事件-消费」分工）。

    幂等：attempts 表一旦出现 role=verifier 行（含 terminal——
    §12.3 历史 verifier 的存在本就不阻止新派生，新的请求走新事件），
    同一请求事件不再重复兑现。
    """
    for contract in list_contracts(conn, limit=1000):
        if contract.state != ContractState.ACTIVE:
            continue
        requested = [
            event
            for event in get_events(conn, contract_id=contract.contract_id)
            if event.event_type == EventType.VERIFICATION_REQUESTED
        ]
        if not requested:
            continue
        consumed_request_ids: set[int] = set()
        for event in get_events(conn, contract_id=contract.contract_id):
            if event.event_type != EventType.VERIFICATION_CONSUMED:
                continue
            try:
                payload = _json.loads(event.payload_json or "{}")
                consumed_request_ids.add(int(payload["request_event_id"]))
            except (KeyError, TypeError, ValueError, _json.JSONDecodeError):
                # A malformed marker cannot prove consumption; leave the
                # request eligible and let the next tick retry it.
                continue
        pending = next(
            (event for event in requested if event.event_id not in consumed_request_ids),
            None,
        )
        if pending is None:
            continue
        # attempts.goal_id stores the owning Goal identity, not the contract
        # identity.  A contract may be bound to a long-lived goal, so using
        # contract_id here would miss an existing verifier and break the
        # request-consumption idempotence guarantee.
        already = conn.execute(
            "SELECT attempt_id FROM attempts "
            "WHERE goal_id = ? AND role = 'verifier' "
            "AND state NOT IN ('succeeded', 'failed', 'cancelled', 'stale', 'orphaned') "
            "LIMIT 1",
            (contract.goal_id,),
        ).fetchone()
        if already is not None:
            continue
        last_executor = conn.execute(
            "SELECT executor_id FROM attempts "
            "WHERE goal_id = ? AND role = 'executor' "
            "ORDER BY admitted_at DESC LIMIT 1",
            (contract.goal_id,),
        ).fetchone()
        executor_id = str(last_executor[0]) if last_executor else ""
        ok = runner._dispatch_verifier(
            now, contract_id=contract.contract_id, executor_id=executor_id
        )
        append_event(
            conn,
            contract_id=contract.contract_id,
            goal_id=contract.goal_id,
            event_type=EventType.VERIFICATION_CONSUMED,
            payload={
                "request_event_id": pending.event_id,
                "outcome": "dispatched" if ok else "refused",
            },
            now=now,
            actor="daemon",
            contract_revision=contract.revision,
            role="verifier",
        )
        if ok:
            append_event(
                conn,
                contract_id=contract.contract_id,
                goal_id=contract.goal_id,
                event_type=EventType.VERIFICATION_STARTED,
                payload={
                    "requested_by": "user",
                    "executor_of_record": executor_id,
                    "request_event_id": pending.event_id,
                },
                now=now,
                actor="daemon",
            )
            rebuild_projection(root, contract.contract_id, conn)


def _cancel_terminal_contract_attempts(
    conn: sqlite3.Connection,
    runner: AttemptRunner,
    now: datetime,
) -> None:
    """终止已取消/已过期合同的本进程 attempt（幂等、只处理本进程）。"""
    terminal_contracts = {
        contract.contract_id
        for contract in list_contracts(conn, limit=1000)
        if contract.state in (ContractState.CANCELLED, ContractState.EXPIRED)
    }
    for contract_id, attempt_id in runner.running_attempts():
        if contract_id not in terminal_contracts:
            continue
        runner.cancel_attempt(
            now,
            contract_id=contract_id,
            attempt_id=attempt_id,
            reason="contract reached terminal state",
            actor="daemon",
        )


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

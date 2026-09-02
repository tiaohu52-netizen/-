"""常驻调度主循环（DESIGN §3.3、§6.4、§10）。

循环执行 run_daemon_tick + 执行桥接：每轮 tick 前观察/回收 attempt、
tick 后真实拉起新 attempt；分层唤醒（L0 电源守卫 / L1 计划任务）
与 control/interrupt 消费在轮内兑现。
"""

from __future__ import annotations

import json as _json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from longtask.adapters.registry import ExecutorRegistry
from longtask.cli.daemon_proc import (
    DAEMON_STOP_FILE,
    DEFAULT_TICK_INTERVAL_SECONDS,
    REGISTRY_FILE,
)
from longtask.cli.runner import AttemptRunner
from longtask.cli.tick import run_daemon_tick
from longtask.persistence.decisions import earliest_next_decision_at
from longtask.persistence.events import EventType
from longtask.persistence.projections import rebuild_projection
from longtask.persistence.store import (
    StoreConfig,
    connect,
    ensure_schema,
    get_events,
    list_contracts,
)
from longtask.promoter.reconcile import reconcile_attempts
from longtask.scheduler.wakeup import (
    NullSchedulePort,
    PowerPort,
    RtcAlarm,
    SchedulePort,
    SleepGuard,
    WindowsPowerPort,
    guard_needed,
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
                sleep_fn(sleep_seconds)
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

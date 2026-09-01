"""分层唤醒（DESIGN §6.4、ADR-0002）：L0 电源守卫 + L1 计划任务唤醒。

核心不变式（ADR-0002）：唤醒源永远不是权威——外部唤醒只能「推」
（通知 + 唤醒信号），不能读合同内容、不能仲裁、不能写状态；仲裁仍只
发生在 longtaskd 醒来后的首轮扫描（仲裁时刻语义不变）。

实现范围（与 claims 注册表如实对齐）：
- L0 SleepGuard：active 租约存活或 u ≥ 1.0 时持有系统电源请求
  （Windows SetThreadExecutionState），事件 wakeup/sleep-guard；
- L1 RtcAlarm：为 active 合同注册带 wake 标志的计划任务
  （max(next_wakeup_at, deadline_at - safety_margin)），事件
  wakeup/rtc-armed / wakeup/rtc-fired；
- 任一层失效：记 wakeup/degraded 事件并降级声明（§11.4），绝不静默。
  L2（云侧准时通知）与 L3（常在线中继）依赖外部基础设施，
  本参考实现不部署，strict_deadline 的 notify_only/notify_and_wake
  相应能力如实报 degraded（fail-closed，不假装 strict）。

所有系统交互（电源 API、计划任务程序）经可注入端口，测试零外部副作用。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from longtask.persistence.events import EventType
from longtask.persistence.store import append_event, get_lease, list_contracts

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED：持续持有「系统勿睡」请求
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
# L1 安全边距：deadline 前这么久就注册唤醒，给仲裁与补跑留余量
DEFAULT_SAFETY_MARGIN = timedelta(minutes=30)


class PowerPort(Protocol):
    """电源请求端口（L0）：acquire 返回句柄，release 归还。"""

    def acquire(self, reason: str) -> Any: ...

    def release(self, handle: Any) -> None: ...


class SchedulePort(Protocol):
    """计划任务端口（L1）：注册/注销带 wake 标志的一次性定时任务。"""

    def arm(self, task_id: str, at: datetime) -> None: ...

    def disarm(self, task_id: str) -> None: ...

    def is_available(self) -> bool: ...


class NullSchedulePort:
    """L1 空通道：平台无计划任务唤醒可用时如实报不可用（记 degraded）。"""

    def is_available(self) -> bool:
        return False

    def arm(self, task_id: str, at: datetime) -> None:
        raise OSError(f"schedule port unavailable: cannot arm {task_id}")

    def disarm(self, task_id: str) -> None:
        raise OSError(f"schedule port unavailable: cannot disarm {task_id}")


class WindowsPowerPort:
    """SetThreadExecutionState 实现（仅 Windows；其他平台如实报不可用）。"""

    def __init__(self) -> None:
        import sys

        self._available = sys.platform == "win32"

    def is_available(self) -> bool:
        return self._available

    def acquire(self, reason: str) -> Any:
        if not self._available:
            raise OSError("SetThreadExecutionState is Windows-only")
        import ctypes

        # 返回值非 0 即持有成功；句柄语义由 EXECUTION_STATE 值本身承载
        state = ctypes.c_uint(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        result = ctypes.windll.kernel32.SetThreadExecutionState(state)
        if not result:
            raise OSError("SetThreadExecutionState failed")
        return result

    def release(self, handle: Any) -> None:
        if not self._available:
            return
        import ctypes

        # 归还 INFINITE 恢复默认；失败再抛（调用方记 degraded）
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            ctypes.c_uint(0x80000000)  # ES_CONTINUOUS
        )
        if not result:
            raise OSError("SetThreadExecutionState release failed")


@dataclass(frozen=True, slots=True)
class WakeupDecision:
    """一轮 tick 的唤醒决策结果（audit 用）。"""

    guard_held: bool
    armed_contracts: tuple[str, ...]
    degraded_layers: tuple[str, ...]


class SleepGuard:
    """L0 电源守卫：按「active 租约存活或 u ≥ 1.0」持有电源请求。

    状态转换即事件（挂在触发守卫的合同上，进该合同的 log.jsonl 审计）：
    持有记 wakeup/sleep-guard(held=true)，释放记 held=false；
    acquire/release 失败记 wakeup/degraded(layer=L0)。
    """

    def __init__(self, power: PowerPort) -> None:
        self._power = power
        self._handle: Any = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def update(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
        guard_needed: bool,
        reason: str,
        contract_id: str,
    ) -> bool:
        """按需持有/释放；返回当前持有状态。失败降级记事件，不抛出。"""
        if guard_needed and self._handle is None:
            try:
                self._handle = self._power.acquire(reason)
                append_event(
                    conn,
                    contract_id=contract_id,
                    event_type=EventType.WAKEUP_SLEEP_GUARD,
                    payload={"held": True, "reason": reason},
                    now=now,
                    actor="daemon",
                )
            except OSError as exc:
                append_event(
                    conn,
                    contract_id=contract_id,
                    event_type=EventType.WAKEUP_DEGRADED,
                    payload={"layer": "L0", "reason": str(exc)},
                    now=now,
                    actor="daemon",
                )
        elif not guard_needed and self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                self._power.release(handle)
                append_event(
                    conn,
                    contract_id=contract_id,
                    event_type=EventType.WAKEUP_SLEEP_GUARD,
                    payload={"held": False, "reason": reason},
                    now=now,
                    actor="daemon",
                )
            except OSError as exc:
                append_event(
                    conn,
                    contract_id=contract_id,
                    event_type=EventType.WAKEUP_DEGRADED,
                    payload={"layer": "L0", "reason": str(exc)},
                    now=now,
                    actor="daemon",
                )
        return self._handle is not None


class RtcAlarm:
    """L1 计划任务唤醒：为 active 合同注册带 wake 位的一次性任务。

    每个 active 合同一个 task_id（longtask-wakeup-<cid>）；目标时刻取
    max(next_wakeup_at, deadline_at - safety_margin)。注册/注销失败记
    wakeup/degraded(layer=L1)，绝不静默假装已注册。
    """

    def __init__(self, schedule: SchedulePort, safety_margin: timedelta = DEFAULT_SAFETY_MARGIN):
        self._schedule = schedule
        self._safety_margin = safety_margin
        self._armed: dict[str, datetime] = {}

    def refresh(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
    ) -> tuple[str, ...]:
        """对齐 active 合同与已注册任务集；返回当前 armed 的合同 id 元组。"""
        if not self._schedule.is_available():
            append_event(
                conn,
                contract_id=None,
                event_type=EventType.WAKEUP_DEGRADED,
                payload={"layer": "L1", "reason": "schedule port unavailable on this platform"},
                now=now,
                actor="daemon",
            )
            self._armed.clear()
            return ()

        from longtask.contracts.schema import ContractState

        targets: dict[str, datetime] = {}
        for contract in list_contracts(conn, limit=1000):
            if contract.state != ContractState.ACTIVE:
                continue
            cid = contract.contract_id
            wakeup = contract.next_wakeup_at
            deadline_minus_margin = contract.draft.deadline_at - self._safety_margin
            # max(next_wakeup_at, deadline - margin)：两者都缺就不注册
            candidates = [t for t in (wakeup, deadline_minus_margin) if t is not None]
            if not candidates:
                continue
            targets[cid] = max(candidates)

        # 目标时刻已变或新出现的合同：重新注册
        for cid, at in targets.items():
            if self._armed.get(cid) != at:
                task_id = f"longtask-wakeup-{cid}"
                try:
                    self._schedule.arm(task_id, at)
                    self._armed[cid] = at
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.WAKEUP_RTC_ARMED,
                        payload={"task_id": task_id, "at": at.isoformat()},
                        now=now,
                        actor="daemon",
                    )
                except OSError as exc:
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.WAKEUP_DEGRADED,
                        payload={"layer": "L1", "reason": str(exc)},
                        now=now,
                        actor="daemon",
                    )

        # 终态/消失的合同：注销
        for cid in list(self._armed):
            if cid not in targets:
                try:
                    self._schedule.disarm(f"longtask-wakeup-{cid}")
                except OSError as exc:
                    append_event(
                        conn,
                        contract_id=cid,
                        event_type=EventType.WAKEUP_DEGRADED,
                        payload={"layer": "L1", "reason": str(exc)},
                        now=now,
                        actor="daemon",
                    )
                del self._armed[cid]
        return tuple(sorted(self._armed))


def guard_needed(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    urgency_threshold: float = 1.0,
) -> tuple[bool, str | None]:
    """L0 持有条件（ADR-0002）：任一合同 active 租约存活，或 u ≥ 阈值。

    返回 (needed, 触发守卫的合同 id)：事件挂到触发合同上做审计。
    """
    from longtask.contracts.schema import ContractState

    for contract in list_contracts(conn, limit=1000):
        if contract.state != ContractState.ACTIVE:
            continue
        lease = get_lease(conn, contract.contract_id)
        if lease is not None and lease.is_alive(now):
            return True, contract.contract_id
        # u = 剩余工作量 / 剩余时间；越 deadline 视为无穷紧迫，同样需要守卫
        time_left_hours = (contract.draft.deadline_at - now).total_seconds() / 3600.0
        if time_left_hours <= 0:
            return True, contract.contract_id
        if contract.draft.workload_initial_hours / time_left_hours >= urgency_threshold:
            return True, contract.contract_id
    return False, None


__all__ = [
    "DEFAULT_SAFETY_MARGIN",
    "NullSchedulePort",
    "PowerPort",
    "RtcAlarm",
    "SchedulePort",
    "SleepGuard",
    "WakeupDecision",
    "WindowsPowerPort",
    "guard_needed",
]

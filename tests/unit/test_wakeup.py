"""分层唤醒单元测试（DESIGN §6.4、ADR-0002）。

端口全部注入 fake：零系统副作用、无真实墙钟。覆盖：
- L0 guard_needed 判定（active 租约存活 / u >= 1.0 / 越 Deadline / 无需守卫）；
- SleepGuard 持有/释放状态转换即事件，失败降级记 wakeup/degraded；
- RtcAlarm 对齐 active 合同（注册目标时刻取最早的决策/唤醒/安全边距时刻）、
  终态合同注销、端口不可用降级；
- 事件词汇表 wakeup/* 与 ADR-0002 一致。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from longtask.contracts.schema import Acceptance, Budget, ContractDraft, ContractState
from longtask.persistence.events import EventType
from longtask.persistence.store import (
    StoreConfig,
    acquire_lease,
    connect,
    ensure_schema,
    get_events,
    save_contract,
    update_contract_state,
)
from longtask.scheduler.wakeup import (
    DEFAULT_SAFETY_MARGIN,
    NullSchedulePort,
    RtcAlarm,
    SleepGuard,
    guard_needed,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


class FakePowerPort:
    """可编程电源端口：可注入 acquire/release 失败。"""

    def __init__(self, fail_acquire: bool = False, fail_release: bool = False) -> None:
        self.fail_acquire = fail_acquire
        self.fail_release = fail_release
        self.acquired: list[str] = []
        self.released: list[str] = []

    def acquire(self, reason: str) -> Any:
        if self.fail_acquire:
            raise OSError("power acquire failed")
        self.acquired.append(reason)
        return object()

    def release(self, handle: Any) -> None:
        if self.fail_release:
            raise OSError("power release failed")
        self.released.append(str(handle))


class FakeSchedulePort:
    """可编程计划任务端口：记录 arm/disarm，可注入失败与不可用。"""

    def __init__(
        self, available: bool = True, fail_arm: bool = False, fail_disarm: bool = False
    ) -> None:
        self._available = available
        self.fail_arm = fail_arm
        self.fail_disarm = fail_disarm
        self.armed: dict[str, datetime] = {}
        self.disarmed: list[str] = []

    def is_available(self) -> bool:
        return self._available

    def arm(self, task_id: str, at: datetime) -> None:
        if self.fail_arm:
            raise OSError(f"arm failed: {task_id}")
        self.armed[task_id] = at

    def disarm(self, task_id: str) -> None:
        if self.fail_disarm:
            raise OSError(f"disarm failed: {task_id}")
        self.disarmed.append(task_id)


def make_store(tmp_path: Path) -> Any:
    conn = connect(StoreConfig(db_path=tmp_path / "state.db"))
    ensure_schema(conn)
    return conn


def save_active_contract(
    conn: Any,
    cid: str,
    *,
    deadline: datetime,
    workload_hours: float = 4.0,
    next_wakeup: datetime | None = None,
) -> None:
    draft = ContractDraft(
        title="唤醒测试合同",
        objective="验证分层唤醒",
        deadline_at=deadline,
        hard_constraints={"file_effects": {"mode": "workspace-write"}},
        acceptance=Acceptance(standard="测试通过", checks=("通过",)),
        workload_initial_hours=workload_hours,
        budget=Budget(
            max_dispatches=5,
            max_escalations=2,
            max_concurrent_attempts=1,
            max_attempt_minutes=60,
            max_output_bytes=1048576,
        ),
    )
    save_contract(conn, draft, contract_id=cid, now=NOW)
    update_contract_state(conn, contract_id=cid, new_state=ContractState.ACTIVE, now=NOW)
    if next_wakeup is not None:
        conn.execute(
            "UPDATE contracts SET next_wakeup_at = ? WHERE contract_id = ?",
            (next_wakeup.isoformat(), cid),
        )
        conn.commit()


def event_types(conn: Any, cid: str) -> list[str]:
    return [str(e.event_type) for e in get_events(conn, contract_id=cid)]


class TestGuardNeeded:
    def test_live_lease_triggers_guard(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w01", deadline=NOW + timedelta(hours=10))
        acquire_lease(
            conn,
            contract_id="lt-w01",
            holder_attempt_id="att-1",
            expected_generation=0,
            heartbeat_at=NOW,
            timeout=timedelta(minutes=30),
        )
        needed, cid = guard_needed(conn, now=NOW)
        assert needed is True
        assert cid == "lt-w01"
        conn.close()

    def test_high_urgency_triggers_guard(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        # 4h 工作 / 2h 剩余 -> u = 2.0 >= 1.0
        save_active_contract(conn, "lt-w02", deadline=NOW + timedelta(hours=2), workload_hours=4.0)
        needed, cid = guard_needed(conn, now=NOW)
        assert needed is True
        assert cid == "lt-w02"
        conn.close()

    def test_past_deadline_triggers_guard(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w03", deadline=NOW - timedelta(hours=1), workload_hours=0.1)
        needed, _cid = guard_needed(conn, now=NOW)
        assert needed is True
        conn.close()

    def test_calm_contract_no_guard(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        # 1h 工作 / 100h 剩余 -> u = 0.01 < 1.0，无租约
        save_active_contract(
            conn, "lt-w04", deadline=NOW + timedelta(hours=100), workload_hours=1.0
        )
        needed, cid = guard_needed(conn, now=NOW)
        assert needed is False
        assert cid is None
        conn.close()


class TestSleepGuard:
    def test_hold_and_release_emit_events(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        power = FakePowerPort()
        guard = SleepGuard(power)

        held = guard.update(
            conn, now=NOW, guard_needed=True, reason="lease alive", contract_id="lt-w01"
        )
        assert held is True
        assert len(power.acquired) == 1

        held = guard.update(conn, now=NOW, guard_needed=False, reason="calm", contract_id="")
        assert held is False
        assert len(power.released) == 1

        types = event_types(conn, "lt-w01")
        assert types.count(EventType.WAKEUP_SLEEP_GUARD.value) == 1  # 持有事件
        conn.close()

    def test_acquire_failure_records_degraded(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        guard = SleepGuard(FakePowerPort(fail_acquire=True))

        held = guard.update(
            conn, now=NOW, guard_needed=True, reason="lease alive", contract_id="lt-w01"
        )
        assert held is False  # 失败即未持有，不假装
        degraded = [
            e
            for e in get_events(conn, contract_id="lt-w01")
            if str(e.event_type) == EventType.WAKEUP_DEGRADED.value
        ]
        assert len(degraded) == 1
        assert "L0" in degraded[0].payload_json
        conn.close()


class TestRtcAlarm:
    def test_arms_active_contract_at_earliest_decision_or_deadline_margin(
        self, tmp_path: Path
    ) -> None:
        conn = make_store(tmp_path)
        deadline = NOW + timedelta(hours=10)
        wakeup = NOW + timedelta(minutes=20)
        save_active_contract(conn, "lt-w10", deadline=deadline, next_wakeup=wakeup)

        port = FakeSchedulePort()
        alarm = RtcAlarm(port)
        armed = alarm.refresh(conn, now=NOW)

        assert armed == ("lt-w10",)
        # next_wakeup 是更早的真实决策点，不能被 deadline 安全边距遮蔽。
        expected = wakeup
        assert port.armed["longtask-wakeup-lt-w10"] == expected
        assert EventType.WAKEUP_RTC_ARMED.value in event_types(conn, "lt-w10")
        conn.close()

    def test_arms_at_deadline_margin_when_no_earlier_decision_exists(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        deadline = NOW + timedelta(hours=10)
        wakeup = NOW + timedelta(hours=12)
        save_active_contract(conn, "lt-w10b", deadline=deadline, next_wakeup=wakeup)

        port = FakeSchedulePort()
        RtcAlarm(port).refresh(conn, now=NOW)

        assert port.armed["longtask-wakeup-lt-w10b"] == deadline - DEFAULT_SAFETY_MARGIN
        conn.close()

    def test_disarms_terminal_contract(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w11", deadline=NOW + timedelta(hours=10))
        port = FakeSchedulePort()
        alarm = RtcAlarm(port)
        alarm.refresh(conn, now=NOW)
        assert port.armed

        # 合同转终态：下一轮刷新应注销
        update_contract_state(
            conn,
            contract_id="lt-w11",
            new_state=ContractState.COMPLETE,
            now=NOW,
        )
        armed = alarm.refresh(conn, now=NOW + timedelta(minutes=1))
        assert armed == ()
        assert port.disarmed == ["longtask-wakeup-lt-w11"]
        conn.close()

    def test_unavailable_port_records_degraded(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w12", deadline=NOW + timedelta(hours=10))

        alarm = RtcAlarm(FakeSchedulePort(available=False))
        armed = alarm.refresh(conn, now=NOW)
        assert armed == ()
        # 空合同的全局事件：从事件表直接查
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE event_type = ?", ("wakeup/degraded",)
        ).fetchall()
        assert len(rows) == 1
        assert "L1" in rows[0][0]
        conn.close()

    def test_unavailable_port_degraded_event_is_deduplicated(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w13", deadline=NOW + timedelta(hours=10))
        alarm = RtcAlarm(FakeSchedulePort(available=False))
        alarm.refresh(conn, now=NOW)
        alarm.refresh(conn, now=NOW + timedelta(minutes=1))
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?", ("wakeup/degraded",)
        ).fetchone()[0]
        assert count == 1
        conn.close()

    def test_contract_arm_failure_degraded_event_is_deduplicated(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w14", deadline=NOW + timedelta(hours=10))
        alarm = RtcAlarm(FakeSchedulePort(fail_arm=True))
        alarm.refresh(conn, now=NOW)
        alarm.refresh(conn, now=NOW + timedelta(minutes=1))
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ? AND contract_id = ?",
            ("wakeup/degraded", "lt-w14"),
        ).fetchone()[0]
        assert count == 1
        conn.close()

    def test_contract_disarm_failure_is_retried(self, tmp_path: Path) -> None:
        conn = make_store(tmp_path)
        save_active_contract(conn, "lt-w15", deadline=NOW + timedelta(hours=10))
        port = FakeSchedulePort()
        alarm = RtcAlarm(port)
        alarm.refresh(conn, now=NOW)
        conn.execute("UPDATE contracts SET state = 'cancelled' WHERE contract_id = 'lt-w15'")
        port.fail_disarm = True
        alarm.refresh(conn, now=NOW + timedelta(minutes=1))
        assert "lt-w15" in alarm._armed
        port.fail_disarm = False
        alarm.refresh(conn, now=NOW + timedelta(minutes=2))
        assert "lt-w15" not in alarm._armed
        assert port.disarmed == ["longtask-wakeup-lt-w15"]
        conn.close()

    def test_null_schedule_port_is_unavailable(self) -> None:
        port = NullSchedulePort()
        assert port.is_available() is False


def test_wakeup_event_vocabulary_matches_adr() -> None:
    """事件词汇与 ADR-0002/DESIGN §6.4 一致（只增不改）。"""
    assert EventType.WAKEUP_SLEEP_GUARD.value == "wakeup/sleep-guard"
    assert EventType.WAKEUP_RTC_ARMED.value == "wakeup/rtc-armed"
    assert EventType.WAKEUP_RTC_FIRED.value == "wakeup/rtc-fired"
    assert EventType.WAKEUP_DEGRADED.value == "wakeup/degraded"

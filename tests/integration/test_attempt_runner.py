"""执行桥接层集成测试（DESIGN §3.4、§5.1、§7、§10）。

真实 SQLite + 真实子进程（sys.executable 微型脚本，秒级完成）覆盖
AttemptRunner 全生命周期：spawn 成功、spawn 失败、死租约回收再分发、
预算事件计数触顶转 blocked(need-user)。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from longtask.adapters.manifest import Capabilities, SandboxCapability
from longtask.adapters.registry import CostHint, ExecutorRegistry, LaunchSpec, RegistryEntry
from longtask.cli.daemon import run_daemon_tick
from longtask.cli.runner import AttemptRunner
from longtask.contracts.schema import (
    Acceptance,
    BlockReason,
    Budget,
    ContractDraft,
    ContractState,
    Enforcement,
)
from longtask.persistence.events import EventType
from longtask.persistence.store import (
    StoreConfig,
    acquire_lease,
    connect,
    ensure_schema,
    get_contract,
    get_events,
    get_lease,
    save_contract,
    update_contract_state,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def make_caps() -> Capabilities:
    return Capabilities(
        spawn=True,
        observe=True,
        cancel=True,
        notify=False,
        followup=False,
        steer=False,
        interrupt=True,
        context="optional",
        sandbox=SandboxCapability(
            file_effects="workspace-write",
            network="unsupported",
            process="unsupported",
            enforcement=Enforcement.PARTIAL,
        ),
        acceptance_evidence=True,
    )


def make_registry(*, argv: tuple[str, ...]) -> ExecutorRegistry:
    reg = ExecutorRegistry()
    reg.register(
        RegistryEntry(
            id="exec-1",
            kind="subprocess",
            launch=LaunchSpec(argv=argv, env_allowlist=("PATH", "SYSTEMROOT", "TEMP")),
            capabilities=make_caps(),
            limits={"max_concurrent_attempts": 2},
            cost_hint=CostHint.LOW,
            enabled=True,
        )
    )
    return reg


def make_contract(
    data_dir: Path, cid: str, *, max_dispatches: int = 5, max_attempt_minutes: int = 60
) -> None:
    conn = connect(StoreConfig(db_path=data_dir / "state.db"))
    ensure_schema(conn)
    draft = ContractDraft(
        title="执行桥接测试合同",
        objective="验证 AttemptRunner 生命周期",
        deadline_at=NOW + timedelta(hours=2),  # u=2.0 -> RESPAWN
        hard_constraints={
            "file_effects": {
                "mode": "workspace-write",
                "workspace_root": str(data_dir / "ws"),
            }
        },
        acceptance=Acceptance(standard="测试通过", checks=("通过",)),
        workload_initial_hours=4.0,
        budget=Budget(
            max_dispatches=max_dispatches,
            max_escalations=2,
            max_concurrent_attempts=1,
            max_attempt_minutes=max_attempt_minutes,
            max_output_bytes=1048576,
        ),
    )
    save_contract(conn, draft, contract_id=cid, now=NOW)
    update_contract_state(conn, contract_id=cid, new_state=ContractState.ACTIVE, now=NOW)
    conn.close()


def open_store(data_dir: Path) -> object:
    return connect(StoreConfig(db_path=data_dir / "state.db"))


def event_types(conn: object, cid: str) -> list[str]:
    return [str(e.event_type) for e in get_events(conn, contract_id=cid)]  # type: ignore[arg-type]


def test_runner_spawns_collects_and_releases_lease(tmp_path: Path) -> None:
    """成功路径：dispatch -> spawn 真实子进程 -> 收尾 attempt/succeeded + 租约释放。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ws").mkdir()
    cid = "lt-20260901-r01"
    script = "import pathlib; pathlib.Path('done.txt').write_text('ok', encoding='utf-8')"
    make_registry(argv=(sys.executable, "-c", script)).save_to_file(data_dir / "registry.json")
    make_contract(data_dir, cid)

    conn = open_store(data_dir)
    try:
        runner = AttemptRunner(
            data_dir, conn, ExecutorRegistry.load_from_file(data_dir / "registry.json")
        )
        res = run_daemon_tick(
            data_dir, conn, ExecutorRegistry.load_from_file(data_dir / "registry.json"), now=NOW
        )
        assert res["dispatched"] == 1
        started = res["attempts_started"][0]
        assert started["executor_id"] == "exec-1"

        assert runner.start_attempt(
            NOW,
            contract_id=started["contract_id"],
            attempt_id=started["attempt_id"],
            executor_id=started["executor_id"],
        )
        assert runner.spawned_count == 1
        # 子进程秒级完成：先真实等待进程退出，再 poll 回收（poll 的 observe
        # 是瞬时快照，进程未退出时会续约留到下一轮——这是正常时序）。
        # 等待 done.txt 落盘是必要但不充分——必须 proc.poll() 返 0 才是真的终态。
        import time

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if (data_dir / "ws" / "done.txt").is_file():
                # 再多等一拍确保进程回收资源后退出码可读
                time.sleep(0.1)
                break
            time.sleep(0.05)
        runner.poll_attempts(NOW + timedelta(seconds=1))
        assert runner.finished_count == 1

        types = event_types(conn, cid)
        assert "attempt/started" in types
        assert "attempt/succeeded" in types
        # 终态回收后租约已释放
        assert get_lease(conn, cid) is None
        # 子进程确实在 workspace 里写了产出
        assert (data_dir / "ws" / "done.txt").read_text(encoding="utf-8") == "ok"
    finally:
        conn.close()  # type: ignore[attr-defined]


def test_runner_failed_attempt_releases_lease_and_redispatches(tmp_path: Path) -> None:
    """失败路径：argv 指向不存在的可执行文件 -> spawn OSError -> attempt/failed + 租约释放。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cid = "lt-20260901-r02"
    make_registry(argv=("definitely-not-a-real-executable-xyz", "exec")).save_to_file(
        data_dir / "registry.json"
    )
    make_contract(data_dir, cid)

    conn = open_store(data_dir)
    try:
        registry = ExecutorRegistry.load_from_file(data_dir / "registry.json")
        runner = AttemptRunner(data_dir, conn, registry)
        res = run_daemon_tick(data_dir, conn, registry, now=NOW)
        started = res["attempts_started"][0]

        assert not runner.start_attempt(
            NOW,
            contract_id=started["contract_id"],
            attempt_id=started["attempt_id"],
            executor_id=started["executor_id"],
        )
        types = event_types(conn, cid)
        assert "attempt/failed" in types
        assert get_lease(conn, cid) is None  # 失败即释放，不悬挂

        # 失败释放后下一轮可再派工（预算 5 未触顶）
        res2 = run_daemon_tick(data_dir, conn, registry, now=NOW + timedelta(minutes=1))
        assert res2["dispatched"] == 1
    finally:
        conn.close()  # type: ignore[attr-defined]


def test_running_attempt_is_terminated_at_contract_timeout(tmp_path: Path) -> None:
    """max_attempt_minutes 是硬上限，不应因心跳续租而无限运行。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ws").mkdir()
    cid = "lt-20260901-timeout"
    make_registry(argv=(sys.executable, "-c", "import time; time.sleep(120)")).save_to_file(
        data_dir / "registry.json"
    )
    make_contract(data_dir, cid, max_attempt_minutes=1)

    conn = open_store(data_dir)
    try:
        registry = ExecutorRegistry.load_from_file(data_dir / "registry.json")
        runner = AttemptRunner(data_dir, conn, registry)
        res = run_daemon_tick(data_dir, conn, registry, now=NOW)
        started = res["attempts_started"][0]
        assert runner.start_attempt(
            NOW,
            contract_id=cid,
            attempt_id=started["attempt_id"],
            executor_id="exec-1",
        )

        runner.poll_attempts(NOW + timedelta(minutes=2))
        row = conn.execute(
            "SELECT state, error_class FROM attempts WHERE attempt_id = ?",
            (started["attempt_id"],),
        ).fetchone()
        assert row == ("failed", "attempt-timeout")
        assert get_lease(conn, cid) is None
    finally:
        conn.close()  # type: ignore[attr-defined]


def test_dead_lease_reclaimed_before_redispatch(tmp_path: Path) -> None:
    """死租约回收：旧持有者心跳超时 -> redispatch 走 lease/reclaimed 而非裸 acquire。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cid = "lt-20260901-r03"
    make_registry(argv=("definitely-not-a-real-executable-xyz",)).save_to_file(
        data_dir / "registry.json"
    )
    make_contract(data_dir, cid)

    conn = open_store(data_dir)
    try:
        # 人为制造一个心跳已死的旧租约（generation=1，心跳 NOW-10min，超时 5min）
        acquire_lease(
            conn,
            contract_id=cid,
            holder_attempt_id="att-dead",
            expected_generation=0,
            heartbeat_at=NOW - timedelta(minutes=10),
            timeout=timedelta(minutes=5),
        )
        registry = ExecutorRegistry.load_from_file(data_dir / "registry.json")
        res = run_daemon_tick(data_dir, conn, registry, now=NOW)
        assert res["dispatched"] == 1

        types = event_types(conn, cid)
        assert "lease/reclaimed" in types
        lease = get_lease(conn, cid)
        assert lease is not None
        assert lease.generation == 2  # 回收让代次 +1
        assert lease.holder_attempt_id == res["attempts_started"][0]["attempt_id"]
    finally:
        conn.close()  # type: ignore[attr-defined]


def test_budget_exhaustion_by_event_count_blocks_contract(tmp_path: Path) -> None:
    """预算硬边界（§6.3）：attempt/started 事件计数触顶 -> 档 5 blocked(need-user)。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cid = "lt-20260901-r04"
    make_registry(argv=("definitely-not-a-real-executable-xyz",)).save_to_file(
        data_dir / "registry.json"
    )
    # 预算 1：首次 dispatch 后事件计数 1/1，第二轮 tick 即触顶
    make_contract(data_dir, cid, max_dispatches=1)

    conn = open_store(data_dir)
    try:
        registry = ExecutorRegistry.load_from_file(data_dir / "registry.json")
        runner = AttemptRunner(data_dir, conn, registry)
        res1 = run_daemon_tick(data_dir, conn, registry, now=NOW)
        assert res1["dispatched"] == 1

        # 模拟真实循环：spawn 失败 -> attempt/failed + 租约释放
        started = res1["attempts_started"][0]
        assert not runner.start_attempt(
            NOW,
            contract_id=started["contract_id"],
            attempt_id=started["attempt_id"],
            executor_id=started["executor_id"],
        )

        # 第二轮：事件计数 1/1 触顶 -> 档 5 blocked(need-user)，不再派工
        res2 = run_daemon_tick(data_dir, conn, registry, now=NOW + timedelta(minutes=1))
        assert res2["dispatched"] == 0

        c_view = get_contract(conn, cid)
        assert c_view is not None
        assert c_view.state == ContractState.BLOCKED
        assert c_view.blocked_reason == BlockReason.NEED_USER
        types = event_types(conn, cid)
        assert EventType.ESCALATION_HANDED_TO_USER.value in types or "contract/blocked" in types
    finally:
        conn.close()  # type: ignore[attr-defined]

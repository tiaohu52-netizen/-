"""longtaskd 常驻进程生命周期集成测试（DESIGN §3.3、§15.2）。

真实分离子进程：start -> status running -> stop 优雅退出 -> 状态与文件清理。
全部走 main() CLI 入口；唯一真实等待是启动确认与停止轮询（秒级，integration 允许）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from longtask.cli.daemon import (
    DAEMON_STOP_FILE,
    PID_FILE,
    TOKEN_FILE,
    get_daemon_status,
    halt_daemon,
)
from longtask.cli.daemon_loop import run_daemon_loop
from longtask.cli.main import main
from longtask.persistence.store import StoreConfig, connect, ensure_schema

pytestmark = pytest.mark.integration


def test_start_stop_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 1. start：分离后台进程，写真实 pid/token
    rc = main(["--data-dir", str(data_dir), "start", "--interval", "0.2"])
    assert rc == 0
    started = json.loads(capsys.readouterr().out)
    assert started["ok"] is True
    assert (data_dir / PID_FILE).is_file()
    assert (data_dir / TOKEN_FILE).is_file()
    assert get_daemon_status(data_dir)["running"] is True
    assert "rpc_socket_available" in get_daemon_status(data_dir)

    try:
        # 2. stop：daemon.stop 优雅退出（间隔 0.2s 的循环很快消费掉标记）
        rc = main(["--data-dir", str(data_dir), "stop"])
        assert rc == 0
        halted = json.loads(capsys.readouterr().out)
        assert halted["was_running"] is True
        assert halted["forced"] is False  # 优雅退出，未升级强杀

        status = get_daemon_status(data_dir)
        assert status["running"] is False
        assert not (data_dir / PID_FILE).exists()
        assert not (data_dir / TOKEN_FILE).exists()
        assert not (data_dir / DAEMON_STOP_FILE).exists()
    finally:
        # 兜底清理：断言失败时不遗留孤儿进程
        halt_daemon(data_dir, grace_seconds=1.0)


def test_rpc_unavailable_is_audited_without_thread_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows 无 Unix socket 时，RPC 降级不应产生未捕获线程异常。"""
    import longtask.cli.daemon_loop as daemon_loop

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = connect(StoreConfig(db_path=data_dir / "state.db"))
    ensure_schema(conn)
    conn.close()
    (data_dir / TOKEN_FILE).write_text("test-token\n", encoding="utf-8")
    messages: list[str] = []

    def unavailable(**_kwargs: object) -> None:
        raise RuntimeError("this platform does not provide Unix domain sockets")

    monkeypatch.setattr(daemon_loop, "serve_unix_socket", unavailable)
    result = run_daemon_loop(data_dir, interval_seconds=0, max_cycles=1, emit_fn=messages.append)
    assert result["ok"] is True
    assert any(message.startswith("rpc/degraded:") for message in messages)


def test_start_rejects_when_already_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    rc = main(["--data-dir", str(data_dir), "start", "--interval", "0.2"])
    assert rc == 0
    capsys.readouterr()

    try:
        rc2 = main(["--data-dir", str(data_dir), "start", "--interval", "0.2"])
        assert rc2 == 1
        err = json.loads(capsys.readouterr().out)
        assert "already running" in err["error"]
    finally:
        halt_daemon(data_dir, grace_seconds=1.0)


def test_start_recovers_from_stale_pid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 一个已经退出的进程的 pid：残留 pid 文件不阻塞新 start
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait()
    (data_dir / PID_FILE).write_text(f"{dead.pid}\n", encoding="utf-8")

    rc = main(["--data-dir", str(data_dir), "start", "--interval", "0.2"])
    assert rc == 0
    capsys.readouterr()
    assert get_daemon_status(data_dir)["running"] is True

    halt_daemon(data_dir, grace_seconds=1.0)
    assert get_daemon_status(data_dir)["running"] is False

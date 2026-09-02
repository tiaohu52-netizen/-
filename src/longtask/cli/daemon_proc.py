"""longtaskd 进程生命周期管理（DESIGN §3.3、§15.2）。

spawn / halt / status 三件套与进程存活判定。pid/token/stop 标记文件
是 daemon 进程与 CLI 控制面之间的唯一通道；一切状态如实报告，
不假装成功（§0 诚实性公理）。
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from longtask.promoter.killswitch import is_kill_switch_active

PID_FILE = "daemon.pid"
TOKEN_FILE = "daemon.token"  # noqa: S105
DAEMON_STOP_FILE = "daemon.stop"
DAEMON_LOG_FILE = "daemon.log"
START_LOCK_FILE = "daemon.start.lock"
RPC_SOCKET_FILE = "daemon.sock"
REGISTRY_FILE = "registry.json"
DEFAULT_TICK_INTERVAL_SECONDS = 60.0
STOP_GRACE_SECONDS = 10.0


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
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / START_LOCK_FILE
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        status = get_daemon_status(root)
        if status["running"] and status["pid"] is not None:
            return {"ok": False, "error": f"daemon already running (pid {status['pid']})"}
        lock_path.unlink(missing_ok=True)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        status = get_daemon_status(root)
        if status["running"] and status["pid"] is not None:
            return {"ok": False, "error": f"daemon already running (pid {status['pid']})"}
        (root / PID_FILE).unlink(missing_ok=True)
        (root / TOKEN_FILE).unlink(missing_ok=True)
        token = secrets.token_hex(16)
        (root / TOKEN_FILE).write_text(f"{token}\n", encoding="utf-8")

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

        for _ in range(40):
            if proc.poll() is not None:
                (root / TOKEN_FILE).unlink(missing_ok=True)
                return {
                    "ok": False,
                    "error": f"daemon process exited immediately (code {proc.returncode})",
                    "log_tail": _read_log_tail(log_path),
                }
            time.sleep(0.05)

        (root / PID_FILE).write_text(f"{proc.pid}\n", encoding="utf-8")
        return {"ok": True, "pid": proc.pid, "interval_seconds": interval_seconds}
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)


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


def lhgpd_entrypoint() -> int:
    """lhgpd 前台常驻入口（P6 新名）：等价 `lhgp _daemon-run`。

    给习惯「专 daemon 命令」的用户一个直连入口：前台跑主循环，
    Ctrl-C 即退。分离后台启动仍走 `lhgp start`（pid/token 通道不变）。
    """
    from longtask.cli.daemon_loop import run_daemon_loop
    from longtask.cli.paths import default_data_root

    root = default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    res = run_daemon_loop(root, emit_fn=print)
    import json as _json

    print(_json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1

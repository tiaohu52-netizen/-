"""Process identity and liveness probes for restart-safe adapters."""

from __future__ import annotations

import os
import sys

IDENTITY_TOLERANCE_SECONDS = 2.0

if sys.platform == "win32":  # pragma: no cover - platform-specific branch
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_TERMINATE = 0x0001
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
    _EPOCH_AS_FILETIME = 11644473600.0

    def _filetime_to_epoch(ft: wintypes.FILETIME) -> float:
        value = (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)
        return value / 1e7 - _EPOCH_AS_FILETIME

    def _open(pid: int, access: int) -> int:
        ctypes.set_last_error(0)
        return int(_kernel32.OpenProcess(access, False, pid))

    def process_start_time(pid: int) -> float | None:
        if pid <= 0:
            return None
        handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return _filetime_to_epoch(creation)
        finally:
            _kernel32.CloseHandle(handle)

    def process_alive(pid: int) -> bool | None:
        if pid <= 0:
            return None
        handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
                return None
            return False
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return int(code.value) == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)

    def terminate_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        handle = _open(pid, _PROCESS_TERMINATE)
        if not handle:
            return False
        try:
            return bool(_kernel32.TerminateProcess(handle, 1))
        finally:
            _kernel32.CloseHandle(handle)

else:  # pragma: no cover - exercised on POSIX CI
    import signal
    from pathlib import Path

    def _read_proc_stat(pid: int) -> list[str] | None:
        """Fields of /proc/<pid>/stat after comm, or None without /proc.

        comm may contain spaces and parentheses, so parse after the final ')'
        rather than splitting the whole line.
        """
        try:
            stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            return None
        return stat_line.rpartition(")")[2].split() or None

    def _boot_time_epoch() -> float | None:
        try:
            for stat_line in (
                Path("/proc/stat").read_text(encoding="ascii", errors="replace").splitlines()
            ):
                if stat_line.startswith("btime "):
                    return float(stat_line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def process_start_time(pid: int) -> float | None:
        """Epoch start time from /proc field 22 (stable after process exit).

        st_ctime is not a start-time proxy: it changes when the process exits
        or is reaped, breaking identity checks for dead-but-unreaped runs.
        """
        if pid <= 0:
            return None
        boot = _boot_time_epoch()
        fields = _read_proc_stat(pid)
        if boot is None or not fields or len(fields) < 20:
            return None
        try:
            clk_tck = float(os.sysconf("SC_CLK_TCK"))
        except (ValueError, OSError):
            clk_tck = 100.0
        try:
            ticks = float(fields[19])
        except ValueError:
            return None
        return boot + ticks / clk_tck

    def process_alive(pid: int) -> bool | None:
        if pid <= 0:
            return None
        fields = _read_proc_stat(pid)
        if fields:
            # kill(pid, 0) succeeds on zombies, so without this check an
            # exited-but-unreaped detached run looks alive forever on Linux.
            return fields[0] != "Z"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
        except OSError:
            return None
        return True

    def terminate_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True


def identity_matches(pid: int, recorded_start_time: float | None) -> bool | None:
    """Confirm PID and recorded start time refer to the same process."""
    if pid <= 0 or recorded_start_time is None:
        return None
    actual = process_start_time(pid)
    if actual is None:
        return None
    return abs(actual - float(recorded_start_time)) <= IDENTITY_TOLERANCE_SECONDS


__all__ = [
    "IDENTITY_TOLERANCE_SECONDS",
    "identity_matches",
    "process_alive",
    "process_start_time",
    "terminate_pid",
]

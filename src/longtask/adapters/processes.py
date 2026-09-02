"""进程身份与存活探测（SPEC §11.3 process_identity）。

规范原文：任何可长期运行的 adapter 在 spawn 成功后 MUST 持久返回
`process_identity`（PID/启动时间等提示），并明确「不得单独作为身份真相」。

因此本模块提供的是**双重比对**所需的最小能力：
- `process_start_time(pid)`：进程启动时刻（epoch 秒）
- `process_alive(pid)`：存活判定
- `terminate_pid(pid)`：尽力终止

契约：无法取得就返回 None，绝不猜一个值回来。调用方拿到 None 只能
按「状态未知」处理（orphan grace），不得据此判定已终止或仍存活。

平台：Windows 走 ctypes/kernel32（无第三方依赖），POSIX 走 /proc 与
os.kill；不支持的平台同样返回 None，不假装支持。
"""

from __future__ import annotations

import os
import sys

# 身份容差（秒）：不同时钟源取到的启动时间允许的小误差
IDENTITY_TOLERANCE_SECONDS = 2.0

if sys.platform == "win32":  # pragma: no cover —— 平台分支，测试按平台择一执行
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_TERMINATE = 0x0001
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
    _EPOCH_AS_FILETIME = 11644473600.0  # 1601-01-01 → 1970-01-01（秒）

    def _filetime_to_epoch(ft: wintypes.FILETIME) -> float:
        value = (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)
        return value / 1e7 - _EPOCH_AS_FILETIME

    def _open(pid: int, access: int) -> int:
        """OpenProcess 包装；失败清掉 last error 并返回 0（调用方判定）。"""
        ctypes.set_last_error(0)
        return int(_kernel32.OpenProcess(access, False, pid))

    def process_start_time(pid: int) -> float | None:
        """Windows：GetProcessTimes 的 CreationTime（§11.3 启动时间提示）。"""
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
            ok = _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            if not ok:
                return None
            return _filetime_to_epoch(creation)
        finally:
            _kernel32.CloseHandle(handle)

    def process_alive(pid: int) -> bool | None:
        """Windows：GetExitCodeProcess 判定；仍活跃为 True。"""
        if pid <= 0:
            return None
        handle = _open(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
        if not handle:
            # 权限打不开 ≠ 进程不存在：如实返回 None 让调用方走未知分支
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
        """Windows：TerminateProcess；拿不到句柄如实返回 False。"""
        if pid <= 0:
            return False
        handle = _open(pid, _PROCESS_TERMINATE)
        if not handle:
            return False
        try:
            return bool(_kernel32.TerminateProcess(handle, 1))
        finally:
            _kernel32.CloseHandle(handle)

else:  # pragma: no cover —— POSIX 分支
    import signal
    from pathlib import Path

    def process_start_time(pid: int) -> float | None:
        """POSIX：/proc/<pid> 目录 ctime 近似进程启动时刻；无 /proc 返回 None。"""
        if pid <= 0:
            return None
        try:
            return Path(f"/proc/{pid}").stat().st_ctime
        except OSError:
            return None

    def process_alive(pid: int) -> bool | None:
        """POSIX：os.kill(pid, 0) 探测；权限不足返回 None。"""
        if pid <= 0:
            return None
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
        """POSIX：SIGTERM。"""
        if pid <= 0:
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True


def identity_matches(pid: int, recorded_start_time: float | None) -> bool | None:
    """「pid + 启动时间」双重比对（§11.3：PID 不得单独作为身份真相）。

    返回 True 表示确认是同一进程；False 表示确认不是（pid 已被复用）；
    None 表示无法确认（缺启动时间或平台不支持）。
    """
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

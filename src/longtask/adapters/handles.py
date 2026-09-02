"""外部运行句柄（SPEC §11.3）。

spawn 持久化返回：
- external_run_id:      第三方 harness 提供的稳定运行 id（subprocess 时用 pid）
- session_locator:      重新观察或发送控制的定位信息
- recovery_strategy:    守护进程重启后如何找回（reattach | poll | nonrecoverable）
- process_identity:     PID/启动时间等提示，不得单独作为身份真相
- capability_snapshot:  spawn 时实际可用的 observe/cancel/checkpoint/control 能力

「只把 subprocess.Popen 存在内存中」不符合跨守护进程重启连续性要求
（§11.3 末句）——句柄必须落库，reconcile 才能在新进程里重新绑定。

词汇收敛：recovery_strategy 用规范原文的 `reattach | poll | nonrecoverable`。
早期草稿里的 `fence_respawn` 不是策略而是「宽限后的处置动作」，由
reconciler 据策略与宽限期推导，不写进句柄。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 规范 §11.3 的三种恢复策略
RECOVERY_REATTACH = "reattach"
RECOVERY_POLL = "poll"
RECOVERY_NONRECOVERABLE = "nonrecoverable"
RECOVERY_STRATEGIES = (RECOVERY_REATTACH, RECOVERY_POLL, RECOVERY_NONRECOVERABLE)

# 外部状态「无法确认」的观察哨兵（§11.3 分支 3）。刻意不是 AttemptState 成员：
# 它不是 attempt 轴的合法状态，而是「证据不足」的声明，必须显式处理——
# 拿不到证据就走 orphan grace，不许退化成 succeeded 或 failed。
EXTERNAL_STATE_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExternalRunHandle:
    """外部运行句柄（SPEC §11.3）。"""

    external_run_id: str
    session_locator: str
    recovery_strategy: str  # reattach | poll | nonrecoverable
    capability_snapshot: dict[str, Any] = field(default_factory=dict)
    # 提示性信息（pid/启动时间）：只用于加速定位，不得单独作为身份真相
    process_identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_run_id": self.external_run_id,
            "session_locator": self.session_locator,
            "recovery_strategy": self.recovery_strategy,
            "capability_snapshot": dict(self.capability_snapshot),
            "process_identity": dict(self.process_identity),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalRunHandle:
        return cls(
            external_run_id=str(data.get("external_run_id", "")),
            session_locator=str(data.get("session_locator", "")),
            recovery_strategy=str(data.get("recovery_strategy", RECOVERY_NONRECOVERABLE)),
            capability_snapshot=dict(data.get("capability_snapshot") or {}),
            process_identity=dict(data.get("process_identity") or {}),
        )

    def is_recoverable(self) -> bool:
        """是否存在重新绑定的可能（nonrecoverable 直接进终态判定）。"""
        return self.recovery_strategy in (RECOVERY_REATTACH, RECOVERY_POLL)


def parse_legacy_session_ref(session_ref: str) -> ExternalRunHandle:
    """解析旧版字符串 session_ref（subprocess:<attempt_id>:<pid>）成 ExternalRunHandle。

    给 P3 reconcile 路径做向后兼容：老 attempt 的 session_ref 是字符串，
    解析成 handle 后按 recovery_strategy='poll' 处理（能用 pid 观察就观察，
    观察不到就是状态未知 → orphan grace）。
    """
    parts = session_ref.split(":")
    if len(parts) >= 3 and parts[0] == "subprocess":
        return ExternalRunHandle(
            external_run_id=parts[2],  # pid
            session_locator=parts[1],  # attempt_id
            recovery_strategy=RECOVERY_POLL,
            capability_snapshot={"transport": "subprocess"},
            process_identity={"pid": parts[2]},
        )
    if len(parts) >= 2 and parts[0] == "fake":
        # fake 适配器是纯内存：重启后无从找回，但可直接结算（非阻塞）
        return ExternalRunHandle(
            external_run_id=parts[1],
            session_locator=parts[1],
            recovery_strategy=RECOVERY_NONRECOVERABLE,
            capability_snapshot={"transport": "fake"},
        )
    return ExternalRunHandle(
        external_run_id=session_ref,
        session_locator=session_ref,
        recovery_strategy=RECOVERY_NONRECOVERABLE,
        capability_snapshot={},
    )


__all__ = [
    "EXTERNAL_STATE_UNKNOWN",
    "RECOVERY_NONRECOVERABLE",
    "RECOVERY_POLL",
    "RECOVERY_REATTACH",
    "RECOVERY_STRATEGIES",
    "ExternalRunHandle",
    "parse_legacy_session_ref",
]

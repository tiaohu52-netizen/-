"""外部运行句柄（SPEC §11.3）。

spawn 持久化返回四元组：
- external_run_id:      第三方 harness 提供的稳定运行 id（subprocess 时用 pid）
- session_locator:      本地定位指针（subprocess 时用 attempt_id）
- recovery_strategy:    守护进程重启后如何找回（'reattach' | 'orphan_grace' | 'fence_respawn'）
- capability_snapshot:  spawn 时的执行器 capability 摘要（用于 reconcile 校验）

实现位置：放在本独立模块而不是 subprocess_adapter 内，避免改动 base.py
与 fake_executor.py 的协议。所有 ExecutorAdapter 仍返回 str 兼容层（先
做 backward-compatible），新增 ExternalRunHandle dataclass 给将来
bridge 适配器用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalRunHandle:
    """外部运行句柄（SPEC §11.3）。"""

    external_run_id: str
    session_locator: str
    recovery_strategy: str  # reattach | orphan_grace | fence_respawn
    capability_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_run_id": self.external_run_id,
            "session_locator": self.session_locator,
            "recovery_strategy": self.recovery_strategy,
            "capability_snapshot": dict(self.capability_snapshot),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalRunHandle:
        return cls(
            external_run_id=str(data.get("external_run_id", "")),
            session_locator=str(data.get("session_locator", "")),
            recovery_strategy=str(data.get("recovery_strategy", "fence_respawn")),
            capability_snapshot=dict(data.get("capability_snapshot") or {}),
        )


def parse_legacy_session_ref(session_ref: str) -> ExternalRunHandle:
    """解析旧版字符串 session_ref（subprocess:<attempt_id>:<pid>）成 ExternalRunHandle。

    给 P3 reconcile 路径做向后兼容：老 attempt 的 session_ref 是字符串，
    解析成 handle 后按 recovery_strategy='orphan_grace' 处理（pid 还活就
    reattach；不活就 fence_respawn）。
    """
    parts = session_ref.split(":")
    if len(parts) >= 3 and parts[0] == "subprocess":
        return ExternalRunHandle(
            external_run_id=parts[2],  # pid
            session_locator=parts[1],  # attempt_id
            recovery_strategy="orphan_grace",
            capability_snapshot={"transport": "subprocess"},
        )
    return ExternalRunHandle(
        external_run_id=session_ref,
        session_locator=session_ref,
        recovery_strategy="fence_respawn",
        capability_snapshot={},
    )


__all__ = ["ExternalRunHandle", "parse_legacy_session_ref"]

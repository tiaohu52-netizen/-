"""持久层公共 dataclass（DESIGN §7、§11.3、§13.3）。

StoreConfig / StoredLease / StoredEvent / EventInput / WriteBackResult
是 SQLite 写读路径的稳定边界类型；其他模块不应定义等价结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass(slots=True)
class StoreConfig:
    """存储配置（DESIGN §3.1）。

    db_path 默认 ~/.longtask/state.db；运行时会由 caller 覆盖。
    schema_version 默认 STORE_SCHEMA_VERSION = 2。
    """

    db_path: Path
    schema_version: int = 2  # 默认值由调用方覆盖（store 层导入 STORE_SCHEMA_VERSION）


@dataclass(frozen=True, slots=True)
class StoredLease:
    """持久化租约状态（DESIGN §7）。

    generation 单调递增：每次租约变更（获取/回收/换人）+1。
    执行者的一切写回必须携带自己的 attempt_id 与 lease_generation。
    """

    contract_id: str
    holder_attempt_id: str
    generation: int
    heartbeat_at: datetime
    timeout: timedelta
    partition_id: str | None = None

    def is_alive(self, now: datetime) -> bool:
        """心跳存活判定（DESIGN §7）。纯函数，时间注入。"""
        return now <= self.heartbeat_at + self.timeout


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """持久化事件记录（DESIGN §11.3、§13.3、§7 四轴事件）。"""

    event_id: int
    contract_id: str | None
    event_type: str
    payload_json: str
    request_id: str | None
    created_at: datetime
    actor: str
    schema_version: int = 2
    goal_id: str | None = None  # P1
    attempt_id: str | None = None
    lease_generation: int | None = None
    contract_revision: int | None = None  # P1：所属修订号
    role: str | None = (
        None  # P1：executor / verifier / daemon / user / promoter / scheduler / system
    )
    payload_schema_version: int | None = None  # P1


@dataclass(frozen=True, slots=True)
class EventInput:
    """待追加的事件入参（DESIGN §11.3、§13.3）。"""

    event_type: Any  # EventType | str
    payload: dict[str, Any] = field(default_factory=dict)
    attempt_id: str | None = None
    lease_generation: int | None = None
    request_id: str | None = None
    actor: str | None = None
    contract_revision: int | None = None  # P1
    role: str | None = None  # P1
    payload_schema_version: int | None = None  # P1，默认 STORE_SCHEMA_VERSION
    goal_id: str | None = None  # P1


@dataclass(frozen=True, slots=True)
class WriteBackResult:
    """写回操作结果（DESIGN §7、§11.3）。"""

    contract_id: str
    attempt_id: str
    lease_generation: int
    event_ids: tuple[int, ...]
    revision: int | None = None


__all__ = [
    "EventInput",
    "StoreConfig",
    "StoredEvent",
    "StoredLease",
    "WriteBackResult",
]

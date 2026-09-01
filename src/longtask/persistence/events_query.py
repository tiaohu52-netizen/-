"""事件表读写（DESIGN §11.3、§13.3、§7 四轴事件列）。

从 persistence/store.py 拆出。EventType 枚举本身在 events.py（与本模块并列），
调用方通常用：

    from longtask.persistence.events import EventType
    from longtask.persistence.events_query import append_event, get_events
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from longtask.persistence.errors import StoreError
from longtask.persistence.events import EventType
from longtask.persistence.schema import STORE_SCHEMA_VERSION, format_event_type, transaction
from longtask.persistence.types import StoredEvent

# 14 个事件列在 SELECT 中的固定顺序（§13.3）：
_COLS = (
    "event_id",
    "contract_id",
    "goal_id",
    "attempt_id",
    "lease_generation",
    "contract_revision",
    "role",
    "event_type",
    "payload_json",
    "payload_schema_version",
    "request_id",
    "created_at",
    "actor",
    "schema_version",
)
_SELECT_LIST = ", ".join(_COLS)  # 预拼好（模块级常量）


def _row_to_stored_event(r: sqlite3.Row | tuple[Any, ...]) -> StoredEvent:
    """单行 → StoredEvent。处理 payload_schema_version 兜底（旧 v1 行无该列）。"""
    return StoredEvent(
        event_id=int(r[0]),
        contract_id=r[1],
        goal_id=r[2],
        attempt_id=r[3],
        lease_generation=int(r[4]) if r[4] is not None else None,
        contract_revision=int(r[5]) if r[5] is not None else None,
        role=r[6],
        event_type=r[7],
        payload_json=r[8],
        payload_schema_version=int(r[9]) if r[9] is not None else int(r[12]),
        request_id=r[10],
        created_at=datetime.fromisoformat(r[11]),
        actor=r[12],
        schema_version=int(r[13]),
    )


def append_event(
    conn: sqlite3.Connection,
    *,
    contract_id: str | None,
    event_type: EventType | str,
    payload: dict[str, Any],
    now: datetime,
    attempt_id: str | None = None,
    lease_generation: int | None = None,
    request_id: str | None = None,
    actor: str = "daemon",
    schema_version: int = STORE_SCHEMA_VERSION,
    goal_id: str | None = None,  # P1
    contract_revision: int | None = None,  # P1
    role: str | None = None,  # P1
    payload_schema_version: int | None = None,  # P1
) -> StoredEvent:
    """追加单条事件（DESIGN §11.3、§13.3、§7 四轴事件）。"""
    evt_type_str = format_event_type(event_type)
    payload_json = json.dumps(payload, ensure_ascii=False)
    created_at_str = now.isoformat()
    payload_sv = payload_schema_version if payload_schema_version is not None else schema_version
    resolved_goal_id = goal_id if goal_id is not None else contract_id

    with transaction(conn):
        cursor = conn.execute(
            """
            INSERT INTO events (
                contract_id, goal_id, attempt_id, lease_generation,
                contract_revision, role,
                event_type, payload_json, payload_schema_version,
                request_id, created_at, actor, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_id,
                resolved_goal_id,
                attempt_id,
                lease_generation,
                contract_revision,
                role,
                evt_type_str,
                payload_json,
                payload_sv,
                request_id,
                created_at_str,
                actor,
                schema_version,
            ),
        )
        if cursor.lastrowid is None:
            raise StoreError("failed to obtain lastrowid for inserted event")
        event_id = int(cursor.lastrowid)

    return StoredEvent(
        event_id=event_id,
        contract_id=contract_id,
        goal_id=resolved_goal_id,
        attempt_id=attempt_id,
        lease_generation=lease_generation,
        contract_revision=contract_revision,
        role=role,
        event_type=evt_type_str,
        payload_json=payload_json,
        payload_schema_version=payload_sv,
        request_id=request_id,
        created_at=now,
        actor=actor,
        schema_version=schema_version,
    )


def get_events(
    conn: sqlite3.Connection,
    *,
    contract_id: str | None = None,
    after_event_id: int | None = None,
    limit: int | None = None,
) -> list[StoredEvent]:
    """查询事件列表（DESIGN §11.3、§13.3、§7 四轴事件列）。"""
    query = "SELECT " + _SELECT_LIST + " FROM events WHERE 1=1"
    params: list[Any] = []
    if contract_id is not None:
        query += " AND contract_id = ?"
        params.append(contract_id)
    if after_event_id is not None:
        query += " AND event_id > ?"
        params.append(after_event_id)
    query += " ORDER BY event_id ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    return [_row_to_stored_event(r) for r in conn.execute(query, params).fetchall()]


def get_events_by_request_id(conn: sqlite3.Connection, request_id: str) -> list[StoredEvent]:
    """根据 request_id 查询已提交事件（DESIGN §11.3 幂等去重）。"""
    _query = "SELECT " + _SELECT_LIST + " FROM events WHERE request_id = ? ORDER BY event_id ASC"
    rows = conn.execute(_query, (request_id,)).fetchall()
    return [_row_to_stored_event(r) for r in rows]


__all__ = ["append_event", "get_events", "get_events_by_request_id"]

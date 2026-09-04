"""Event append and query operations for the canonical persistence API."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from lhgp.persistence.errors import StoreError
from lhgp.persistence.events import EventType
from lhgp.persistence.schema import STORE_SCHEMA_VERSION, format_event_type, transaction
from lhgp.persistence.types import StoredEvent

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
_SELECT_LIST = ", ".join(_COLS)


def _row_to_stored_event(row: sqlite3.Row | tuple[Any, ...]) -> StoredEvent:
    return StoredEvent(
        event_id=int(row[0]),
        contract_id=row[1],
        goal_id=row[2],
        attempt_id=row[3],
        lease_generation=int(row[4]) if row[4] is not None else None,
        contract_revision=int(row[5]) if row[5] is not None else None,
        role=row[6],
        event_type=row[7],
        payload_json=row[8],
        payload_schema_version=int(row[9]) if row[9] is not None else int(row[13]),
        request_id=row[10],
        created_at=datetime.fromisoformat(row[11]),
        actor=row[12],
        schema_version=int(row[13]),
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
    goal_id: str | None = None,
    contract_revision: int | None = None,
    role: str | None = None,
    payload_schema_version: int | None = None,
) -> StoredEvent:
    event_value = format_event_type(event_type)
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_version = (
        payload_schema_version if payload_schema_version is not None else schema_version
    )
    resolved_goal_id = goal_id if goal_id is not None else contract_id
    with transaction(conn):
        cursor = conn.execute(
            """INSERT INTO events (
                contract_id, goal_id, attempt_id, lease_generation, contract_revision, role,
                event_type, payload_json, payload_schema_version, request_id, created_at,
                actor, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract_id,
                resolved_goal_id,
                attempt_id,
                lease_generation,
                contract_revision,
                role,
                event_value,
                payload_json,
                payload_version,
                request_id,
                now.isoformat(),
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
        event_type=event_value,
        payload_json=payload_json,
        payload_schema_version=payload_version,
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
    query = "SELECT " + _SELECT_LIST + " FROM events WHERE 1=1"  # noqa: S608
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
    return [_row_to_stored_event(row) for row in conn.execute(query, params).fetchall()]


def get_recent_events(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    limit: int = 20,
) -> list[StoredEvent]:
    """Return newest contract events using a database-side limit."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    query = (  # noqa: S608 — _SELECT_LIST is a fixed internal column list
        "SELECT "
        + _SELECT_LIST
        + " FROM events WHERE contract_id = ? ORDER BY event_id DESC LIMIT ?"
    )
    rows = conn.execute(query, (contract_id, limit)).fetchall()
    return [_row_to_stored_event(row) for row in reversed(rows)]


def get_events_by_request_id(conn: sqlite3.Connection, request_id: str) -> list[StoredEvent]:
    query = "SELECT " + _SELECT_LIST + " FROM events WHERE request_id = ? ORDER BY event_id ASC"  # noqa: S608
    return [_row_to_stored_event(row) for row in conn.execute(query, (request_id,)).fetchall()]


__all__ = ["append_event", "get_events", "get_events_by_request_id", "get_recent_events"]

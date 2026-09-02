"""Persistence operations for ``next_decision_at`` bookkeeping."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from lhgp.persistence.events import EventType
from lhgp.persistence.events_query import append_event


def set_next_decision_at(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    when: datetime,
    now: datetime,
    reason: str,
    goal_id: str | None = None,
    contract_revision: int | None = None,
) -> bool:
    row = conn.execute(
        "SELECT next_decision_at FROM contracts WHERE contract_id = ?", (contract_id,)
    ).fetchone()
    if row is None:
        return False
    new_value = when.isoformat()
    if row[0] == new_value:
        return False
    conn.execute(
        "UPDATE contracts SET next_decision_at = ?, updated_at = ? WHERE contract_id = ?",
        (new_value, now.isoformat(), contract_id),
    )
    append_event(
        conn,
        contract_id=contract_id,
        event_type=EventType.NEXT_DECISION_AT_SET,
        payload={"at": new_value, "reason": reason},
        now=now,
        actor="daemon",
        goal_id=goal_id,
        contract_revision=contract_revision,
        role="promoter",
    )
    return True


def earliest_next_decision_at(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    states: tuple[str, ...] = ("active", "blocked"),
) -> datetime | None:
    placeholders = ", ".join("?" for _ in states)
    row = conn.execute(
        f"SELECT MIN(next_decision_at) FROM contracts WHERE state IN ({placeholders})",  # noqa: S608
        tuple(states),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    value = datetime.fromisoformat(row[0])
    return value if value > now else None


__all__ = ["earliest_next_decision_at", "set_next_decision_at"]

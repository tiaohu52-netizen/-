"""next_decision_at 落库（SPEC §9 next_decision_at、P4）。

决策点计算是 promoter 层的纯函数（tick.py）；本模块只做忠实落库：
更新 contracts.next_decision_at 列 + next-decision/set 事件，**不递增
revision、不改状态**——决策点是调度簿记，不是合同修订。

去重：值未变化时不写事件（重跑 tick 不刷屏事件流）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from longtask.persistence.events import EventType
from longtask.persistence.events_query import append_event


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
    """写入合同的下一个决策点（P4）。

    返回是否发生变化（True = 已更新并落事件；False = 值相同，幂等跳过）。
    """
    row = conn.execute(
        "SELECT next_decision_at FROM contracts WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    if row is None:
        return False
    current = row[0]
    new_value = when.isoformat()
    if current == new_value:
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
    """非终态合同里最早的决策点（主循环据此决定睡多久）。"""
    placeholders = ", ".join("?" for _ in states)
    row = conn.execute(
        f"SELECT MIN(next_decision_at) FROM contracts WHERE state IN ({placeholders})",
        tuple(states),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    value = datetime.fromisoformat(row[0])
    return value if value > now else None


__all__ = ["earliest_next_decision_at", "set_next_decision_at"]

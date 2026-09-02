"""推进簿记 helpers（DESIGN §6、§7）：attempts / decisions 表写入与预算判定。

本模块只做调度簿记（§3.3）：upsert attempts 行、追加 decisions 行、
以及从 attempts/events 派生跨 tick 预算与停滞判定。所有函数纯参数化，
连接与时间由调用方注入，可独立测试。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from longtask.persistence.events import EventType
from longtask.promoter.urgency import UrgencyTier


def _record_attempt(
    conn: sqlite3.Connection,
    *,
    goal_id: str,
    attempt_id: str,
    contract_revision: int,
    role: str,
    executor_id: str | None,
    model_id: str | None = None,
    state: str,
    admitted_at: datetime,
    started_at: datetime | None = None,
    terminal_at: datetime | None = None,
    return_code: int | None = None,
    error_class: str | None = None,
    payload: dict[str, Any] | None = None,
    updated_at: datetime,
) -> None:
    """upsert 一行 attempts（DESIGN §7、P1）。

    主键 attempt_id：同一 attempt_id 已存在则 UPDATE；首次创建则 INSERT。
    """
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO attempts (
            attempt_id, goal_id, contract_revision, role,
            executor_id, model_id, state, lease_generation, partition_id,
            admitted_at, started_at, terminal_at, return_code, error_class,
            payload_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (attempt_id) DO UPDATE SET
            state = excluded.state,
            lease_generation = excluded.lease_generation,
            model_id = COALESCE(excluded.model_id, attempts.model_id),
            started_at = COALESCE(excluded.started_at, attempts.started_at),
            terminal_at = excluded.terminal_at,
            return_code = excluded.return_code,
            error_class = excluded.error_class,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            attempt_id,
            goal_id,
            contract_revision,
            role,
            executor_id,
            model_id,
            state,
            admitted_at.isoformat(),
            started_at.isoformat() if started_at else None,
            terminal_at.isoformat() if terminal_at else None,
            return_code,
            error_class,
            payload_json,
            updated_at.isoformat(),
        ),
    )


def _record_decision(
    conn: sqlite3.Connection,
    *,
    goal_id: str,
    contract_revision: int,
    tier: UrgencyTier | None,
    decision_type: str,
    reason: str,
    budget_dispatches_left: int,
    budget_escalations_left: int,
    now: datetime,
    actor: str,
) -> None:
    """追加 decisions 行（DESIGN §6 升级历史）。"""
    tier_str = None if tier is None else int(tier)
    conn.execute(
        """
        INSERT INTO decisions (
            goal_id, contract_revision, tier, decision_type,
            reason, budget_dispatches_left, budget_escalations_left,
            payload_json, recorded_at, actor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            goal_id,
            contract_revision,
            tier_str,
            decision_type,
            reason,
            budget_dispatches_left,
            budget_escalations_left,
            now.isoformat(),
            actor,
        ),
    )


def _count_verifier_attempts(conn: sqlite3.Connection, contract_id: str) -> int:
    """attempts 表里 role='verifier' 且已 terminal 的数量（DESIGN §6 escalation_used）。"""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM attempts
        WHERE goal_id = ? AND role = 'verifier'
          AND state IN ('succeeded', 'failed', 'cancelled', 'stale', 'orphaned')
        """,
        (contract_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def _estimate_stalled_from_attempts(conn: sqlite3.Connection, contract_id: str) -> bool:
    """estimate_stalled 近似：最近两次 executor attempt 同档/高档且无 verifier 派生。

    严格定义见 DESIGN §6.2 档 4 触发：本模块给最小可观测近似
    ——同 tick 观察到连续两条 attempt/started 间隔 < budget.max_attempt_minutes
    且无 verifier 派生，即视为停滞。"""
    rows = conn.execute(
        """
        SELECT a.state, a.admitted_at, a.contract_revision, a.role
        FROM attempts a
        WHERE a.goal_id = ?
        ORDER BY a.admitted_at DESC
        LIMIT 4
        """,
        (contract_id,),
    ).fetchall()
    if len(rows) < 2:
        return False
    recent = [r for r in rows if r[3] == "executor"]
    if len(recent) < 2:
        return False
    last, prev = recent[0], recent[1]
    if last[0] != "running" and last[0] != "admitted":
        return False
    if prev[0] in ("succeeded", "cancelled"):
        return False
    # 两次 attempt 之间无 verifier 派生
    verifier_exists = conn.execute(
        "SELECT 1 FROM attempts WHERE goal_id = ? AND role = 'verifier' LIMIT 1",
        (contract_id,),
    ).fetchone()
    return verifier_exists is None


def _last_event_at(
    conn: sqlite3.Connection, contract_id: str, event_type: EventType
) -> datetime | None:
    """最近一次指定事件的发生时间（用于跨档判定与冷却）。"""
    row = conn.execute(
        """
        SELECT created_at FROM events
        WHERE contract_id = ? AND event_type = ?
        ORDER BY event_id DESC LIMIT 1
        """,
        (contract_id, event_type.value),
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row[0])


def _last_attempt_started_at(conn: sqlite3.Connection, contract_id: str) -> datetime | None:
    """最近一次 attempt/started 事件时间。"""
    return _last_event_at(conn, contract_id, EventType.ATTEMPT_STARTED)

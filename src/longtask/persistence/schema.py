"""SQLite schema 与连接/事务层（DESIGN §3.1、§7、§13.3）。

从 store.py 拆出。本模块管：
- connect() 打开 SQLite + WAL + foreign_keys
- _check_schema_version() 未来版本只读拒写
- ensure_schema() 建表 / 索引 / v1→v2 迁移
- _migrate_v1_to_v2() 原地升级列
- transaction() BEGIN IMMEDIATE 上下文
- partition / event_type 小 helper

业务表读写（contracts / leases / events / attempts / decisions /
contract_revisions）按主题拆到 contracts.py / leases.py / events.py / revisions.py
等模块，本模块不参与。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from longtask.persistence.errors import StoreError, StoreTamperedError
from longtask.persistence.types import StoreConfig

STORE_SCHEMA_VERSION = 2  # state.db schema 版本（DESIGN §13.3）；P1 升 v2


def connect(config: StoreConfig) -> sqlite3.Connection:
    """打开权威库并执行启动检查（DESIGN §13.3）。

    - WAL 模式：单用户本机下的崩溃恢复与跨进程并发。
    - schema 版本检查：高于本实现版本 → 只读并拒绝写入，不猜测迁移。
    - fail-closed：任何启动检查失败抛 StoreError，调用方不得忽略。
    """
    if not isinstance(config.db_path, Path):
        raise StoreError(f"db_path must be a Path, got {type(config.db_path).__name__}")
    conn = sqlite3.connect(config.db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema_version(conn, config.schema_version)
    return conn


def _check_schema_version(conn: sqlite3.Connection, expected: int) -> None:
    """未知未来版本只读拒写（DESIGN §13.3）。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    current: int = int(row[0]) if row else 0
    if current > expected:
        conn.close()
        raise StoreTamperedError(
            f"state.db schema version {current} is newer than supported {expected}; "
            "refusing to write (read-only mode required)"
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """初始化数据库 schema（DESIGN §3.1、§7、§13.3）。

    P1 v2：contracts / leases / events 三张 v1 既有表保留并加列；新增
    contract_revisions / attempts / decisions / idempotency 四张表。

    既有 state.db（v1）会通过 _migrate_v1_to_v2 平滑升级，不丢历史事件。
    """
    # ── v2 终态：建表 + 索引（IF NOT EXISTS 保证幂等）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contracts (
            contract_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,  -- P1：与 contract_id 同义（§7 命名迁移）
            revision INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL,  -- commitment lifecycle 轴
            deadline_status TEXT NOT NULL DEFAULT 'not_due',  -- P1：deadline 轴
            acceptance_status TEXT NOT NULL DEFAULT 'pending',  -- P1：acceptance 轴
            blocked_reason TEXT,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            deadline_at TEXT NOT NULL,
            hard_constraints_json TEXT NOT NULL,
            acceptance_json TEXT NOT NULL,
            workload_initial_hours REAL NOT NULL,
            budget_json TEXT NOT NULL,
            soft_guidance_json TEXT NOT NULL DEFAULT '{}',
            context_json TEXT NOT NULL DEFAULT '{}',
            execution_json TEXT NOT NULL DEFAULT '{}',
            client_meta_json TEXT NOT NULL DEFAULT '{}',
            authority_json TEXT NOT NULL DEFAULT '{}',  -- P2
            attention_json TEXT NOT NULL DEFAULT '{}',  -- P2
            continuity_json TEXT NOT NULL DEFAULT '{}',  -- P2
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            next_wakeup_at TEXT,
            next_decision_at TEXT,  -- P4
            schema_version INTEGER NOT NULL DEFAULT 2
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leases (
            contract_id TEXT NOT NULL,
            partition_id TEXT NOT NULL DEFAULT '',
            holder_attempt_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            heartbeat_at TEXT NOT NULL,
            timeout_seconds REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, partition_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT,
            goal_id TEXT,  -- P1：与 contract_id 同义
            attempt_id TEXT,
            lease_generation INTEGER,
            contract_revision INTEGER,  -- P1：事件所属合同修订号
            role TEXT,  -- P1：executor / verifier / daemon / user / promoter / scheduler / system
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_schema_version INTEGER NOT NULL DEFAULT 2,  -- P1：payload schema 版本
            request_id TEXT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_request_id
        ON events(request_id)
        WHERE request_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_contract_id
        ON events(contract_id, event_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_goal_id
        ON events(goal_id, event_id)
        WHERE goal_id IS NOT NULL
        """
    )
    # 注：v1→v2 迁移会 ALTER TABLE events 加 goal_id 列；若 events 表已存在但
    # 是 v1 schema，goal_id index 必须放到 _migrate_v1_to_v2 之后再建。

    # ── P1：合同修订不可变表（替换 v1 就地 CAS UPDATE）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_revisions (
            contract_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            state TEXT NOT NULL,
            deadline_status TEXT NOT NULL,
            acceptance_status TEXT NOT NULL,
            blocked_reason TEXT,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            deadline_at TEXT NOT NULL,
            hard_constraints_json TEXT NOT NULL,
            acceptance_json TEXT NOT NULL,
            workload_initial_hours REAL NOT NULL,
            budget_json TEXT NOT NULL,
            soft_guidance_json TEXT NOT NULL DEFAULT '{}',
            context_json TEXT NOT NULL DEFAULT '{}',
            execution_json TEXT NOT NULL DEFAULT '{}',
            client_meta_json TEXT NOT NULL DEFAULT '{}',
            authority_json TEXT NOT NULL DEFAULT '{}',
            attention_json TEXT NOT NULL DEFAULT '{}',
            continuity_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL,
            change_reason TEXT,
            PRIMARY KEY (contract_id, revision)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contract_revisions_recorded
        ON contract_revisions(contract_id, revision DESC)
        """
    )

    # ── P1：attempts 实体表（§7 attempt 轴；C1 修复用：实际判断 verifier 是否派生）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            role TEXT NOT NULL,  -- executor | verifier
            executor_id TEXT,
            state TEXT NOT NULL,  -- attempt state axis (§7)
            lease_generation INTEGER,
            partition_id TEXT,
            admitted_at TEXT NOT NULL,
            started_at TEXT,
            terminal_at TEXT,
            return_code INTEGER,
            error_class TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            -- P3：持久外部句柄（SPEC §11.3）——外部 run 身份与恢复策略
            external_run_id TEXT,
            session_locator TEXT,
            recovery_strategy TEXT,  -- reattach | poll | nonrecoverable
            process_identity_json TEXT,  -- 提示，不得单独作为身份真相
            capability_snapshot_json TEXT,
            handle_registered_at TEXT,
            orphaned_at TEXT  -- 进入 orphan grace 的起点（§11.3 分支 3）
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_goal
        ON attempts(goal_id, admitted_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_role_state
        ON attempts(role, state)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_orphaned
        ON attempts(state)
        WHERE state = 'orphaned'
        """
    )

    # ── P1：decisions 实体表（§6 escalation 轴历史）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            tier TEXT,  -- urgency tier；None 表示 deadline-arbiter
            decision_type TEXT NOT NULL,  -- see DESIGN §6
            reason TEXT NOT NULL,
            budget_dispatches_left INTEGER,
            budget_escalations_left INTEGER,
            payload_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL,
            actor TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_decisions_goal_time
        ON decisions(goal_id, recorded_at)
        """
    )

    # ── P1：idempotency 实体表（§11.3 重放去重）──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency (
            request_id TEXT PRIMARY KEY,
            goal_id TEXT,
            response_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )

    # ── 迁移：v1 → v2 ──
    _migrate_v1_to_v2(conn)

    conn.execute(f"PRAGMA user_version={STORE_SCHEMA_VERSION}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2 原地迁移（DESIGN §13.3 演化纪律）。

    - 给既有 contracts 表加 goal_id / deadline_status / acceptance_status / next_decision_at /
      authority_json / attention_json / continuity_json 列（缺省值兜底）。
    - 给既有 events 表加 goal_id / contract_revision / role / payload_schema_version 列。
    - 给既有 contracts 行回填 goal_id = contract_id（§7 命名迁移）。
    - 给既有 contracts 行回填 next_wakeup_at（v1 已有，直接保留）。
    - 不重建既有事件数据。
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    current = int(row[0]) if row else 0
    if current >= 2:
        return

    # contracts：缺啥补啥（IF NOT EXISTS 风格靠 suppress(OperationalError)）
    def _add_column_if_missing(table: str, col_def: str) -> None:
        with suppress(sqlite3.OperationalError):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

    _add_column_if_missing("contracts", "goal_id TEXT")
    _add_column_if_missing("contracts", "deadline_status TEXT NOT NULL DEFAULT 'not_due'")
    # 早期预发布版本曾写入不存在的 on_track 枚举；迁移时统一到协议
    # 当前语义的 not_due，避免旧库在读取 ContractView 时崩溃。
    conn.execute(
        "UPDATE contracts SET deadline_status = 'not_due' WHERE deadline_status = 'on_track'"
    )
    _add_column_if_missing("contracts", "acceptance_status TEXT NOT NULL DEFAULT 'pending'")
    _add_column_if_missing("contracts", "authority_json TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing("contracts", "attention_json TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing("contracts", "continuity_json TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing("contracts", "next_decision_at TEXT")

    _add_column_if_missing("events", "goal_id TEXT")
    _add_column_if_missing("events", "contract_revision INTEGER")
    _add_column_if_missing("events", "role TEXT")
    _add_column_if_missing("events", "payload_schema_version INTEGER NOT NULL DEFAULT 1")

    # P3：attempts 表外部句柄列（SPEC §11.3）——v2 早期 attempts 表无这些列
    _add_column_if_missing("attempts", "external_run_id TEXT")
    _add_column_if_missing("attempts", "session_locator TEXT")
    _add_column_if_missing("attempts", "recovery_strategy TEXT")
    _add_column_if_missing("attempts", "process_identity_json TEXT")
    _add_column_if_missing("attempts", "handle_registered_at TEXT")
    _add_column_if_missing("attempts", "orphaned_at TEXT")

    # 回填 goal_id
    conn.execute("UPDATE contracts SET goal_id = contract_id WHERE goal_id IS NULL OR goal_id = ''")
    conn.execute("UPDATE events SET goal_id = contract_id WHERE goal_id IS NULL")
    conn.execute(
        "UPDATE events SET payload_schema_version = schema_version "
        "WHERE payload_schema_version IS NULL"
    )

    # 合同状态名迁移：complete → satisfied（acceptance_status 轴的终态），
    # 但只在 v1 行明确为 complete 时改；其它状态保留。
    conn.execute("UPDATE contracts SET state = 'satisfied' WHERE state = 'complete'")
    # expired 状态保留作 commitment lifecycle 中的非终态；deadline_status 由
    # 应用层据 deadline_at 派生为 past_deadline。


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """进入 BEGIN IMMEDIATE 事务，异常回滚，退出提交（DESIGN §3.1、§13.3）。

    保证单事务原子性，防止并发写写冲突（SQLite WAL 下 BEGIN IMMEDIATE 立即获取写锁）。
    支持嵌套：如果连接已在事务中，内部上下文复用外层事务，不提前提交或回滚。
    """
    in_trans = conn.in_transaction
    if not in_trans:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        if not in_trans:
            conn.commit()
    except Exception:
        if not in_trans:
            conn.rollback()
        raise


def partition_key(partition_id: str | None) -> str:
    """partition_id → DB 内存储键（DESIGN §7.1：'' 表示全局分区）。"""
    return partition_id or ""


def parse_partition_key(stored: str) -> str | None:
    """DB 内存储键 → 逻辑 partition_id（DESIGN §7.1）。"""
    return stored or None


def format_event_type(event_type: object) -> str:
    """EventType | str → 字符串值（DESIGN §11.3 事件词汇稳定）。"""
    return event_type.value if hasattr(event_type, "value") else str(event_type)


__all__ = [
    "STORE_SCHEMA_VERSION",
    "connect",
    "ensure_schema",
    "format_event_type",
    "parse_partition_key",
    "partition_key",
    "transaction",
]

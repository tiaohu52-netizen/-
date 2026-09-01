"""Contract 视图与四轴状态枚举（SPEC §5、§7）。

P2 起独立模块。所有 StrEnum 字面值保持 SPEC §5/§7/§11.7 原文；如需新增须
先在 LHGP-SPEC.md §7 增补权威段，否则视为虚假引用。
"""

from __future__ import annotations

from enum import StrEnum


class ContractState(StrEnum):
    """commitment lifecycle 轴（DESIGN §5、SPEC §7.1）。"""

    DRAFTED = "drafted"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETE = "complete"  # legacy: 等同 satisfied；保留读端兼容
    SATISFIED = "satisfied"  # P1: acceptance 全通过后的终态
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # 仍作为 commitment lifecycle 终态字面值
    ARCHIVED = "archived"


class DeadlineStatus(StrEnum):
    """deadline_status 轴（SPEC §7.2）。"""

    NOT_DUE = "not_due"  # P2 起：替代 v0.7 on_track 字面值
    AT_RISK = "at_risk"
    MET = "met"
    MISSED = "missed"
    WAIVED = "waived"


class AcceptanceStatus(StrEnum):
    """acceptance_status 轴（SPEC §7.3）。"""

    PENDING = "pending"
    CANDIDATE = "candidate"  # P2 起：candidate → verifying → passed|failed|undetermined
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    UNDETERMINED = "undetermined"
    NOT_REQUIRED = "not_required"


class BlockReason(StrEnum):
    """blocked 原因码（DESIGN §5、SPEC §10.5）。

    SPEC §11.7 的 RPC 错误码另有枚举（rpc/errors.py），不在此复用。
    """

    NEED_USER = "need-user"
    LEASE_DEAD = "lease-dead"
    BUDGET_EXHAUSTED = "budget-exhausted"
    CONSTRAINT_REFUSED = "constraint-refused"
    NO_EXECUTOR = "no-executor"
    ACCEPTANCE_FAILED = "acceptance-failed"
    DEADLINE_MISSED = "deadline-missed"
    NEED_ARBITRATION = "need-arbitration"


class AttemptRole(StrEnum):
    """attempt 角色（DESIGN §5.2、SPEC §7.4）。"""

    EXECUTOR = "executor"
    VERIFIER = "verifier"
    PLANNER = "planner"  # P2 起：与 §6.1 authority.executors[].roles 对齐


class AttemptState(StrEnum):
    """attempt 轴（SPEC §7.4）。"""

    ADMITTED = "admitted"
    STARTING = "starting"  # P2 起：与 §7.4 对齐
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    ORPHANED = "orphaned"


class Enforcement(StrEnum):
    """约束兑现能力等级（DESIGN §12.4 manifest）。"""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"
    UNSUPPORTED = "unsupported"


class EventActor(StrEnum):
    """事件 actor 枚举（DESIGN §11.7，P1）。"""

    USER = "user"
    DAEMON = "daemon"
    PROMOTER = "promoter"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


# SPEC §4 冻结区字段：创建后不可修改；P2 增 acceptance.standard（按 §6.4 仅
# 软指引/通知偏好/计划策略可普通修订，其余走 Principal 批准）。
FROZEN_FIELDS: frozenset[str] = frozenset(
    {"objective", "deadline_at", "hard_constraints", "authority"}
)


def to_state_dict(state: ContractState) -> str:
    return state.value


def from_state_dict(value: str) -> ContractState:
    return ContractState(value)

"""ContractView 实体类（SPEC §11.6 + §7 四轴）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from longtask.contracts.contract_draft import SCHEMA_VERSION, ContractDraft
from longtask.contracts.contract_view import (
    AcceptanceStatus,
    BlockReason,
    ContractState,
    DeadlineStatus,
)


@dataclass(frozen=True, slots=True)
class ContractView:
    """服务端返回的合同视图（SPEC §11.6 + §7）。"""

    draft: ContractDraft
    contract_id: str
    goal_id: str  # P1：与 contract_id 同义
    revision: int
    state: ContractState
    deadline_status: DeadlineStatus
    acceptance_status: AcceptanceStatus
    created_at: datetime
    updated_at: datetime
    next_wakeup_at: datetime | None
    next_decision_at: datetime | None
    blocked_reason: BlockReason | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为线协议字典格式（SPEC §11.6、§7）。"""
        return {
            "schema_version": SCHEMA_VERSION,
            "contract_id": self.contract_id,
            "goal_id": self.goal_id,
            "revision": self.revision,
            "state": self.state.value,
            "deadline_status": self.deadline_status.value,
            "acceptance_status": self.acceptance_status.value,
            "blocked_reason": self.blocked_reason.value if self.blocked_reason else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "next_wakeup_at": self.next_wakeup_at.isoformat() if self.next_wakeup_at else None,
            "next_decision_at": (
                self.next_decision_at.isoformat() if self.next_decision_at else None
            ),
            **self.draft.to_dict(),
        }

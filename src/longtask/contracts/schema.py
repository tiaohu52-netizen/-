"""contracts 包兼容层（re-export）。

P2 起把原 schema.py 拆为：
- acceptance / authority / attention / continuity / budget（字段子组）
- contract_draft / contract_view_entity（顶层 dataclass）
- contract_view（StrEnum 集中地）
- validation（单一 runtime validator）
- state_machine（四轴判定函数）

本文件保留所有旧名字以便既有导入（cli/main.py, rpc/handlers.py,
persistence/store.py 等）继续工作；不再定义新内容。新代码请直接 import
具体子模块。
"""

from __future__ import annotations

# 字段子组
from longtask.contracts.acceptance import (
    VALID_VERIFIER_KINDS,
    Acceptance,
)
from longtask.contracts.attention import Attention, QuietHours
from longtask.contracts.authority import (
    ALLOWED_CONTROLS,
    Authority,
    AuthorityBinding,
)
from longtask.contracts.budget import Budget
from longtask.contracts.continuity import Continuity

# 顶层 dataclass
from longtask.contracts.contract_draft import SCHEMA_VERSION, ContractDraft

# 状态枚举
from longtask.contracts.contract_view import (
    FROZEN_FIELDS,
    AcceptanceStatus,
    AttemptRole,
    AttemptState,
    BlockReason,
    ContractState,
    DeadlineStatus,
    Enforcement,
    EventActor,
)

# 视图
from longtask.contracts.contract_view_entity import ContractView

# state_machine 重导出（避免破坏既有 from longtask.contracts.schema import ... 路径）
from longtask.contracts.state_machine import (
    LEGAL_TRANSITIONS,
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    is_terminal_state,
    is_valid_acceptance_transition,
    is_valid_deadline_transition,
    is_valid_transition,
)

# 单一 validator
from longtask.contracts.validation import validate_draft, validate_raw

__all__ = [
    "ALLOWED_CONTROLS",
    "FROZEN_FIELDS",
    "LEGAL_TRANSITIONS",
    "NON_TERMINAL_STATES",
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "VALID_VERIFIER_KINDS",
    "Acceptance",
    "AcceptanceStatus",
    "AttemptRole",
    "AttemptState",
    "Attention",
    "Authority",
    "AuthorityBinding",
    "BlockReason",
    "Budget",
    "Continuity",
    "ContractDraft",
    "ContractState",
    "ContractView",
    "DeadlineStatus",
    "Enforcement",
    "EventActor",
    "QuietHours",
    "is_terminal_state",
    "is_valid_acceptance_transition",
    "is_valid_deadline_transition",
    "is_valid_transition",
    "validate_draft",
    "validate_raw",
]

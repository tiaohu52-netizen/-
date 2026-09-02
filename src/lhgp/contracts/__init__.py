"""Canonical LHGP contract namespace.

These modules are compatibility facades over the single ``longtask.contracts``
implementation.  Keeping one implementation prevents version or state forks
while callers migrate imports incrementally.
"""

from lhgp.contracts.acceptance import VALID_VERIFIER_KINDS, Acceptance
from lhgp.contracts.attention import VALID_NOTIFY_ON, Attention, QuietHours
from lhgp.contracts.authority import ALLOWED_CONTROLS, Authority, AuthorityBinding
from lhgp.contracts.budget import DEFAULT_VERIFICATION_RESERVED, Budget
from lhgp.contracts.continuity import Continuity
from lhgp.contracts.contract_view import (
    FROZEN_FIELDS,
    AcceptanceStatus,
    AttemptRole,
    AttemptState,
    BlockReason,
    ContractState,
    DeadlineStatus,
    Enforcement,
    EventActor,
    from_state_dict,
    to_state_dict,
)

__all__ = [
    "ALLOWED_CONTROLS",
    "DEFAULT_VERIFICATION_RESERVED",
    "FROZEN_FIELDS",
    "VALID_NOTIFY_ON",
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
    "ContractState",
    "DeadlineStatus",
    "Enforcement",
    "EventActor",
    "QuietHours",
    "from_state_dict",
    "to_state_dict",
]

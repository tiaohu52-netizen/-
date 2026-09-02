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

__all__ = [
    "ALLOWED_CONTROLS",
    "DEFAULT_VERIFICATION_RESERVED",
    "VALID_NOTIFY_ON",
    "VALID_VERIFIER_KINDS",
    "Acceptance",
    "Attention",
    "Authority",
    "AuthorityBinding",
    "Budget",
    "Continuity",
    "QuietHours",
]

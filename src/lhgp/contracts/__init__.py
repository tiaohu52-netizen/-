"""Canonical LHGP contract namespace.

These modules are compatibility facades over the single ``longtask.contracts``
implementation.  Keeping one implementation prevents version or state forks
while callers migrate imports incrementally.
"""

from lhgp.contracts.budget import DEFAULT_VERIFICATION_RESERVED, Budget

__all__ = ["DEFAULT_VERIFICATION_RESERVED", "Budget"]

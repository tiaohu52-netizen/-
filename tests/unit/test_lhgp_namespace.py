"""Canonical LHGP Python namespace compatibility tests (P6)."""

import longtask
from lhgp import PROTOCOL_VERSION, __version__
from lhgp.contracts.contract_draft import ContractDraft as CanonicalContractDraft
from longtask.contracts.contract_draft import ContractDraft as LegacyContractDraft


def test_canonical_namespace_matches_legacy_runtime_identity() -> None:
    """The new namespace must not fork protocol or package version state."""

    assert PROTOCOL_VERSION == longtask.PROTOCOL_VERSION
    assert __version__ == longtask.__version__


def test_contract_namespace_reexports_single_implementation() -> None:
    """Contract facades must preserve class identity during migration."""

    assert CanonicalContractDraft is LegacyContractDraft

"""Canonical LHGP Python namespace compatibility tests (P6)."""

import longtask
from lhgp import PROTOCOL_VERSION, __version__
from lhgp.contracts.contract_draft import ContractDraft as CanonicalContractDraft
from lhgp.persistence.store import connect as canonical_connect
from longtask.contracts.contract_draft import ContractDraft as LegacyContractDraft
from longtask.persistence.store import connect as legacy_connect


def test_canonical_namespace_matches_legacy_runtime_identity() -> None:
    """The new namespace must not fork protocol or package version state."""

    assert PROTOCOL_VERSION == longtask.PROTOCOL_VERSION
    assert __version__ == longtask.__version__


def test_contract_namespace_reexports_single_implementation() -> None:
    """Contract facades must preserve class identity during migration."""

    assert CanonicalContractDraft is LegacyContractDraft


def test_persistence_namespace_reexports_single_implementation() -> None:
    """Persistence facades must preserve callable identity during migration."""

    assert canonical_connect is legacy_connect

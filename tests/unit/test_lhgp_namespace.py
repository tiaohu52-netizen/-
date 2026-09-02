"""Canonical LHGP Python namespace compatibility tests (P6)."""

import subprocess
import sys

import longtask
from lhgp import PROTOCOL_VERSION, __version__
from lhgp.acceptance import CheckSpec as CanonicalPackageCheckSpec
from lhgp.acceptance.checks import CheckSpec as CanonicalCheckSpec
from lhgp.adapters.registry import ExecutorRegistry as CanonicalExecutorRegistry
from lhgp.admission import (
    AdmissionRefuseCode as CanonicalRefuseCode,
)
from lhgp.admission import (
    AdmissionRefusedError as CanonicalRefusedError,
)
from lhgp.admission import (
    Offer as CanonicalPackageOffer,
)
from lhgp.admission.offer import Offer as CanonicalOffer
from lhgp.admission.refuse import (
    AdmissionRefuseCode as CanonicalModuleRefuseCode,
)
from lhgp.admission.refuse import (
    AdmissionRefusedError as CanonicalModuleRefusedError,
)
from lhgp.cli.formatting import format_eta as canonical_format_eta
from lhgp.contracts import Acceptance as CanonicalPackageAcceptance
from lhgp.contracts import Attention as CanonicalPackageAttention
from lhgp.contracts import Budget as CanonicalPackageBudget
from lhgp.contracts import Continuity as CanonicalPackageContinuity
from lhgp.contracts import ContractDraft as CanonicalPackageContractDraft
from lhgp.contracts.acceptance import Acceptance as CanonicalAcceptance
from lhgp.contracts.attention import Attention as CanonicalAttention
from lhgp.contracts.authority import Authority as CanonicalAuthority
from lhgp.contracts.authority import AuthorityBinding as CanonicalAuthorityBinding
from lhgp.contracts.budget import Budget as CanonicalBudget
from lhgp.contracts.continuity import Continuity as CanonicalContinuity
from lhgp.contracts.contract_draft import ContractDraft as CanonicalContractDraft
from lhgp.contracts.contract_view import (
    AcceptanceStatus as CanonicalAcceptanceStatus,
)
from lhgp.contracts.contract_view import (
    AttemptRole as CanonicalAttemptRole,
)
from lhgp.contracts.contract_view import (
    AttemptState as CanonicalAttemptState,
)
from lhgp.contracts.contract_view import (
    BlockReason as CanonicalBlockReason,
)
from lhgp.contracts.contract_view import (
    ContractState as CanonicalContractState,
)
from lhgp.contracts.contract_view import (
    DeadlineStatus as CanonicalDeadlineStatus,
)
from lhgp.contracts.contract_view import (
    Enforcement as CanonicalEnforcement,
)
from lhgp.contracts.contract_view import (
    EventActor as CanonicalEventActor,
)
from lhgp.contracts.contract_view_entity import ContractView as CanonicalContractView
from lhgp.contracts.schema import ContractDraft as CanonicalSchemaContractDraft
from lhgp.contracts.state_machine import (
    is_valid_transition as canonical_is_valid_transition,
)
from lhgp.contracts.validation import validate_draft as canonical_validate_draft
from lhgp.forecast import Forecast as CanonicalPackageForecast
from lhgp.forecast.model import Forecast as CanonicalForecast
from lhgp.persistence.errors import StoreError as CanonicalStoreError
from lhgp.persistence.events import EventType as CanonicalEventType
from lhgp.persistence.events_query import append_event as canonical_append_event
from lhgp.persistence.store import connect as canonical_connect
from lhgp.persistence.types import StoredLease as CanonicalStoredLease
from lhgp.promoter.urgency import classify as canonical_classify
from lhgp.rpc.errors import ErrorCode as CanonicalErrorCode
from lhgp.rpc.methods import Method as CanonicalMethod
from lhgp.scheduler.wakeup import guard_needed as canonical_guard_needed
from longtask.acceptance.checks import CheckSpec as LegacyCheckSpec
from longtask.adapters.registry import ExecutorRegistry as LegacyExecutorRegistry
from longtask.admission.offer import Offer as LegacyOffer
from longtask.admission.refuse import (
    AdmissionRefuseCode as LegacyRefuseCode,
)
from longtask.admission.refuse import (
    AdmissionRefusedError as LegacyRefusedError,
)
from longtask.cli.formatting import format_eta as legacy_format_eta
from longtask.contracts.acceptance import Acceptance as LegacyAcceptance
from longtask.contracts.attention import Attention as LegacyAttention
from longtask.contracts.authority import Authority as LegacyAuthority
from longtask.contracts.authority import AuthorityBinding as LegacyAuthorityBinding
from longtask.contracts.budget import Budget as LegacyBudget
from longtask.contracts.continuity import Continuity as LegacyContinuity
from longtask.contracts.contract_draft import ContractDraft as LegacyContractDraft
from longtask.contracts.contract_view import (
    AcceptanceStatus as LegacyAcceptanceStatus,
)
from longtask.contracts.contract_view import (
    AttemptRole as LegacyAttemptRole,
)
from longtask.contracts.contract_view import (
    AttemptState as LegacyAttemptState,
)
from longtask.contracts.contract_view import (
    BlockReason as LegacyBlockReason,
)
from longtask.contracts.contract_view import (
    ContractState as LegacyContractState,
)
from longtask.contracts.contract_view import (
    DeadlineStatus as LegacyDeadlineStatus,
)
from longtask.contracts.contract_view import (
    Enforcement as LegacyEnforcement,
)
from longtask.contracts.contract_view import (
    EventActor as LegacyEventActor,
)
from longtask.contracts.contract_view_entity import ContractView as LegacyContractView
from longtask.contracts.schema import ContractDraft as LegacySchemaContractDraft
from longtask.contracts.state_machine import is_valid_transition as legacy_is_valid_transition
from longtask.contracts.validation import validate_draft as legacy_validate_draft
from longtask.forecast.model import Forecast as LegacyForecast
from longtask.persistence.errors import StoreError as LegacyStoreError
from longtask.persistence.events import EventType as LegacyEventType
from longtask.persistence.events_query import append_event as legacy_append_event
from longtask.persistence.store import connect as legacy_connect
from longtask.persistence.types import StoredLease as LegacyStoredLease
from longtask.promoter.urgency import classify as legacy_classify
from longtask.rpc.errors import ErrorCode as LegacyErrorCode
from longtask.rpc.methods import Method as LegacyMethod
from longtask.scheduler.wakeup import guard_needed as legacy_guard_needed


def test_canonical_namespace_matches_legacy_runtime_identity() -> None:
    """The new namespace must not fork protocol or package version state."""

    assert PROTOCOL_VERSION == longtask.PROTOCOL_VERSION
    assert __version__ == longtask.__version__


def test_contract_namespace_reexports_single_implementation() -> None:
    """Contract facades must preserve class identity during migration."""

    assert CanonicalContractDraft is LegacyContractDraft
    assert CanonicalPackageContractDraft is CanonicalContractDraft
    assert CanonicalBudget is LegacyBudget
    assert CanonicalPackageBudget is CanonicalBudget
    assert CanonicalAcceptance is LegacyAcceptance
    assert CanonicalPackageAcceptance is CanonicalAcceptance
    assert CanonicalAttention is LegacyAttention
    assert CanonicalPackageAttention is CanonicalAttention
    assert CanonicalAuthority is LegacyAuthority
    assert CanonicalAuthorityBinding is LegacyAuthorityBinding
    assert CanonicalContinuity is LegacyContinuity
    assert CanonicalPackageContinuity is CanonicalContinuity
    assert CanonicalContractState is LegacyContractState
    assert CanonicalDeadlineStatus is LegacyDeadlineStatus
    assert CanonicalAcceptanceStatus is LegacyAcceptanceStatus
    assert CanonicalBlockReason is LegacyBlockReason
    assert CanonicalAttemptRole is LegacyAttemptRole
    assert CanonicalAttemptState is LegacyAttemptState
    assert CanonicalEnforcement is LegacyEnforcement
    assert CanonicalEventActor is LegacyEventActor
    assert CanonicalContractView is LegacyContractView
    assert canonical_is_valid_transition is legacy_is_valid_transition
    assert canonical_validate_draft is legacy_validate_draft
    assert CanonicalSchemaContractDraft is LegacySchemaContractDraft is CanonicalContractDraft


def test_persistence_namespace_reexports_single_implementation() -> None:
    """Persistence facades must preserve callable identity during migration."""

    assert canonical_connect is legacy_connect
    assert CanonicalStoredLease is LegacyStoredLease
    assert CanonicalEventType is LegacyEventType
    assert canonical_append_event is legacy_append_event
    assert CanonicalStoreError is LegacyStoreError


def test_adapter_namespace_reexports_single_implementation() -> None:
    """Adapter facades must preserve registry class identity during migration."""

    assert CanonicalExecutorRegistry is LegacyExecutorRegistry


def test_rpc_namespace_reexports_protocol_types() -> None:
    """RPC facades must preserve enum identity during migration."""

    assert CanonicalMethod is LegacyMethod
    assert CanonicalErrorCode is LegacyErrorCode


def test_scheduler_and_promoter_namespaces_reexport_functions() -> None:
    """Scheduling facades must preserve pure-function identity."""

    assert canonical_guard_needed is legacy_guard_needed
    assert canonical_classify is legacy_classify


def test_cli_namespace_reexports_pure_helpers() -> None:
    """CLI facades must preserve helper identity without changing entrypoints."""

    assert canonical_format_eta is legacy_format_eta


def test_supporting_namespaces_reexport_single_implementation() -> None:
    """Acceptance, admission and forecast facades preserve class identity."""

    assert CanonicalCheckSpec is LegacyCheckSpec
    assert CanonicalPackageCheckSpec is CanonicalCheckSpec
    assert CanonicalOffer is LegacyOffer
    assert CanonicalPackageOffer is CanonicalOffer
    assert CanonicalRefuseCode is CanonicalModuleRefuseCode is LegacyRefuseCode
    assert CanonicalRefusedError is CanonicalModuleRefusedError is LegacyRefusedError
    assert CanonicalRefuseCode.POLICY_DENY.value == "policy-deny"
    assert CanonicalForecast is LegacyForecast
    assert CanonicalPackageForecast is CanonicalForecast


def test_canonical_modules_preserve_module_execution_entrypoints() -> None:
    """Canonical module paths must behave like their installed console scripts."""

    cli = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "lhgp.cli.main", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert cli.stdout.strip().startswith("lhgp ")

    mcp = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "lhgp.mcp_server", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "LHGP" in mcp.stdout


def test_legacy_cli_alias_emits_deprecation_warning(monkeypatch, capsys) -> None:
    """Legacy executable names remain usable but visibly announce migration."""

    from longtask.cli.main import main

    monkeypatch.setattr(sys, "argv", ["longtask"])
    assert main(["--version"]) == 0
    assert "deprecated" in capsys.readouterr().err

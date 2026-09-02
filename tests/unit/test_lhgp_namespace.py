"""Canonical LHGP Python namespace compatibility tests (P6)."""

import subprocess
import sys

import longtask
from lhgp import PROTOCOL_VERSION, __version__
from lhgp.acceptance import CheckSpec as CanonicalPackageCheckSpec
from lhgp.acceptance.checks import CheckSpec as CanonicalCheckSpec
from lhgp.adapters.registry import ExecutorRegistry as CanonicalExecutorRegistry
from lhgp.admission.offer import Offer as CanonicalOffer
from lhgp.cli.formatting import format_eta as canonical_format_eta
from lhgp.contracts.contract_draft import ContractDraft as CanonicalContractDraft
from lhgp.forecast import Forecast as CanonicalPackageForecast
from lhgp.forecast.model import Forecast as CanonicalForecast
from lhgp.persistence.store import connect as canonical_connect
from lhgp.promoter.urgency import classify as canonical_classify
from lhgp.rpc.errors import ErrorCode as CanonicalErrorCode
from lhgp.rpc.methods import Method as CanonicalMethod
from lhgp.scheduler.wakeup import guard_needed as canonical_guard_needed
from longtask.acceptance.checks import CheckSpec as LegacyCheckSpec
from longtask.adapters.registry import ExecutorRegistry as LegacyExecutorRegistry
from longtask.admission.offer import Offer as LegacyOffer
from longtask.cli.formatting import format_eta as legacy_format_eta
from longtask.contracts.contract_draft import ContractDraft as LegacyContractDraft
from longtask.forecast.model import Forecast as LegacyForecast
from longtask.persistence.store import connect as legacy_connect
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


def test_persistence_namespace_reexports_single_implementation() -> None:
    """Persistence facades must preserve callable identity during migration."""

    assert canonical_connect is legacy_connect


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

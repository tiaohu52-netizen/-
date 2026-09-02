"""Regression tests for release artifact metadata validation."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from scripts.check_artifacts import _STRICT_SEMVER, _validate_wheel_entrypoints


@pytest.mark.unit
@pytest.mark.parametrize(
    "version",
    ("0.1.0", "1.2.3-alpha.1", "2.0.0+build.7", "1.2.3-rc.1+windows"),
)
def test_accepts_semver_versions(version: str) -> None:
    assert _STRICT_SEMVER.fullmatch(version)


@pytest.mark.unit
@pytest.mark.parametrize("version", ("0.1.0a0", "v1.2.3", "1.2", "01.2.3"))
def test_rejects_non_semver_versions(version: str) -> None:
    assert _STRICT_SEMVER.fullmatch(version) is None


@pytest.mark.unit
def test_wheel_requires_canonical_entrypoints(tmp_path: Path) -> None:
    wheel = tmp_path / "demo.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "demo-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\n"
            "lhgp = lhgp.cli.main:entrypoint\n"
            "lhgpd = lhgp.cli.daemon_proc:lhgpd_entrypoint\n"
            "lhgp-mcp = lhgp.mcp_server:main\n",
        )
    assert _validate_wheel_entrypoints(wheel) is None


@pytest.mark.unit
def test_wheel_reports_missing_canonical_entrypoint(tmp_path: Path) -> None:
    wheel = tmp_path / "demo.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "demo-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nlhgp = lhgp.cli.main:entrypoint\n",
        )
    error = _validate_wheel_entrypoints(wheel)
    assert error is not None
    assert "lhgpd" in error

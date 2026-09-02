"""Regression tests for release artifact metadata validation."""

from __future__ import annotations

import pytest
from scripts.check_artifacts import _STRICT_SEMVER


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

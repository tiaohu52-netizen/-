"""Compatibility facade for the canonical :mod:`lhgp.acceptance.checks`."""

from lhgp.acceptance.checks import CheckKind, CheckSpec, RepairBrief, parse_check

__all__ = ["CheckKind", "CheckSpec", "RepairBrief", "parse_check"]

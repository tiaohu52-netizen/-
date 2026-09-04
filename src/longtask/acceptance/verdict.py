"""Compatibility facade for :mod:`lhgp.acceptance.verdict`."""

from lhgp.acceptance.verdict import (
    VERDICT_MARKER,
    ModelVerdict,
    merge_evidence,
    parse_verdict_block,
)

__all__ = ["VERDICT_MARKER", "ModelVerdict", "merge_evidence", "parse_verdict_block"]

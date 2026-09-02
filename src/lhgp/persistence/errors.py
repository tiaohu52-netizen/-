"""Canonical persistence error hierarchy."""

from __future__ import annotations


class StoreError(Exception):
    """Base class for persistence failures."""


class StoreTamperedError(StoreError):
    """The store was externally modified or uses an unsupported schema."""


class LeaseCASError(StoreError):
    """A lease compare-and-swap expectation failed."""


LeaseConflictError = LeaseCASError


class LeaseFencedError(StoreError):
    """A write-back used an expired lease or mismatched attempt."""


class RevisionConflictError(StoreError):
    """A contract revision compare-and-swap failed."""


class IdempotencyMismatchError(StoreError):
    """A request id was replayed with different input."""


__all__ = [
    "IdempotencyMismatchError",
    "LeaseCASError",
    "LeaseConflictError",
    "LeaseFencedError",
    "RevisionConflictError",
    "StoreError",
    "StoreTamperedError",
]

"""Compatibility facade for :mod:`lhgp.adapters.processes`."""

from lhgp.adapters.processes import (
    IDENTITY_TOLERANCE_SECONDS,
    identity_matches,
    process_alive,
    process_start_time,
    terminate_pid,
)

__all__ = [
    "IDENTITY_TOLERANCE_SECONDS",
    "identity_matches",
    "process_alive",
    "process_start_time",
    "terminate_pid",
]

"""Canonical persistence boundary for the LHGP state store.

Only this package should be used by integrations that need the authoritative
SQLite connection, schema bootstrap, and event vocabulary.
"""

__all__ = [
    "STORE_SCHEMA_VERSION",
    "EventInput",
    "EventType",
    "StoreConfig",
    "StoredEvent",
    "StoredLease",
    "WriteBackResult",
    "connect",
    "ensure_schema",
    "get_events",
    "transaction",
]


def __getattr__(name: str) -> object:
    """Resolve public symbols lazily to avoid legacy store import cycles."""
    if name == "EventType":
        from lhgp.persistence.events import EventType

        return EventType
    if name in {"EventInput", "StoredEvent", "StoredLease", "WriteBackResult"}:
        from lhgp.persistence import types

        return getattr(types, name)
    if name in {
        "STORE_SCHEMA_VERSION",
        "StoreConfig",
        "connect",
        "ensure_schema",
        "get_events",
        "transaction",
    }:
        from lhgp.persistence import store

        return getattr(store, name)
    raise AttributeError(name)

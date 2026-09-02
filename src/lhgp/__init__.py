"""LHGP public namespace shim.

The implementation package remains ``longtask`` during the P6 compatibility
window.  This small, dependency-free module makes the protocol's canonical
Python identity available without duplicating the runtime or creating two
independent persistence implementations.  Internal submodule migration is
intentionally deferred until the compatibility window is closed.
"""

from longtask import PROTOCOL_VERSION, __version__

__all__ = ["PROTOCOL_VERSION", "__version__"]

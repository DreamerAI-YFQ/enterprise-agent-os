"""Global tracer registry — allows @traced decorator to find the active Tracer.

Set by application bootstrap (e.g. FastAPI startup) via ``set_global_tracer``.
When None, @traced is a no-op passthrough.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eaos.observability.tracer import Tracer

_tracer: Tracer | None = None


def set_global_tracer(tracer: Tracer | None) -> None:
    """Register or unregister the global tracer."""
    global _tracer
    _tracer = tracer


def get_global_tracer() -> Tracer | None:
    """Return the currently registered global tracer, or None."""
    return _tracer

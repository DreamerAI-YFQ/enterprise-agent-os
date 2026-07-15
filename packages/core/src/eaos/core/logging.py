"""Structured logging configuration via structlog.

JSON output for staging/prod, pretty console for local dev. A custom
processor injects tenant_id / user_id from contextvars (set by API/IM
middleware via eaos.core.context) so every log line carries request scope
without callers threading it through every function signature.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog
from eaos.core.context import tenant_id_var, user_id_var

if TYPE_CHECKING:
    from eaos.core.config import AppConfig


def _inject_tenant_context(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: add tenant_id/user_id from contextvars if set."""
    tenant_id = tenant_id_var.get()
    user_id = user_id_var.get()
    if tenant_id is not None:
        event_dict["tenant_id"] = str(tenant_id)
    if user_id is not None:
        event_dict["user_id"] = str(user_id)
    return event_dict


def configure_logging(config: AppConfig) -> None:
    """Configure structlog + stdlib logging.

    Level: DEBUG when config.debug, else INFO. Output: pretty console for
    local environment, JSON otherwise.
    """
    level = logging.DEBUG if config.debug else logging.INFO

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_tenant_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if config.environment == "local":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging so libraries (sqlalchemy, redis, etc.) flow through
    # the same structlog processors.
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(message)s",
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound with the given name."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]

"""Data classes for external connection management (T5).

These types flow through ``ConnectionManager``: ``ConnectionSpec`` is the
input for register/update, ``ConnectionRecord`` is the persisted row (sans
credentials), and ``ResolvedConnection`` is the live client/connector ready
for tool invocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.data.http_connector import HttpApiConnector
    from eaos.data.mcp.client import McpClient


@dataclass(frozen=True)
class ConnectionSpec:
    """Input for registering or updating an external connection.

    ``config`` shape depends on ``type``:
    - ``mcp_stdio``: ``{"command": "python", "args": ["-m", "..."], "env": {}}``
    - ``http_api``: ``{"base_url": "...", "spec": {...}, "auth": {...}, "pagination": {...}}``
    """

    tenant_id: UUID
    name: str
    type: str  # "mcp_stdio" | "mcp_sse" | "mcp_http" | "http_api"
    config: dict[str, Any]
    credentials: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConnectionRecord:
    """A persisted external connection row — credentials excluded for safety."""

    id: UUID
    tenant_id: UUID
    name: str
    type: str
    config: dict[str, Any]
    health_status: str = "unknown"  # "healthy" | "unhealthy" | "unknown"
    last_health_check: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ResolvedConnection:
    """A live, ready-to-use connection — either an MCP client or HTTP connector."""

    conn_id: UUID
    name: str
    type: str
    mcp_client: McpClient | None = None
    http_connector: HttpApiConnector | None = None


@dataclass(frozen=True)
class HealthStatus:
    """Result of a health check on an external connection."""

    status: str  # "healthy" | "unhealthy" | "unknown"
    last_check: datetime = field(default_factory=lambda: datetime.now())
    error: str | None = None

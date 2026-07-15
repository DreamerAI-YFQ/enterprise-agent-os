"""Data classes for standard MCP client and tool registry.

These types decouple the EAOS agent layer from the underlying MCP transport,
enabling the ToolRegistry to aggregate tools from both external MCP servers
and internal DataConnectors behind a uniform interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpTool:
    """A tool exposed by an MCP server or internal connector.

    The ``name`` is fully-qualified as ``"{server_name}.{tool_name}"`` for MCP
    tools, or ``"{connector}_{operation}"`` for internal connector tools, so
    the ToolRegistry can route ``call_tool`` requests unambiguously.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # "mcp:{server_name}" or "internal:{connector_name}"


@dataclass(frozen=True)
class McpToolResult:
    """Result of calling an MCP tool.

    ``content`` mirrors the MCP protocol's content blocks:
    ``[{"type": "text", "text": "..."}]``.
    """

    content: list[dict[str, Any]]
    is_error: bool = False
    error_message: str | None = None


@dataclass(frozen=True)
class McpResource:
    """A resource exposed by an MCP server."""

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None

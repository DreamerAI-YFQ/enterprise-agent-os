"""MCP client/server and unified tool registry.

Exposes:
- ``McpClient`` / ``McpTransport`` / ``StdioTransport`` — standard MCP client
  for connecting to external MCP servers (T1).
- ``ToolRegistry`` — aggregates tools from MCP clients + internal connectors,
  the single interface between agent runner and all tool sources (T1).
- ``McpServerImpl`` — legacy internal MCP server, deprecated in favor of
  ``McpClient`` + ``ToolRegistry`` (retained for backward compatibility).
- ``McpTool`` / ``McpToolResult`` / ``McpResource`` — data classes.
"""

from __future__ import annotations

from eaos.data.mcp.client import McpClient, McpTransport, StdioTransport
from eaos.data.mcp.registry import ToolRegistry
from eaos.data.mcp.server import EnterpriseMCPServer, McpServerImpl
from eaos.data.mcp.types import McpResource, McpTool, McpToolResult

__all__ = [
    "EnterpriseMCPServer",
    "McpClient",
    "McpResource",
    "McpServerImpl",
    "McpTool",
    "McpToolResult",
    "McpTransport",
    "StdioTransport",
    "ToolRegistry",
]

"""Standard MCP client — connects to external MCP servers via JSON-RPC.

Replaces the internal ``McpServerImpl`` for external system access. Uses the
Anthropic MCP Python SDK (``mcp`` package) under the hood, wrapped in a
transport abstraction so tests can mock the session without spawning
subprocesses.

The ``McpClient`` is one of two tool sources aggregated by ``ToolRegistry``
(external MCP servers + internal DataConnectors). The runner queries the
registry — never the client directly — so multi-source tool discovery is
transparent to the agent layer.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Protocol

from eaos.data.mcp.types import McpResource, McpTool, McpToolResult

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


class McpTransport(Protocol):
    """Abstract MCP transport layer (stdio / SSE / HTTP).

    Manages the session lifecycle: ``connect()`` returns a session-like
    object (the mcp SDK's ``ClientSession`` for stdio, or an equivalent for
    SSE/HTTP), and ``close()`` tears down the underlying connection.
    """

    async def connect(self) -> Any:
        """Establish connection and return an initialized session."""
        ...

    async def close(self) -> None:
        """Close the connection and release resources."""
        ...


class StdioTransport:
    """Stdio MCP transport using the mcp SDK.

    Launches an MCP server as a subprocess and communicates over stdin/stdout
    JSON-RPC. The subprocess lifecycle is managed via an ``AsyncExitStack`` so
    that ``close()`` cleanly terminates both the session and the subprocess.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def connect(self) -> Any:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env,
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self._session = session
        return session

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._session = None


class McpClient:
    """Standard MCP client — connects to an external MCP server.

    Wraps a ``McpTransport`` to provide high-level MCP operations
    (``list_tools``, ``call_tool``, ``list_resources``, ``read_resource``).
    Tool names are qualified with ``server_name`` to avoid collisions across
    multiple MCP servers in the ``ToolRegistry``.

    The ``tenant_id`` parameter is accepted for EAOS-side tenant scoping but
    is NOT transmitted to the MCP server — the MCP protocol is tenant-agnostic.
    Tenant isolation is enforced by EAOS connectors (T6) and the write
    pipeline (T3), not by external MCP servers.
    """

    def __init__(self, transport: McpTransport, server_name: str) -> None:
        self._transport = transport
        self._server_name = server_name
        self._session: Any = None
        self._connected = False

    @property
    def server_name(self) -> str:
        return self._server_name

    async def _ensure_session(self) -> Any:
        if not self._connected:
            self._session = await self._transport.connect()
            self._connected = True
        return self._session

    async def list_tools(self) -> list[McpTool]:
        """JSON-RPC ``tools/list`` → returns tools exposed by this server."""
        session = await self._ensure_session()
        result = await session.list_tools()
        tools: list[McpTool] = []
        for tool in result.tools:
            schema: dict[str, Any] = (
                dict(tool.inputSchema) if tool.inputSchema else {}
            )
            tools.append(
                McpTool(
                    name=f"{self._server_name}.{tool.name}",
                    description=tool.description or "",
                    input_schema=schema,
                    source=f"mcp:{self._server_name}",
                )
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> McpToolResult:
        """JSON-RPC ``tools/call`` → invoke an external tool.

        Strips the ``server_name.`` prefix if present, so callers can use the
        fully-qualified name from ``list_tools``.
        """
        del tenant_id  # MCP protocol is tenant-agnostic; EAOS enforces scoping

        bare_name = self._strip_prefix(tool_name)
        session = await self._ensure_session()
        result = await session.call_tool(bare_name, arguments)

        content: list[dict[str, Any]] = []
        for block in result.content:
            entry: dict[str, Any] = {"type": getattr(block, "type", "text")}
            text = getattr(block, "text", None)
            if text is not None:
                entry["text"] = text
            content.append(entry)

        error_msg: str | None = None
        if result.isError and content:
            error_msg = str(content[0].get("text", "MCP tool error"))

        return McpToolResult(
            content=content,
            is_error=result.isError,
            error_message=error_msg,
        )

    async def list_resources(self) -> list[McpResource]:
        """JSON-RPC ``resources/list`` → returns resources exposed by this server."""
        session = await self._ensure_session()
        result = await session.list_resources()
        return [
            McpResource(
                uri=str(r.uri),
                name=r.name,
                description=getattr(r, "description", None),
                mime_type=getattr(r, "mimeType", None),
            )
            for r in result.resources
        ]

    async def read_resource(self, uri: str, tenant_id: UUID) -> str:
        """JSON-RPC ``resources/read`` → fetch resource content as text."""
        del tenant_id

        session = await self._ensure_session()
        result = await session.read_resource(uri)
        parts: list[str] = []
        for block in result.contents:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts)

    async def health_check(self) -> bool:
        """Check if the MCP server is reachable by calling ``list_tools``."""
        try:
            await self.list_tools()
            return True
        except Exception:
            logger.warning(
                "MCP health check failed for server '%s'", self._server_name
            )
            return False

    async def close(self) -> None:
        """Close the underlying transport connection."""
        if self._connected:
            await self._transport.close()
            self._session = None
            self._connected = False

    def _strip_prefix(self, tool_name: str) -> str:
        """Remove the ``server_name.`` prefix from a qualified tool name."""
        prefix = f"{self._server_name}."
        if tool_name.startswith(prefix):
            return tool_name[len(prefix):]
        return tool_name

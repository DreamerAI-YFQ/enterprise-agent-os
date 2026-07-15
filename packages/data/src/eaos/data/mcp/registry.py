"""Unified tool registry — aggregates tools from MCP clients and internal connectors.

The ``ToolRegistry`` is the single interface between the agent runner and all
tool sources. It merges:
- **External MCP servers** (via ``McpClient.list_tools``) — arbitrary tools
  defined by the server, qualified as ``"{server}.{tool}"``.
- **Internal DataConnectors** — 3 canonical read tools per connector
  (``{connector}_list_resources``, ``{connector}_read``,
  ``{connector}_describe_schema``), matching the legacy ``McpServerImpl`` tool
  surface for backward compatibility.

The runner calls ``list_tools`` to build the LLM tool catalog, then
``call_tool`` to route a tool invocation to the correct source. Write
operations (T3 WritePipeline) are layered on top of ``call_tool`` by
inspecting ``tool_name`` and deciding whether to enter the guarded pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from eaos.data.connector import ReadQuery
from eaos.data.mcp.types import McpTool, McpToolResult

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.data.connector import DataConnector
    from eaos.data.mcp.client import McpClient

logger = logging.getLogger(__name__)

_INTERNAL_OPERATIONS = ("list_resources", "read", "describe_schema")


class ToolRegistry:
    """Aggregates all tool sources (external MCP + internal connectors).

    Use ``register_mcp`` to add an external MCP server and
    ``register_internal`` to add an internal DataConnector. The runner queries
    ``list_tools`` / ``call_tool`` without needing to know which source a tool
    belongs to.

    C07: Write tools are registered via ``register_write_tool`` and executed
    through ``call_write_tool`` via the WritePipeline governance path.
    """

    def __init__(self) -> None:
        self._mcp_clients: dict[str, McpClient] = {}
        self._internal_connectors: dict[str, DataConnector] = {}
        # C07: Write pipeline and write tool definitions
        self._write_pipeline: Any = None  # WritePipeline | None
        self._write_tools: dict[str, dict[str, Any]] = {}  # tool_name → spec

    def register_mcp(self, name: str, client: McpClient) -> None:
        """Register an external MCP client under ``name``."""
        self._mcp_clients[name] = client

    def register_internal(self, name: str, connector: DataConnector) -> None:
        """Register an internal DataConnector under ``name``."""
        self._internal_connectors[name] = connector

    def set_write_pipeline(self, pipeline: Any) -> None:
        """C07: Inject the WritePipeline for governed write operations."""
        self._write_pipeline = pipeline

    def register_write_tool(
        self,
        tool_name: str,
        resource: str,
        operation: str = "create",
        risk_level: str = "high",
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """C07: Register a governed write tool.

        Write tools are executed through WritePipeline, which enforces
        permission checks, HITL approval, audit logging, and rollback.
        """
        self._write_tools[tool_name] = {
            "resource": resource,
            "operation": operation,
            "risk_level": risk_level,
            "description": description or f"Create {resource} via governed pipeline",
            "input_schema": input_schema or {"type": "object", "properties": {}},
        }

    def is_write_tool(self, tool_name: str) -> bool:
        """C07: Check if a tool name is a registered write tool."""
        return tool_name in self._write_tools

    def unregister_mcp(self, name: str) -> None:
        self._mcp_clients.pop(name, None)

    def unregister_internal(self, name: str) -> None:
        self._internal_connectors.pop(name, None)

    async def list_tools(self, tenant_id: UUID) -> list[McpTool]:
        """Aggregate tools from all registered sources (including write tools)."""
        tools: list[McpTool] = []

        for name, client in self._mcp_clients.items():
            try:
                mcp_tools = await client.list_tools()
                tools.extend(mcp_tools)
            except Exception:
                logger.exception("list_tools failed for MCP client '%s'", name)

        for name in self._internal_connectors:
            for op in _INTERNAL_OPERATIONS:
                tools.append(self._internal_tool_spec(name, op))

        # C07: Add registered write tools
        for tool_name, spec in self._write_tools.items():
            tools.append(
                McpTool(
                    name=tool_name,
                    description=spec["description"],
                    input_schema=spec["input_schema"],
                    source="write_pipeline",
                )
            )

        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> McpToolResult:
        """Route a tool call to the correct source (MCP client or internal).

        C07: Write tools cannot be called via this method — they require
        full ToolExecutionContext. Use ``call_write_tool`` instead.
        """
        # C07: Block write tools from unguarded call_tool path
        if tool_name in self._write_tools:
            return McpToolResult(
                content=[{"type": "text", "text": _json_str({
                    "error": "write tool must be called via call_write_tool with full context",
                    "tool": tool_name,
                })}],
                is_error=True,
                error_message="write tool requires governed execution path",
            )

        mcp_result = await self._try_mcp_call(tool_name, arguments, tenant_id)
        if mcp_result is not None:
            return mcp_result

        internal_result = await self._try_internal_call(
            tool_name, arguments, tenant_id
        )
        if internal_result is not None:
            return internal_result

        return McpToolResult(
            content=[{"type": "text", "text": f"unknown tool: {tool_name}"}],
            is_error=True,
            error_message=f"unknown tool: {tool_name}",
        )

    async def call_write_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        ctx: Any,  # ToolExecutionContext from C01
    ) -> McpToolResult:
        """C07: Execute a write tool through the governed WritePipeline.

        This is the ONLY path for write operations. It enforces:
        - Pre-action guard (permission/capability check)
        - HITL approval for high-risk writes
        - Audit logging (before/after snapshots)
        - Compensating rollback on failure

        Returns McpToolResult with the write outcome. If approval is required,
        raises the WriteApprovalRequired exception for the caller to handle.
        """
        from eaos.harness.write_pipeline import WriteApprovalRequired, WriteIntent

        if tool_name not in self._write_tools:
            return McpToolResult(
                content=[{"type": "text", "text": _json_str({
                    "error": f"not a registered write tool: {tool_name}",
                })}],
                is_error=True,
                error_message=f"unknown write tool: {tool_name}",
            )

        if self._write_pipeline is None:
            return McpToolResult(
                content=[{"type": "text", "text": _json_str({
                    "error": "write pipeline not configured",
                })}],
                is_error=True,
                error_message="write pipeline not configured",
            )

        spec = self._write_tools[tool_name]

        # C09: Generate idempotency key from context + tool + arguments.
        # Same session + same tool + same arguments = same key.
        # Retries within the same session reuse the key, preventing duplicates.
        import hashlib
        import json

        raw = f"{ctx.tenant_id}:{ctx.session_id}:{tool_name}:{json.dumps(arguments, sort_keys=True, default=str)}"
        idempotency_key = hashlib.sha256(raw.encode()).hexdigest()[:32]

        # Build WriteIntent from context + tool spec + arguments
        intent = WriteIntent(
            tenant_id=ctx.tenant_id,
            principal_id=ctx.user_id,
            agent_id=ctx.agent_id,
            tool_name=tool_name,
            resource=spec["resource"],
            operation=spec["operation"],
            data=arguments,
            agent_scope=ctx.agent_scope,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            risk_level=spec["risk_level"],
            department_ids=list(ctx.department_ids),
            idempotency_key=idempotency_key,  # C09: dedup key
        )

        try:
            outcome = await self._write_pipeline.execute(intent)
        except WriteApprovalRequired as exc:
            # Re-raise for the caller (runner) to handle HITL flow
            raise

        return McpToolResult(
            content=[{"type": "text", "text": _json_str({
                "success": outcome.success,
                "before": outcome.before,
                "after": outcome.after,
                "error": outcome.error,
                "audit_id": str(outcome.audit_id) if outcome.audit_id else None,
                "rolled_back": outcome.rolled_back,
                "rollback_error": outcome.rollback_error,
                "approval_id": str(outcome.approval_id) if outcome.approval_id else None,
            })}],
            is_error=not outcome.success,
            error_message=outcome.error,
        )

    async def health_check_all(self) -> dict[str, bool]:
        """Check all MCP clients. Internal connectors are assumed healthy."""
        results: dict[str, bool] = {}
        for name, client in self._mcp_clients.items():
            results[name] = await client.health_check()
        return results

    async def close_all(self) -> None:
        """Close all MCP client connections."""
        for client in self._mcp_clients.values():
            try:
                await client.close()
            except Exception:
                logger.exception("failed to close MCP client")

    # -- Internal connector tool specs -----------------------------------

    @staticmethod
    def _internal_tool_spec(connector_name: str, operation: str) -> McpTool:
        """Build a static McpTool for an internal connector operation."""
        tool_name = f"{connector_name}_{operation}"
        if operation == "list_resources":
            return McpTool(
                name=tool_name,
                description=f"List available {connector_name} resources (tables)",
                input_schema={"type": "object", "properties": {}},
                source=f"internal:{connector_name}",
            )
        if operation == "read":
            return McpTool(
                name=tool_name,
                description=(
                    f"Read records from an {connector_name} resource/table. "
                    f"Use {connector_name}_list_resources to discover resources. "
                    "Set resource to the table name (e.g. products, customers, "
                    "opportunities, leads). Leave filters empty to list all records."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "resource": {"type": "string"},
                        "filters": {"type": "object"},
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "limit": {"type": "integer", "default": 100},
                        "offset": {"type": "integer", "default": 0},
                    },
                    "required": ["resource"],
                },
                source=f"internal:{connector_name}",
            )
        # describe_schema
        return McpTool(
            name=tool_name,
            description=f"Describe schema of an {connector_name} resource",
            input_schema={
                "type": "object",
                "properties": {"resource": {"type": "string"}},
                "required": ["resource"],
            },
            source=f"internal:{connector_name}",
        )

    # -- MCP routing -----------------------------------------------------

    async def _try_mcp_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> McpToolResult | None:
        """Attempt to route ``tool_name`` to an MCP client.

        Returns ``None`` if no MCP client matches the tool name prefix.
        """
        for server_name, client in self._mcp_clients.items():
            prefix = f"{server_name}."
            if tool_name.startswith(prefix):
                return await client.call_tool(tool_name, arguments, tenant_id)
        return None

    # -- Internal connector routing --------------------------------------

    async def _try_internal_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> McpToolResult | None:
        """Attempt to route ``tool_name`` to an internal connector.

        Tool names follow ``{connector}_{operation}`` where operation is one
        of ``list_resources`` / ``read`` / ``describe_schema``.
        Returns ``None`` if no internal connector matches.
        """
        for connector_name, connector in self._internal_connectors.items():
            for op in _INTERNAL_OPERATIONS:
                suffix = f"_{op}"
                if tool_name == f"{connector_name}{suffix}":
                    return await self._dispatch_internal(
                        connector, op, arguments, tenant_id
                    )
        return None

    async def _dispatch_internal(
        self,
        connector: DataConnector,
        operation: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> McpToolResult:
        """Execute an internal connector operation and wrap as McpToolResult."""
        try:
            if operation == "list_resources":
                resources = await connector.list_resources(tenant_id)
                payload: dict[str, Any] = {
                    "resources": [
                        {
                            "name": r.name,
                            "display_name": r.display_name,
                            "description": r.description,
                            "access_mode": r.access_mode,
                        }
                        for r in resources
                    ]
                }
                return McpToolResult(
                    content=[{"type": "text", "text": _json_str(payload)}]
                )

            resource = arguments.get("resource")
            if not isinstance(resource, str) or resource == "":
                return McpToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": _json_str({"error": "missing required argument: resource"}),
                        }
                    ],
                    is_error=True,
                    error_message="missing required argument: resource",
                )

            if operation == "read":
                query = _build_read_query(arguments)
                result = await connector.read(tenant_id, resource, query)
                payload = {"rows": result.rows, "total": result.total}
                return McpToolResult(
                    content=[{"type": "text", "text": _json_str(payload)}]
                )

            # describe_schema
            schema = await connector.describe_schema(tenant_id, resource)
            payload = {
                "table_name": schema.table_name,
                "columns": schema.columns,
                "relations": schema.relations,
                "sample_rows": schema.sample_rows,
            }
            return McpToolResult(
                content=[{"type": "text", "text": _json_str(payload)}]
            )
        except Exception as exc:
            logger.exception("internal connector call failed: %s", operation)
            return McpToolResult(
                content=[
                    {"type": "text", "text": _json_str({"error": str(exc)})}
                ],
                is_error=True,
                error_message=str(exc),
            )


def _build_read_query(arguments: dict[str, Any]) -> ReadQuery:
    """Build a ReadQuery from tool arguments, with type-safe defaults."""
    filters: dict[str, object] = {}
    filters_raw = arguments.get("filters")
    if isinstance(filters_raw, dict):
        for k, v in filters_raw.items():
            filters[str(k)] = v

    fields: list[str] | None = None
    fields_raw = arguments.get("fields")
    if isinstance(fields_raw, list):
        fields = [str(f) for f in fields_raw]

    limit_raw = arguments.get("limit", 100)
    limit = limit_raw if isinstance(limit_raw, int) else 100
    offset_raw = arguments.get("offset", 0)
    offset = offset_raw if isinstance(offset_raw, int) else 0

    return ReadQuery(
        filters=filters,
        fields=fields,
        limit=limit,
        offset=offset,
    )


def _json_str(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str, ensure_ascii=False)

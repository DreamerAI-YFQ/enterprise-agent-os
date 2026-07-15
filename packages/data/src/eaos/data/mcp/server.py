"""MCP server protocol — exposes data connectors as MCP tools/resources.

Each enterprise data source (ERP, CRM) wraps its DataConnector in an MCP
server so LangGraph agents can invoke it via standard tool calls.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

from eaos.data.connector import ReadQuery

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.data.connector import DataConnector, DataResource


class EnterpriseMCPServer(Protocol):
    """MCP server exposing enterprise data as tools."""

    async def list_tools(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List MCP tools exposed by this server."""
        ...

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> dict[str, Any]:
        """Invoke an MCP tool with arguments."""
        ...

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        """List MCP resources (tables, objects) available."""
        ...

    async def read_resource(
        self,
        resource_uri: str,
        tenant_id: UUID,
    ) -> bytes:
        """Read a resource by URI (e.g., 'erp://customers/123')."""
        ...


class McpServerImpl:
    """EnterpriseMCPServer backed by a dict of DataConnectors.

    .. deprecated:: Phase 7 T1
        Use ``McpClient`` + ``ToolRegistry`` instead. ``McpServerImpl`` is
        retained for backward compatibility but will be removed in a future
        release. The ``ToolRegistry`` provides the same tool surface for
        internal connectors plus unified routing to external MCP servers.

    Each connector exposes 3 tools: ``{connector}_list_resources``,
    ``{connector}_read``, ``{connector}_describe_schema``. Resource URIs
    follow ``{connector}://{resource}[/{record_id}]``.
    """

    _OPERATIONS = ("list_resources", "read", "describe_schema")

    def __init__(self, connectors: dict[str, DataConnector]) -> None:
        self._connectors = connectors
        self._tool_map: dict[str, tuple[str, str]] = {}
        for name in connectors:
            for op in self._OPERATIONS:
                self._tool_map[f"{name}_{op}"] = (name, op)

    async def list_tools(self, tenant_id: UUID) -> list[dict[str, Any]]:
        del tenant_id  # tools are the same across tenants
        tools: list[dict[str, Any]] = []
        for name in self._connectors:
            tools.append(
                {
                    "name": f"{name}_list_resources",
                    "description": f"List available {name} resources (tables)",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            )
            tools.append(
                {
                    "name": f"{name}_read",
                    "description": f"Read data from an {name} resource",
                    "inputSchema": {
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
                }
            )
            tools.append(
                {
                    "name": f"{name}_describe_schema",
                    "description": f"Describe schema of an {name} resource",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "resource": {"type": "string"},
                        },
                        "required": ["resource"],
                    },
                }
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: UUID,
    ) -> dict[str, Any]:
        mapping = self._tool_map.get(tool_name)
        if mapping is None:
            return {"error": f"unknown tool: {tool_name}"}
        connector_name, op = mapping
        connector = self._connectors[connector_name]

        if op == "list_resources":
            resources = await connector.list_resources(tenant_id)
            return {
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

        resource = arguments.get("resource")
        if not isinstance(resource, str) or resource == "":
            return {"error": "missing required argument: resource"}

        if op == "read":
            query = self._build_read_query(arguments)
            result = await connector.read(tenant_id, resource, query)
            return {"rows": result.rows, "total": result.total}

        # op == "describe_schema"
        schema = await connector.describe_schema(tenant_id, resource)
        return {
            "table_name": schema.table_name,
            "columns": schema.columns,
            "relations": schema.relations,
            "sample_rows": schema.sample_rows,
        }

    @staticmethod
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

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        all_resources: list[DataResource] = []
        for connector in self._connectors.values():
            resources = await connector.list_resources(tenant_id)
            all_resources.extend(resources)
        return all_resources

    async def read_resource(
        self,
        resource_uri: str,
        tenant_id: UUID,
    ) -> bytes:
        parsed = urlparse(resource_uri)
        connector_name = parsed.scheme
        resource = parsed.netloc
        record_id = parsed.path.lstrip("/") or None

        if not connector_name or not resource:
            payload: dict[str, object] = {"error": f"invalid URI: {resource_uri}"}
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        connector = self._connectors.get(connector_name)
        if connector is None:
            payload = {"error": f"unknown connector: {connector_name}"}
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        filters: dict[str, object] = {}
        if record_id is not None:
            filters["id"] = record_id
        query = ReadQuery(filters=filters, limit=100)
        result = await connector.read(tenant_id, resource, query)
        payload = {"rows": result.rows, "total": result.total}
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

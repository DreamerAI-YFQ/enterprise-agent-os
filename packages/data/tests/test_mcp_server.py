"""Unit tests for McpServerImpl — mock DataConnector."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from eaos.data.connector import (
    DataResource,
    DataResult,
    SchemaDescription,
)
from eaos.data.mcp.server import McpServerImpl

TID = UUID("00000000-0000-0000-0000-000000000001")


def _resource(name: str = "products") -> DataResource:
    return DataResource(
        name=name,
        display_name=name.capitalize(),
        description=f"{name} table",
        access_mode="read",
    )


def _schema(table: str = "erp.products") -> SchemaDescription:
    return SchemaDescription(
        table_name=table,
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "comment": None},
            {"name": "name", "type": "varchar", "nullable": False, "comment": "名称"},
        ],
        relations=[],
        sample_rows=[{"id": "x", "name": "sample"}],
    )


def _make_connector(
    *,
    resources: list[DataResource] | None = None,
    rows: list[dict[str, Any]] | None = None,
    schema: SchemaDescription | None = None,
) -> Any:
    connector: Any = MagicMock()
    connector.list_resources = AsyncMock(
        return_value=resources if resources is not None else [_resource()]
    )
    connector.read = AsyncMock(
        return_value=DataResult(
            rows=rows if rows is not None else [{"id": "1", "name": "Alice"}],
            total=len(rows) if rows is not None else 1,
        )
    )
    connector.describe_schema = AsyncMock(
        return_value=schema if schema is not None else _schema()
    )
    return connector


def _make_server(
    *, connectors: dict[str, Any] | None = None
) -> tuple[McpServerImpl, dict[str, Any]]:
    if connectors is None:
        erp = _make_connector()
        crm = _make_connector(resources=[_resource("leads")])
        connectors = {"erp": erp, "crm": crm}
    server = McpServerImpl(connectors)
    return server, connectors


class TestListTools:
    async def test_three_tools_per_connector(self) -> None:
        server, _ = _make_server()
        tools = await server.list_tools(TID)

        names = [t["name"] for t in tools]
        assert "erp_list_resources" in names
        assert "erp_read" in names
        assert "erp_describe_schema" in names
        assert "crm_list_resources" in names
        assert "crm_read" in names
        assert "crm_describe_schema" in names
        assert len(tools) == 6

    async def test_tool_has_name_description_schema(self) -> None:
        server, _ = _make_server()
        tools = await server.list_tools(TID)

        read_tool = next(t for t in tools if t["name"] == "erp_read")
        assert "description" in read_tool
        assert "inputSchema" in read_tool
        assert "resource" in read_tool["inputSchema"]["properties"]
        assert read_tool["inputSchema"]["required"] == ["resource"]

    async def test_single_connector(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})
        tools = await server.list_tools(TID)
        assert len(tools) == 3


class TestCallToolListResources:
    async def test_returns_resources(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool("erp_list_resources", {}, TID)

        assert "resources" in result
        assert len(result["resources"]) == 1
        assert result["resources"][0]["name"] == "products"

    async def test_includes_all_metadata(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool("erp_list_resources", {}, TID)

        res = result["resources"][0]
        assert res["display_name"] == "Products"
        assert res["description"] == "products table"
        assert res["access_mode"] == "read"


class TestCallToolRead:
    async def test_returns_rows_and_total(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool(
            "erp_read", {"resource": "customers"}, TID
        )

        assert result["rows"] == [{"id": "1", "name": "Alice"}]
        assert result["total"] == 1

    async def test_passes_filters(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        await server.call_tool(
            "erp_read",
            {"resource": "customers", "filters": {"industry": "tech"}},
            TID,
        )

        call = connector.read.call_args
        query = call.args[2]
        assert query.filters == {"industry": "tech"}

    async def test_passes_fields(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        await server.call_tool(
            "erp_read",
            {"resource": "customers", "fields": ["name", "code"]},
            TID,
        )

        call = connector.read.call_args
        query = call.args[2]
        assert query.fields == ["name", "code"]

    async def test_passes_limit_and_offset(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        await server.call_tool(
            "erp_read",
            {"resource": "customers", "limit": 50, "offset": 10},
            TID,
        )

        call = connector.read.call_args
        query = call.args[2]
        assert query.limit == 50
        assert query.offset == 10

    async def test_missing_resource_returns_error(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool("erp_read", {}, TID)

        assert "error" in result
        assert "resource" in result["error"]


class TestCallToolDescribeSchema:
    async def test_returns_schema(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool(
            "erp_describe_schema", {"resource": "products"}, TID
        )

        assert result["table_name"] == "erp.products"
        assert len(result["columns"]) == 2
        assert result["sample_rows"] == [{"id": "x", "name": "sample"}]


class TestCallToolErrors:
    async def test_unknown_tool(self) -> None:
        server, _ = _make_server()
        result = await server.call_tool("nonexistent_tool", {}, TID)

        assert "error" in result
        assert "unknown tool" in result["error"]


class TestListResources:
    async def test_aggregates_all_connectors(self) -> None:
        server, _ = _make_server()
        resources = await server.list_resources(TID)

        names = [r.name for r in resources]
        assert "products" in names
        assert "leads" in names
        assert len(resources) == 2


class TestReadResource:
    async def test_parses_uri_without_record_id(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        data = await server.read_resource("erp://customers", TID)
        result = json.loads(data)

        assert result["rows"] == [{"id": "1", "name": "Alice"}]
        assert result["total"] == 1
        call = connector.read.call_args
        query = call.args[2]
        assert query.filters == {}

    async def test_parses_uri_with_record_id(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        await server.read_resource("erp://customers/123", TID)

        call = connector.read.call_args
        query = call.args[2]
        assert query.filters == {"id": "123"}

    async def test_passes_resource_name(self) -> None:
        connector = _make_connector()
        server, _ = _make_server(connectors={"erp": connector})

        await server.read_resource("erp://orders", TID)

        call = connector.read.call_args
        assert call.args[1] == "orders"

    async def test_unknown_connector(self) -> None:
        server, _ = _make_server()
        data = await server.read_resource("oracle://customers", TID)
        result = json.loads(data)

        assert "error" in result
        assert "unknown connector" in result["error"]

    async def test_invalid_uri(self) -> None:
        server, _ = _make_server()
        data = await server.read_resource("not-a-uri", TID)
        result = json.loads(data)

        assert "error" in result
        assert "invalid URI" in result["error"]

    async def test_returns_bytes(self) -> None:
        server, _ = _make_server()
        data = await server.read_resource("erp://customers", TID)
        assert isinstance(data, bytes)

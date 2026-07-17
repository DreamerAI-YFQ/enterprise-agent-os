"""Tests for McpClient and ToolRegistry — standard MCP client + unified registry.

Unit tests mock the MCP session to verify protocol-level conversion (SDK types
→ EAOS McpTool/McpToolResult). ToolRegistry tests verify aggregation and
routing across MCP clients and internal DataConnectors.

Integration tests (marked ``integration``) launch the T0 ``mock_saas.mcp_server``
subprocess and exercise the full stdio JSON-RPC path.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from eaos.core.execution import ToolExecutionContext
from eaos.data.connector import (
    DataResource,
    DataResult,
    SchemaDescription,
)
from eaos.data.mcp.client import McpClient
from eaos.data.mcp.registry import ToolRegistry
from eaos.harness.write_pipeline import WriteOutcome
from eaos.observability._global import set_global_tracer

TID = UUID("00000000-0000-0000-0000-000000000001")


# ============================================================
# Mock helpers
# ============================================================


class _MockSdkTool:
    """Mimics mcp.types.Tool for testing."""

    def __init__(
        self,
        name: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class _MockSdkContent:
    """Mimics mcp.types.TextContent for testing."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _MockSdkCallResult:
    """Mimics mcp.shared.session.CallToolResult."""

    def __init__(self, content: list[_MockSdkContent], is_error: bool = False) -> None:
        self.content = content
        self.isError = is_error


class _MockSdkResource:
    """Mimics mcp.types.Resource."""

    def __init__(
        self,
        uri: str,
        name: str,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> None:
        self.uri = uri
        self.name = name
        self.description = description
        self.mimeType = mime_type


class _MockSdkListToolsResult:
    def __init__(self, tools: list[_MockSdkTool]) -> None:
        self.tools = tools


class _MockSdkListResourcesResult:
    def __init__(self, resources: list[_MockSdkResource]) -> None:
        self.resources = resources


class _MockSdkReadResourceResult:
    def __init__(self, contents: list[_MockSdkContent]) -> None:
        self.contents = contents


class _MockSession:
    """Mock MCP session — configurable return values for each method."""

    def __init__(
        self,
        tools: list[_MockSdkTool] | None = None,
        resources: list[_MockSdkResource] | None = None,
    ) -> None:
        self._tools = tools or []
        self._resources = resources or []
        self.call_tool = AsyncMock()
        self.list_tools = AsyncMock(return_value=_MockSdkListToolsResult(self._tools))
        self.list_resources = AsyncMock(return_value=_MockSdkListResourcesResult(self._resources))
        self.read_resource = AsyncMock(
            return_value=_MockSdkReadResourceResult([_MockSdkContent("resource content")])
        )
        self.initialize = AsyncMock()


class _MockTransport:
    """Mock MCP transport that returns a pre-configured session."""

    def __init__(self, session: _MockSession) -> None:
        self._session = session
        self.connect = AsyncMock(return_value=session)
        self.close = AsyncMock()


def _make_client(
    tools: list[_MockSdkTool] | None = None,
    resources: list[_MockSdkResource] | None = None,
    server_name: str = "mock-saas",
) -> tuple[McpClient, _MockSession, _MockTransport]:
    session = _MockSession(tools=tools, resources=resources)
    transport = _MockTransport(session)
    client = McpClient(transport, server_name)
    return client, session, transport


def _make_internal_connector(
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
    connector.describe_schema = AsyncMock(return_value=schema if schema is not None else _schema())
    return connector


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
        ],
        relations=[],
        sample_rows=[{"id": "x"}],
    )


# ============================================================
# McpClient — list_tools
# ============================================================


class TestMcpClientListTools:
    async def test_returns_tools_with_server_prefix(self) -> None:
        sdk_tools = [
            _MockSdkTool(
                "erp_create_order",
                "Create order",
                {"type": "object", "properties": {"customer_id": {"type": "string"}}},
            ),
            _MockSdkTool("erp_get_order", "Get order"),
        ]
        client, _, _ = _make_client(tools=sdk_tools)

        tools = await client.list_tools()

        assert len(tools) == 2
        assert tools[0].name == "mock-saas.erp_create_order"
        assert tools[0].description == "Create order"
        assert tools[0].source == "mcp:mock-saas"
        assert "customer_id" in tools[0].input_schema["properties"]

    async def test_empty_tool_list(self) -> None:
        client, _, _ = _make_client(tools=[])
        tools = await client.list_tools()
        assert tools == []

    async def test_connects_lazily(self) -> None:
        client, _, transport = _make_client(tools=[])
        assert not transport.connect.called
        await client.list_tools()
        assert transport.connect.called


# ============================================================
# McpClient — call_tool
# ============================================================


class TestMcpClientCallTool:
    async def test_success(self) -> None:
        client, session, _ = _make_client()
        session.call_tool.return_value = _MockSdkCallResult([_MockSdkContent('{"id": "ord_001"}')])

        result = await client.call_tool(
            "mock-saas.erp_create_order",
            {"customer_id": "cus_001", "amount": 100},
            TID,
        )

        assert result.is_error is False
        assert result.content[0]["text"] == '{"id": "ord_001"}'
        # Verify prefix was stripped before calling the session
        session.call_tool.assert_called_once_with(
            "erp_create_order", {"customer_id": "cus_001", "amount": 100}
        )

    async def test_bare_name_without_prefix(self) -> None:
        client, session, _ = _make_client()
        session.call_tool.return_value = _MockSdkCallResult([_MockSdkContent("ok")])

        await client.call_tool("erp_get_order", {"order_id": "ord_001"}, TID)

        session.call_tool.assert_called_once_with("erp_get_order", {"order_id": "ord_001"})

    async def test_error_result(self) -> None:
        client, session, _ = _make_client()
        session.call_tool.return_value = _MockSdkCallResult(
            [_MockSdkContent("order not found")], is_error=True
        )

        result = await client.call_tool("erp_get_order", {"order_id": "x"}, TID)

        assert result.is_error is True
        assert result.error_message == "order not found"

    async def test_tenant_id_not_sent_to_session(self) -> None:
        """MCP protocol is tenant-agnostic; tenant_id is EAOS-side only."""
        client, session, _ = _make_client()
        session.call_tool.return_value = _MockSdkCallResult([_MockSdkContent("ok")])

        await client.call_tool("test_tool", {}, TID)

        call_args = session.call_tool.call_args
        assert len(call_args.args) == 2  # (name, arguments) — no tenant_id


# ============================================================
# McpClient — resources
# ============================================================


class TestMcpClientResources:
    async def test_list_resources(self) -> None:
        sdk_resources = [
            _MockSdkResource("erp://orders", "Orders", "ERP orders"),
            _MockSdkResource("crm://customers", "Customers"),
        ]
        client, _, _ = _make_client(resources=sdk_resources)

        resources = await client.list_resources()

        assert len(resources) == 2
        assert resources[0].uri == "erp://orders"
        assert resources[0].name == "Orders"
        assert resources[0].description == "ERP orders"

    async def test_read_resource(self) -> None:
        client, _, _ = _make_client()
        text = await client.read_resource("erp://orders/ord_001", TID)
        assert text == "resource content"


# ============================================================
# McpClient — health_check + close
# ============================================================


class TestMcpClientHealthCheck:
    async def test_healthy(self) -> None:
        client, _, _ = _make_client(tools=[_MockSdkTool("t1")])
        assert await client.health_check() is True

    async def test_unhealthy(self) -> None:
        client, session, _ = _make_client()
        session.list_tools.side_effect = RuntimeError("connection lost")
        assert await client.health_check() is False


class TestMcpClientClose:
    async def test_close_calls_transport(self) -> None:
        client, _, transport = _make_client()
        await client.list_tools()  # establish connection
        await client.close()
        assert transport.close.called


# ============================================================
# ToolRegistry — list_tools aggregation
# ============================================================


class TestToolRegistryListTools:
    async def test_aggregates_mcp_and_internal(self) -> None:
        registry = ToolRegistry()

        mcp_client, _, _ = _make_client(
            tools=[_MockSdkTool("external_tool", "External")],
            server_name="ext",
        )
        registry.register_mcp("ext", mcp_client)
        registry.register_internal("erp", _make_internal_connector())

        tools = await registry.list_tools(TID)

        names = [t.name for t in tools]
        assert "ext.external_tool" in names
        assert "erp_list_resources" in names
        assert "erp_read" in names
        assert "erp_describe_schema" in names
        assert len(tools) == 4

    async def test_mcp_failure_does_not_block_internal(self) -> None:
        registry = ToolRegistry()

        mcp_client, session, _ = _make_client(server_name="ext")
        session.list_tools.side_effect = RuntimeError("connection lost")
        registry.register_mcp("ext", mcp_client)
        registry.register_internal("erp", _make_internal_connector())

        tools = await registry.list_tools(TID)

        # MCP failed but internal tools still present
        names = [t.name for t in tools]
        assert "ext.external_tool" not in names
        assert "erp_read" in names

    async def test_empty_registry(self) -> None:
        registry = ToolRegistry()
        tools = await registry.list_tools(TID)
        assert tools == []


# ============================================================
# ToolRegistry — call_tool routing
# ============================================================


class TestToolRegistryCallToolMcp:
    async def test_routes_to_mcp_client(self) -> None:
        registry = ToolRegistry()

        mcp_client, session, _ = _make_client(
            tools=[_MockSdkTool("create_order", "Create")],
            server_name="erp",
        )
        registry.register_mcp("erp", mcp_client)
        session.call_tool.return_value = _MockSdkCallResult([_MockSdkContent('{"id": "ord_001"}')])

        result = await registry.call_tool("erp.create_order", {"amount": 100}, TID)

        assert result.is_error is False
        assert "ord_001" in result.content[0]["text"]


class TestToolRegistryCallToolInternal:
    async def test_routes_list_resources(self) -> None:
        registry = ToolRegistry()
        connector = _make_internal_connector(
            resources=[_resource("orders"), _resource("customers")]
        )
        registry.register_internal("erp", connector)

        result = await registry.call_tool("erp_list_resources", {}, TID)

        assert result.is_error is False
        payload = json.loads(result.content[0]["text"])
        assert len(payload["resources"]) == 2
        assert payload["resources"][0]["name"] == "orders"

    async def test_routes_read(self) -> None:
        registry = ToolRegistry()
        connector = _make_internal_connector(rows=[{"id": "1", "name": "Alice"}])
        registry.register_internal("erp", connector)

        result = await registry.call_tool("erp_read", {"resource": "customers", "limit": 10}, TID)

        assert result.is_error is False
        payload = json.loads(result.content[0]["text"])
        assert payload["rows"] == [{"id": "1", "name": "Alice"}]
        assert payload["total"] == 1

        # Verify ReadQuery was built correctly
        connector.read.assert_called_once()
        call = connector.read.call_args
        assert call.args[0] == TID  # tenant_id
        assert call.args[1] == "customers"  # resource
        query = call.args[2]
        assert query.limit == 10

    async def test_routes_describe_schema(self) -> None:
        registry = ToolRegistry()
        connector = _make_internal_connector()
        registry.register_internal("erp", connector)

        result = await registry.call_tool("erp_describe_schema", {"resource": "products"}, TID)

        assert result.is_error is False
        payload = json.loads(result.content[0]["text"])
        assert payload["table_name"] == "erp.products"

    async def test_read_missing_resource_returns_error(self) -> None:
        registry = ToolRegistry()
        registry.register_internal("erp", _make_internal_connector())

        result = await registry.call_tool("erp_read", {}, TID)

        assert result.is_error is True
        assert "resource" in (result.error_message or "")


class TestToolRegistryCallToolErrors:
    async def test_unknown_tool(self) -> None:
        registry = ToolRegistry()
        result = await registry.call_tool("nonexistent", {}, TID)

        assert result.is_error is True
        assert "unknown tool" in (result.error_message or "")

    async def test_internal_connector_exception(self) -> None:
        registry = ToolRegistry()
        connector = _make_internal_connector()
        connector.read.side_effect = RuntimeError("DB connection lost")
        registry.register_internal("erp", connector)

        result = await registry.call_tool("erp_read", {"resource": "customers"}, TID)

        assert result.is_error is True
        assert "DB connection lost" in (result.error_message or "")


class TestToolRegistryWriteNormalization:
    async def test_sales_order_model_price_does_not_change_idempotency(self) -> None:
        registry = ToolRegistry()
        pipeline = AsyncMock()
        pipeline.execute.return_value = WriteOutcome(success=True)
        registry.set_write_pipeline(pipeline)
        registry.register_write_tool(
            "erp_create_sales_order",
            resource="orders",
            operation="create",
            risk_level="high",
        )
        ctx = ToolExecutionContext(
            tenant_id=TID,
            user_id=UUID("00000000-0000-0000-0000-000000000201"),
            agent_id=UUID("00000000-0000-0000-0000-000000000301"),
            session_id=UUID("00000000-0000-0000-0000-000000000401"),
            agent_scope="personal",
        )

        await registry.call_write_tool(
            "erp_create_sales_order",
            {
                "customer_code": "CUS-TECH-0001",
                "product_sku": "PRD-ELEC-001",
                "quantity": 3,
                "unit_price": 0,
                "amount": 0,
            },
            ctx,
        )
        await registry.call_write_tool(
            "erp_create_sales_order",
            {
                "customer_id": "CUS-TECH-0001",
                "product_id": "PRD-ELEC-001",
                "quantity": 3,
            },
            ctx,
        )

        first_intent = pipeline.execute.await_args_list[0].args[0]
        second_intent = pipeline.execute.await_args_list[1].args[0]
        assert first_intent.data == {
            "customer_code": "CUS-TECH-0001",
            "product_sku": "PRD-ELEC-001",
            "quantity": 3,
        }
        assert second_intent.data == first_intent.data
        assert second_intent.idempotency_key == first_intent.idempotency_key


class TestToolRegistryResumeWrite:
    async def test_resume_uses_current_execution_trace(self) -> None:
        registry = ToolRegistry()
        pipeline = AsyncMock()
        pipeline.execute.return_value = WriteOutcome(success=True)
        registry.set_write_pipeline(pipeline)
        registry.register_write_tool(
            "erp_create_sales_order",
            resource="orders",
            operation="create",
            risk_level="high",
        )
        execution_trace_id = UUID("00000000-0000-0000-0000-000000000901")
        tracer = AsyncMock()
        tracer.current_trace_id.return_value = execution_trace_id
        set_global_tracer(tracer)
        approval = {
            "id": "00000000-0000-0000-0000-000000000701",
            "tenant_id": str(TID),
            "agent_id": "00000000-0000-0000-0000-000000000301",
            "session_id": "00000000-0000-0000-0000-000000000401",
            "requested_by": "00000000-0000-0000-0000-000000000201",
            "tool_name": "erp_create_sales_order",
            "resource": "orders",
            "operation": "create",
            "risk_level": "high",
            "intent_data": {
                "data": {"quantity": 1},
                "trace_id": "00000000-0000-0000-0000-000000000801",
                "idempotency_key": "idem-1",
            },
        }
        try:
            result = await registry.resume_write_tool(approval)
        finally:
            set_global_tracer(None)

        assert result.is_error is False
        intent = pipeline.execute.call_args.args[0]
        assert intent.trace_id == execution_trace_id


# ============================================================
# ToolRegistry — health check
# ============================================================


class TestToolRegistryHealthCheck:
    async def test_checks_all_mcp_clients(self) -> None:
        registry = ToolRegistry()

        healthy_client, _, _ = _make_client(tools=[_MockSdkTool("t1")], server_name="healthy")
        unhealthy_client, session, _ = _make_client(server_name="unhealthy")
        session.list_tools.side_effect = RuntimeError("down")

        registry.register_mcp("healthy", healthy_client)
        registry.register_mcp("unhealthy", unhealthy_client)

        results = await registry.health_check_all()

        assert results["healthy"] is True
        assert results["unhealthy"] is False


# ============================================================
# Integration test — real mock_saas MCP server subprocess
# ============================================================


@pytest.mark.integration
class TestMcpClientIntegration:
    """Integration: launch mock_saas.mcp_server subprocess → McpClient stdio."""

    async def test_list_tools_returns_mock_saas_tools(self) -> None:
        from eaos.data.mcp.client import StdioTransport

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "mock_saas.mcp_server"],
        )
        client = McpClient(transport, "mock-saas")

        try:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert "mock-saas.erp_create_order" in names
            assert "mock-saas.crm_get_customer" in names
            assert "mock-saas.erp_update_inventory" in names
        finally:
            await client.close()

    async def test_call_tool_creates_order(self) -> None:
        from eaos.data.mcp.client import StdioTransport

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "mock_saas.mcp_server"],
        )
        client = McpClient(transport, "mock-saas")

        try:
            result = await client.call_tool(
                "mock-saas.erp_create_order",
                {
                    "customer_id": "cus_0001",
                    "amount": 5000,
                    "currency": "CNY",
                    "status": "pending",
                },
                TID,
            )

            assert result.is_error is False
            payload = json.loads(result.content[0]["text"])
            assert payload["customer_id"] == "cus_0001"
            assert payload["amount"] == 5000
            assert payload["id"].startswith("ord_")
        finally:
            await client.close()

"""Tests for the MCP server wrapper — launches the stdio subprocess and
exercises it via the official MCP client SDK.

This validates that ``mock_saas.mcp_server`` speaks the standard MCP protocol
(tools/list + tools/call over JSON-RPC stdio), which is the contract the EAOS
McpClient (Phase 7 T1) will rely on. Not marked ``integration`` because it
needs no external services — only the mcp SDK and a subprocess.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import (
    StdioServerParameters,
    stdio_client,
)

_SUBPROCESS_TIMEOUT: float = 30.0


def _server_params() -> StdioServerParameters:
    """Parameters to launch the mock_saas MCP server as a stdio subprocess."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mock_saas.mcp_server"],
    )


async def _run_session(actions: Any) -> Any:
    """Open a stdio client + session, run ``actions(session)``, return result.

    Wrapped in a timeout so a misbehaving subprocess cannot hang the suite.
    """
    async with (
        stdio_client(_server_params()) as (read, write),
        ClientSession(read, write) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=_SUBPROCESS_TIMEOUT)
        return await asyncio.wait_for(actions(session), timeout=_SUBPROCESS_TIMEOUT)


def _parse_content(result: Any) -> Any:
    """Extract JSON from the first text content block of a CallToolResult.

    Falls back to ``{"error": text}`` when the content is plain text (e.g.
    SDK-level input validation errors that aren't JSON-encoded).
    """
    content = result.content
    assert len(content) >= 1
    first = content[0]
    text = getattr(first, "text", None)
    assert text is not None, "expected a text content block"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text}


class TestListTools:
    async def test_exposes_expected_tools(self) -> None:
        async def actions(session: ClientSession) -> list[str]:
            result = await session.list_tools()
            return [t.name for t in result.tools]

        names = await _run_session(actions)
        expected = {
            "erp_list_orders",
            "erp_get_order",
            "erp_create_order",
            "erp_update_order",
            "erp_delete_order",
            "crm_list_customers",
            "crm_get_customer",
            "crm_create_customer",
            "erp_get_inventory",
            "erp_update_inventory",
        }
        assert expected.issubset(set(names)), f"missing: {expected - set(names)}"

    async def test_tool_has_input_schema(self) -> None:
        async def actions(session: ClientSession) -> dict[str, Any]:
            result = await session.list_tools()
            tool = next(t for t in result.tools if t.name == "erp_create_order")
            schema = tool.inputSchema
            return dict(schema) if isinstance(schema, dict) else {}

        schema = await _run_session(actions)
        assert schema["type"] == "object"
        assert "customer_id" in schema["properties"]
        assert "amount" in schema["properties"]
        assert "customer_id" in schema["required"]


class TestCallToolRead:
    async def test_erp_get_order(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_get_order", {"order_id": "ord_0001"}
            )

        result = await _run_session(actions)
        order = _parse_content(result)
        assert order["id"] == "ord_0001"
        assert order["customer_id"] == "cus_acme"

    async def test_erp_get_order_not_found(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_get_order", {"order_id": "ord_nope"}
            )

        result = await _run_session(actions)
        body = _parse_content(result)
        assert "error" in body

    async def test_crm_get_customer(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "crm_get_customer", {"customer_id": "cus_acme"}
            )

        result = await _run_session(actions)
        customer = _parse_content(result)
        assert customer["name"] == "ACME 工业有限公司"
        assert customer["tier"] == "vip"

    async def test_erp_list_orders_filtered(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_list_orders", {"status": "pending"}
            )

        result = await _run_session(actions)
        orders = _parse_content(result)
        assert len(orders) == 4
        assert all(o["status"] == "pending" for o in orders)

    async def test_erp_get_inventory(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_get_inventory", {"sku": "SKU-0000"}
            )

        result = await _run_session(actions)
        inv = _parse_content(result)
        assert inv["product_name"] == "电机 750W"
        assert inv["quantity"] == 120


class TestCallToolWrite:
    async def test_erp_create_order(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_create_order",
                {
                    "customer_id": "cus_acme",
                    "amount": 500000.0,
                    "currency": "CNY",
                    "status": "pending",
                    "items": [
                        {"sku": "SKU-0000", "quantity": 1, "unit_price": 500000.0}
                    ],
                },
            )

        result = await _run_session(actions)
        order = _parse_content(result)
        assert order["customer_id"] == "cus_acme"
        assert order["amount"] == 500000.0
        assert order["id"].startswith("ord_")
        assert len(order["items"]) == 1

    async def test_erp_update_order_status(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_update_order",
                {"order_id": "ord_0001", "status": "shipped"},
            )

        result = await _run_session(actions)
        order = _parse_content(result)
        assert order["status"] == "shipped"

    async def test_erp_delete_order(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_delete_order", {"order_id": "ord_0002"}
            )

        result = await _run_session(actions)
        body = _parse_content(result)
        assert body["deleted"] is True

    async def test_erp_update_inventory(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "erp_update_inventory",
                {"sku": "SKU-0001", "quantity": 99},
            )

        result = await _run_session(actions)
        inv = _parse_content(result)
        assert inv["quantity"] == 99

    async def test_crm_create_customer(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool(
                "crm_create_customer",
                {
                    "name": "Hooli",
                    "region": "华南",
                    "tier": "gold",
                    "contact_email": "hi@hooli.com",
                },
            )

        result = await _run_session(actions)
        customer = _parse_content(result)
        assert customer["name"] == "Hooli"
        assert customer["tier"] == "gold"
        assert customer["id"].startswith("cus_")


class TestCallToolErrors:
    async def test_unknown_tool(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool("nonexistent_tool", {})

        result = await _run_session(actions)
        body = _parse_content(result)
        assert "error" in body
        assert "unknown tool" in body["error"]

    async def test_missing_required_argument(self) -> None:
        async def actions(session: ClientSession) -> Any:
            return await session.call_tool("erp_get_order", {})

        result = await _run_session(actions)
        body = _parse_content(result)
        assert "error" in body

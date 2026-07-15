"""Standard MCP server wrapper for mock SaaS — exposes tools over stdio.

Wraps the mock SaaS data layer (orders/customers/inventory) as MCP tools so
the EAOS McpClient (Phase 7 T1) can connect via stdio JSON-RPC and exercise
realistic create/read/update flows against this simulated external system.

The server calls the in-process data layer directly rather than HTTP-looping
to the REST API: this keeps the subprocess self-contained (no dependency on
the REST API being up) while presenting an identical MCP tool surface.

Run as a subprocess (launched by McpClient)::

    python -m mock_saas.mcp_server
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mock_saas.db import MockSaasDB, get_db
from mock_saas.models import (
    Customer,
    Order,
    OrderCreate,
    OrderItem,
    OrderUpdate,
)

server: Server = Server("mock-saas")


def _tool_specs() -> list[Tool]:
    """Static tool catalog exposed to MCP clients."""
    return [
        Tool(
            name="erp_list_orders",
            description="List ERP orders, optionally filtered by customer_id and/or status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "confirmed", "shipped", "delivered", "cancelled"],
                    },
                },
            },
        ),
        Tool(
            name="erp_get_order",
            description="Fetch a single ERP order by id.",
            inputSchema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        ),
        Tool(
            name="erp_create_order",
            description="Create a new ERP order for a customer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "amount": {"type": "number", "minimum": 0},
                    "currency": {"type": "string", "default": "CNY"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "confirmed", "shipped", "delivered", "cancelled"],
                        "default": "pending",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer", "minimum": 1},
                                "unit_price": {"type": "number", "minimum": 0},
                            },
                            "required": ["sku", "quantity", "unit_price"],
                        },
                    },
                },
                "required": ["customer_id", "amount"],
            },
        ),
        Tool(
            name="erp_update_order",
            description="Update an existing ERP order (any subset of fields).",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "confirmed", "shipped", "delivered", "cancelled"],
                    },
                    "amount": {"type": "number", "minimum": 0},
                    "customer_id": {"type": "string"},
                },
                "required": ["order_id"],
            },
        ),
        Tool(
            name="erp_delete_order",
            description="Delete an ERP order by id.",
            inputSchema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        ),
        Tool(
            name="crm_list_customers",
            description="List CRM customers, optionally filtered by region and/or tier.",
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {"type": "string"},
                    "tier": {
                        "type": "string",
                        "enum": ["standard", "silver", "gold", "vip"],
                    },
                },
            },
        ),
        Tool(
            name="crm_get_customer",
            description="Fetch a single CRM customer by id.",
            inputSchema={
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
        ),
        Tool(
            name="crm_create_customer",
            description="Create a new CRM customer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "region": {"type": "string"},
                    "tier": {
                        "type": "string",
                        "enum": ["standard", "silver", "gold", "vip"],
                        "default": "standard",
                    },
                    "contact_email": {"type": "string"},
                },
                "required": ["name", "region", "contact_email"],
            },
        ),
        Tool(
            name="erp_get_inventory",
            description="Fetch an inventory record by SKU.",
            inputSchema={
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
            },
        ),
        Tool(
            name="erp_update_inventory",
            description="Update inventory quantity (and optionally product name / warehouse).",
            inputSchema={
                "type": "object",
                "properties": {
                    "sku": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 0},
                    "product_name": {"type": "string"},
                    "warehouse": {"type": "string"},
                },
                "required": ["sku", "quantity"],
            },
        ),
    ]


def _text(payload: Any) -> list[TextContent]:
    """Serialize a payload as a single TextContent block."""
    return [TextContent(type="text", text=json.dumps(payload, default=str, ensure_ascii=False))]


def _call_tool_impl(name: str, arguments: dict[str, Any], db: MockSaasDB) -> Any:
    """Dispatch a tool call to the data layer. Returns JSON-serializable data.

    Raises ValueError on bad arguments or unknown tool names; the caller maps
    that to an MCP error result.
    """
    if name == "erp_list_orders":
        rows = db.list_orders(
            customer_id=arguments.get("customer_id"),
            status=arguments.get("status"),
        )
        return [r.model_dump(mode="json") for r in rows]

    if name == "erp_get_order":
        oid = arguments["order_id"]
        order = db.get_order(oid)
        if order is None:
            return {"error": f"order not found: {oid}"}
        return order.model_dump(mode="json")

    if name == "erp_create_order":
        items = [OrderItem(**i) for i in arguments.get("items", [])]
        create = OrderCreate(
            customer_id=arguments["customer_id"],
            amount=float(arguments["amount"]),
            currency=arguments.get("currency", "CNY"),
            status=arguments.get("status", "pending"),
            items=items,
        )
        order = Order(
            customer_id=create.customer_id,
            amount=create.amount,
            currency=create.currency,
            status=create.status,
            items=create.items,
        )
        return db.create_order(order).model_dump(mode="json")

    if name == "erp_update_order":
        oid = arguments["order_id"]
        update = OrderUpdate(
            status=arguments.get("status"),
            amount=arguments.get("amount"),
            customer_id=arguments.get("customer_id"),
        )
        updated = db.update_order(
            oid,
            status=update.status,
            amount=update.amount,
            customer_id=update.customer_id,
        )
        if updated is None:
            return {"error": f"order not found: {oid}"}
        return updated.model_dump(mode="json")

    if name == "erp_delete_order":
        oid = arguments["order_id"]
        deleted = db.delete_order(oid)
        return {"deleted": deleted, "order_id": oid}

    if name == "crm_list_customers":
        customers = db.list_customers(
            region=arguments.get("region"),
            tier=arguments.get("tier"),
        )
        return [c.model_dump(mode="json") for c in customers]

    if name == "crm_get_customer":
        cid = arguments["customer_id"]
        customer = db.get_customer(cid)
        if customer is None:
            return {"error": f"customer not found: {cid}"}
        return customer.model_dump(mode="json")

    if name == "crm_create_customer":
        customer = db.create_customer(
            _build_customer(arguments)
        )
        return customer.model_dump(mode="json")

    if name == "erp_get_inventory":
        sku = arguments["sku"]
        inv = db.get_inventory(sku)
        if inv is None:
            return {"error": f"inventory not found: {sku}"}
        return inv.model_dump(mode="json")

    if name == "erp_update_inventory":
        sku = arguments["sku"]
        inv_updated = db.update_inventory(
            sku,
            quantity=arguments.get("quantity"),
            product_name=arguments.get("product_name"),
            warehouse=arguments.get("warehouse"),
        )
        if inv_updated is None:
            return {"error": f"inventory not found: {sku}"}
        return inv_updated.model_dump(mode="json")

    raise ValueError(f"unknown tool: {name}")


def _build_customer(arguments: dict[str, Any]) -> Customer:
    """Construct a Customer from MCP arguments."""
    return Customer(
        name=arguments["name"],
        region=arguments["region"],
        tier=arguments.get("tier", "standard"),
        contact_email=arguments["contact_email"],
    )


@server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
async def list_tools() -> list[Tool]:
    return _tool_specs()


@server.call_tool()  # type: ignore[untyped-decorator]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    db = get_db()
    try:
        result = _call_tool_impl(name, arguments, db)
    except KeyError as exc:
        return _text({"error": f"missing required argument: {exc}"})
    except ValueError as exc:
        return _text({"error": str(exc)})
    return _text(result)


async def main() -> None:
    """Run the MCP server over stdio until the client disconnects."""
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

"""Mock SaaS — simulated ERP/CRM external system for EAOS Phase 7.

Standalone FastAPI service exposing REST API (orders/customers/inventory CRUD)
plus a standard MCP server wrapper (stdio). Used as the dev/test/demo target
for the MCP client and HTTP API connector.

Run the REST API::

    uvicorn mock_saas.main:app --host 0.0.0.0 --port 18000

Run the MCP server (stdio, launched as subprocess by McpClient)::

    python -m mock_saas.mcp_server
"""

__version__ = "0.1.0"

"""M2 integration tests for the data package.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.

Covers: Text2SQL (simple, join, injection block, self-correction),
MCP server (list tools, call tool, read resource).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from eaos.data.connector import DataConnector
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.infra.db.base import DbClient

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
DS_ERP = UUID("00000000-0000-0000-0000-000000000509")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")


def _llm_response(sql: str, explanation: str = "") -> str:
    """Build a JSON LLM response string for Text2SQL."""
    return json.dumps({"sql": sql, "explanation": explanation})


def _mock_llm(response_content: str) -> Any:
    """Build a mock LLMRouter returning a fixed response."""
    from eaos.infra.llm.base import LLMResponse

    llm: Any = MagicMock()
    llm.chat = AsyncMock(return_value=LLMResponse(content=response_content))
    return llm


def _make_connectors(db: DbClient) -> dict[str, DataConnector]:
    """Build real ERP + CRM connectors backed by the live DB."""
    from eaos.data.crm_connector import CrmConnector
    from eaos.data.erp_connector import ErpConnector

    connectors: dict[str, DataConnector] = {
        "erp": ErpConnector(db),
        "crm": CrmConnector(db),
    }
    return connectors


def _make_engine(db: DbClient, llm: Any) -> Text2SQLEngineImpl:
    """Build a Text2SQLEngineImpl with real connectors/validator/sandbox."""
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.data.text2sql.validator import SqlValidatorImpl
    from eaos.knowledge.ontology.repository import PgOntologyRepository

    return Text2SQLEngineImpl(
        llm=llm,
        ontology_repo=PgOntologyRepository(db),
        connectors=_make_connectors(db),
        validator=SqlValidatorImpl(db),
        sandbox=PgSqlSandbox(db),
        db=db,
    )


def _make_ctx() -> Any:
    """Build a TenantContext for the seed admin user."""
    from eaos.core.context import TenantContext

    return TenantContext(
        tenant_id=TID,
        user_id=USER_ADMIN,
        agent_id=AGENT_PERSONAL,
        agent_scope="personal",
    )


class TestText2SQL:
    async def test_text2sql_simple(self, db: DbClient) -> None:
        llm = _mock_llm(_llm_response(
            "SELECT name FROM erp.customers", "查询所有客户名称",
        ))
        engine = _make_engine(db, llm)
        ctx = _make_ctx()

        result = await engine.query("查询所有客户名称", ctx, DS_ERP)

        assert result.error is None
        assert "SELECT" in result.sql.upper()
        assert len(result.rows) > 0
        assert "name" in result.rows[0]

    async def test_text2sql_join(self, db: DbClient) -> None:
        join_sql = (
            "SELECT c.name, SUM(o.amount) AS total "
            "FROM erp.customers c "
            "JOIN erp.orders o ON c.id = o.customer_id "
            "GROUP BY c.name"
        )
        llm = _mock_llm(_llm_response(join_sql, "查询每个客户的订单总额"))
        engine = _make_engine(db, llm)
        ctx = _make_ctx()

        result = await engine.query("查询每个客户的订单总额", ctx, DS_ERP)

        assert result.error is None
        assert "JOIN" in result.sql.upper()
        assert len(result.rows) > 0

    async def test_text2sql_injection(self, db: DbClient) -> None:
        """DELETE statements must be blocked by the validator."""
        llm = _mock_llm(_llm_response(
            "DELETE FROM erp.customers", "删除客户数据",
        ))
        engine = _make_engine(db, llm)
        ctx = _make_ctx()

        result = await engine.query("DELETE FROM customers", ctx, DS_ERP)

        assert result.rows == []
        assert result.error is not None
        assert "DELETE" in result.error.upper() or "forbidden" in result.error.lower()

    async def test_text2sql_self_correct(self, db: DbClient) -> None:
        """First attempt generates DELETE (blocked); second generates SELECT."""
        from eaos.infra.llm.base import LLMResponse

        bad_resp = LLMResponse(content=_llm_response(
            "DELETE FROM erp.customers", "删除",
        ))
        good_resp = LLMResponse(content=_llm_response(
            "SELECT name FROM erp.customers", "查询客户名称",
        ))
        llm: Any = MagicMock()
        llm.chat = AsyncMock(side_effect=[bad_resp, good_resp])

        engine = _make_engine(db, llm)
        ctx = _make_ctx()

        result = await engine.query("查询客户", ctx, DS_ERP)

        assert result.error is None
        assert "SELECT" in result.sql.upper()
        assert len(result.rows) > 0
        assert llm.chat.call_count >= 2


class TestMCP:
    async def test_mcp_list_tools(self, db: DbClient) -> None:
        from eaos.data.mcp.server import McpServerImpl

        server = McpServerImpl(_make_connectors(db))
        tools = await server.list_tools(TID)

        assert len(tools) == 6  # 3 per connector
        names = {t["name"] for t in tools}
        assert "erp_list_resources" in names
        assert "erp_read" in names
        assert "erp_describe_schema" in names
        assert "crm_list_resources" in names
        assert "crm_read" in names
        assert "crm_describe_schema" in names

    async def test_mcp_call_tool(self, db: DbClient) -> None:
        from eaos.data.mcp.server import McpServerImpl

        server = McpServerImpl(_make_connectors(db))
        result = await server.call_tool("erp_list_resources", {}, TID)

        assert "resources" in result
        resource_names = {r["name"] for r in result["resources"]}
        assert "products" in resource_names
        assert "customers" in resource_names
        assert "orders" in resource_names
        assert "inventory" in resource_names

    async def test_mcp_read_resource(self, db: DbClient) -> None:
        from eaos.data.mcp.server import McpServerImpl

        server = McpServerImpl(_make_connectors(db))
        payload = await server.read_resource("erp://customers", TID)

        data = json.loads(payload)
        assert "rows" in data
        assert len(data["rows"]) > 0

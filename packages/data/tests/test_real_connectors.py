"""T10: Real data source connector integration tests.

Validates ErpConnector / CrmConnector against the project's own seeded
``erp.*`` / ``crm.*`` tables (simulating external data sources), and the
full Text2SQL chain: LLM SQL generation → SqlValidator → PgSqlSandbox
execution → row return.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.infra.db.base import DbClient

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
DS_ERP = UUID("00000000-0000-0000-0000-000000000509")
DS_CRM = UUID("00000000-0000-0000-0000-000000000510")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")


def _ctx() -> Any:
    from eaos.core.context import TenantContext

    return TenantContext(
        tenant_id=TID,
        user_id=USER_ADMIN,
        agent_id=AGENT_PERSONAL,
        agent_scope="personal",
    )


def _mock_llm(sql: str, explanation: str = "") -> Any:
    """Mock LLMRouter returning a fixed SQL JSON response."""
    from eaos.infra.llm.base import LLMResponse

    llm: Any = MagicMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(
            content=json.dumps({"sql": sql, "explanation": explanation})
        )
    )
    return llm


def _make_engine(db: DbClient, llm: Any) -> Text2SQLEngineImpl:
    from eaos.data.crm_connector import CrmConnector
    from eaos.data.erp_connector import ErpConnector
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.data.text2sql.validator import SqlValidatorImpl
    from eaos.knowledge.ontology.repository import PgOntologyRepository

    return Text2SQLEngineImpl(
        llm=llm,
        ontology_repo=PgOntologyRepository(db),
        connectors={"erp": ErpConnector(db), "crm": CrmConnector(db)},
        validator=SqlValidatorImpl(db),
        sandbox=PgSqlSandbox(db),
        db=db,
    )


# -- Connector read tests ----------------------------------------------------


class TestErpConnector:
    async def test_read_customers(self, db: DbClient) -> None:
        from eaos.data.connector import ReadQuery
        from eaos.data.erp_connector import ErpConnector

        connector = ErpConnector(db)
        result = await connector.read(TID, "customers", ReadQuery())

        assert result.total == 10
        assert len(result.rows) == 10
        assert "name" in result.rows[0]

    async def test_read_orders_with_limit(self, db: DbClient) -> None:
        from eaos.data.connector import ReadQuery
        from eaos.data.erp_connector import ErpConnector

        connector = ErpConnector(db)
        result = await connector.read(TID, "orders", ReadQuery(limit=5))

        row = await db.fetch_one(
            "SELECT COUNT(*) AS total FROM erp.orders WHERE tenant_id = :p0",
            TID,
        )
        assert row is not None
        expected_total = int(row["total"])
        assert expected_total > 0
        assert result.total == expected_total
        assert len(result.rows) == min(5, expected_total)

    async def test_read_customers_with_filter(self, db: DbClient) -> None:
        from eaos.data.connector import ReadQuery
        from eaos.data.erp_connector import ErpConnector

        connector = ErpConnector(db)
        result = await connector.read(
            TID, "customers", ReadQuery(filters={"credit_limit": 0})
        )

        # Seed includes exactly 1 customer with credit_limit=0.
        assert result.total == 1
        assert len(result.rows) == 1

    async def test_describe_schema(self, db: DbClient) -> None:
        from eaos.data.erp_connector import ErpConnector

        connector = ErpConnector(db)
        schema = await connector.describe_schema(TID, "customers")

        assert schema.table_name == "erp.customers"
        assert len(schema.columns) > 0
        assert any(c["name"] == "name" for c in schema.columns)

    async def test_list_resources(self, db: DbClient) -> None:
        from eaos.data.erp_connector import ErpConnector

        connector = ErpConnector(db)
        resources = await connector.list_resources(TID)

        names = {r.name for r in resources}
        assert "customers" in names
        assert "orders" in names
        assert "products" in names


class TestCrmConnector:
    async def test_read_leads(self, db: DbClient) -> None:
        from eaos.data.connector import ReadQuery
        from eaos.data.crm_connector import CrmConnector

        connector = CrmConnector(db)
        result = await connector.read(TID, "leads", ReadQuery())

        assert result.total == 10
        assert len(result.rows) == 10

    async def test_read_opportunities(self, db: DbClient) -> None:
        from eaos.data.connector import ReadQuery
        from eaos.data.crm_connector import CrmConnector

        connector = CrmConnector(db)
        result = await connector.read(TID, "opportunities", ReadQuery())

        assert result.total == 10
        assert len(result.rows) == 10

    async def test_list_resources(self, db: DbClient) -> None:
        from eaos.data.crm_connector import CrmConnector

        connector = CrmConnector(db)
        resources = await connector.list_resources(TID)

        names = {r.name for r in resources}
        assert "leads" in names
        assert "opportunities" in names


# -- Text2SQL end-to-end with real DB ----------------------------------------


class TestText2SQLEndToEnd:
    async def test_simple_select(self, db: DbClient) -> None:
        llm = _mock_llm("SELECT name FROM erp.customers", "查询客户名称")
        engine = _make_engine(db, llm)
        ctx = _ctx()

        result = await engine.query("查询所有客户名称", ctx, DS_ERP)

        assert result.error is None
        assert "SELECT" in result.sql.upper()
        assert len(result.rows) == 10
        assert "name" in result.rows[0]

    async def test_join_query(self, db: DbClient) -> None:
        join_sql = (
            "SELECT c.name, SUM(o.amount) AS total "
            "FROM erp.customers c "
            "JOIN erp.orders o ON c.id = o.customer_id "
            "GROUP BY c.name"
        )
        llm = _mock_llm(join_sql, "客户订单总额")
        engine = _make_engine(db, llm)
        ctx = _ctx()

        result = await engine.query("查询每个客户的订单总额", ctx, DS_ERP)

        assert result.error is None
        assert "JOIN" in result.sql.upper()
        assert len(result.rows) > 0

    async def test_injection_blocked(self, db: DbClient) -> None:
        llm = _mock_llm("DELETE FROM erp.customers", "删除")
        engine = _make_engine(db, llm)
        ctx = _ctx()

        result = await engine.query("删除客户", ctx, DS_ERP)

        assert result.rows == []
        assert result.error is not None
        assert "DELETE" in result.error.upper() or "forbidden" in result.error.lower()

    async def test_self_correction(self, db: DbClient) -> None:
        from eaos.infra.llm.base import LLMResponse

        bad = LLMResponse(content=json.dumps(
            {"sql": "DELETE FROM erp.customers", "explanation": "删除"}
        ))
        good = LLMResponse(content=json.dumps(
            {"sql": "SELECT name FROM erp.customers", "explanation": "查询"}
        ))
        llm: Any = MagicMock()
        llm.chat = AsyncMock(side_effect=[bad, good])

        engine = _make_engine(db, llm)
        ctx = _ctx()

        result = await engine.query("查询客户", ctx, DS_ERP)

        assert result.error is None
        assert "SELECT" in result.sql.upper()
        assert len(result.rows) > 0
        assert llm.chat.call_count >= 2

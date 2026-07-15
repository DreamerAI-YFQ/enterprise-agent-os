"""Unit tests for Text2SQLEngineImpl — mock LLMRouter + all dependencies."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from eaos.core.context import TenantContext
from eaos.data.connector import DataResource, SchemaDescription
from eaos.data.text2sql.engine import Text2SQLEngineImpl
from eaos.data.text2sql.validator import ValidationResult
from eaos.infra.llm.base import LLMResponse

TID = UUID("00000000-0000-0000-0000-000000000001")
UID = UUID("00000000-0000-0000-0000-000000000201")
AID = UUID("00000000-0000-0000-0000-000000000301")
DS_ID = UUID("00000000-0000-0000-0000-000000000509")


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TID, user_id=UID, agent_id=AID, agent_scope="personal"
    )


def _llm_response(sql: str = "SELECT 1", explanation: str = "test") -> LLMResponse:
    return LLMResponse(content=json.dumps({"sql": sql, "explanation": explanation}))


def _schema_desc(table: str = "erp.products") -> SchemaDescription:
    return SchemaDescription(
        table_name=table,
        columns=[
            {"name": "id", "type": "uuid", "nullable": False, "comment": None},
            {"name": "name", "type": "varchar", "nullable": False, "comment": "名称"},
        ],
        relations=[],
        sample_rows=[{"id": "x", "name": "sample"}],
    )


def _make_engine(
    *,
    connector_name: str = "erp",
    llm_side_effect: list[Any] | None = None,
    llm_return: Any | None = None,
    validate_side_effect: list[Any] | None = None,
    validate_return: Any | None = None,
    sandbox_rows: list[dict[str, Any]] | None = None,
) -> tuple[Text2SQLEngineImpl, dict[str, Any]]:
    """Build an engine with mocked dependencies. Returns (engine, mocks)."""
    llm: Any = MagicMock()
    llm.chat = AsyncMock()
    if llm_side_effect is not None:
        llm.chat.side_effect = llm_side_effect
    elif llm_return is not None:
        llm.chat.return_value = llm_return
    else:
        llm.chat.return_value = _llm_response()

    ontology_repo: Any = MagicMock()
    ontology_repo.get_schema_mapping = AsyncMock(return_value={})

    connector: Any = MagicMock()
    connector.list_resources = AsyncMock(
        return_value=[DataResource("products", "产品", "表", "read")]
    )
    connector.describe_schema = AsyncMock(return_value=_schema_desc())

    validator: Any = MagicMock()
    validator.validate = AsyncMock()
    if validate_side_effect is not None:
        validator.validate.side_effect = validate_side_effect
    elif validate_return is not None:
        validator.validate.return_value = validate_return
    else:
        validator.validate.return_value = ValidationResult(valid=True)

    sandbox: Any = MagicMock()
    default_rows = [{"id": "1"}]
    sandbox.execute = AsyncMock(
        return_value=sandbox_rows if sandbox_rows is not None else default_rows
    )

    db: Any = MagicMock()
    db.tenant_scoped_fetch = AsyncMock(
        return_value=[{"connection": {"connector": connector_name}}]
    )
    db.execute = AsyncMock()

    engine = Text2SQLEngineImpl(
        llm=llm,
        ontology_repo=ontology_repo,
        connectors={"erp": connector, "crm": connector},
        validator=validator,
        sandbox=sandbox,
        db=db,
    )
    return engine, {
        "llm": llm,
        "ontology_repo": ontology_repo,
        "connector": connector,
        "validator": validator,
        "sandbox": sandbox,
        "db": db,
    }


class TestSuccessfulQuery:
    async def test_returns_rows_and_sql(self) -> None:
        rows = [{"id": "1", "name": "Alice"}]
        engine, mocks = _make_engine(
            llm_return=_llm_response(sql="SELECT * FROM erp.customers"),
            sandbox_rows=rows,
        )
        result = await engine.query("show all customers", _ctx(), DS_ID)

        assert result.rows == rows
        assert result.sql == "SELECT * FROM erp.customers"
        assert result.explanation == "test"
        assert result.error is None
        assert result.truncated is False

    async def test_explanation_none_when_missing(self) -> None:
        engine, _ = _make_engine(
            llm_return=LLMResponse(content=json.dumps({"sql": "SELECT 1"})),
        )
        result = await engine.query("q", _ctx(), DS_ID)
        assert result.explanation is None

    async def test_records_history_on_success(self) -> None:
        engine, mocks = _make_engine()
        await engine.query("q", _ctx(), DS_ID)

        mocks["db"].execute.assert_called_once()
        call_args = mocks["db"].execute.call_args
        sql_arg = call_args.args[0]
        assert "INSERT INTO data.query_history" in sql_arg
        params = call_args.args[1:]
        assert params[4] == "SELECT 1"  # generated_sql
        assert params[5] is True  # executed
        assert params[6] is True  # success
        assert params[7] == 1  # result_count


class TestSelfCorrection:
    async def test_retries_on_validation_failure(self) -> None:
        engine, mocks = _make_engine(
            llm_side_effect=[
                _llm_response(sql="DELETE FROM erp.customers"),
                _llm_response(sql="SELECT * FROM erp.customers"),
            ],
            validate_side_effect=[
                ValidationResult(valid=False, reason="forbidden keyword: DELETE"),
                ValidationResult(valid=True),
            ],
        )
        result = await engine.query("q", _ctx(), DS_ID)

        assert result.error is None
        assert result.sql == "SELECT * FROM erp.customers"
        assert mocks["llm"].chat.call_count == 2
        assert mocks["validator"].validate.call_count == 2

    async def test_llm_invalid_json_then_valid(self) -> None:
        engine, mocks = _make_engine(
            llm_side_effect=[
                LLMResponse(content="not json"),
                _llm_response(sql="SELECT 1"),
            ],
        )
        result = await engine.query("q", _ctx(), DS_ID)

        assert result.error is None
        assert result.sql == "SELECT 1"
        assert mocks["llm"].chat.call_count == 2

    async def test_all_retries_exhausted(self) -> None:
        bad_response = _llm_response(sql="DELETE FROM x")
        engine, mocks = _make_engine(
            llm_side_effect=[bad_response, bad_response, bad_response, bad_response],
            validate_return=ValidationResult(valid=False, reason="forbidden keyword: DELETE"),
        )
        result = await engine.query("q", _ctx(), DS_ID)

        assert result.rows == []
        assert result.error is not None
        assert "forbidden keyword" in result.error
        assert mocks["llm"].chat.call_count == 4

    async def test_error_feedback_in_prompt(self) -> None:
        engine, mocks = _make_engine(
            llm_side_effect=[
                _llm_response(sql="DELETE FROM x"),
                _llm_response(sql="SELECT 1"),
            ],
            validate_side_effect=[
                ValidationResult(valid=False, reason="forbidden keyword: DELETE"),
                ValidationResult(valid=True),
            ],
        )
        await engine.query("q", _ctx(), DS_ID)

        second_call = mocks["llm"].chat.call_args_list[1]
        user_msg = second_call.args[0][1].content
        assert "上次错误" in user_msg
        assert "forbidden keyword: DELETE" in user_msg

    def test_max_retries_is_three(self) -> None:
        assert Text2SQLEngineImpl.MAX_RETRIES == 3


class TestErrorPaths:
    async def test_datasource_not_found(self) -> None:
        engine, mocks = _make_engine()
        mocks["db"].tenant_scoped_fetch.return_value = []

        result = await engine.query("q", _ctx(), DS_ID)

        assert result.rows == []
        assert result.error is not None
        assert "not found" in result.error
        mocks["sandbox"].execute.assert_not_called()

    async def test_unknown_connector(self) -> None:
        engine, mocks = _make_engine(connector_name="oracle")

        result = await engine.query("q", _ctx(), DS_ID)

        assert result.rows == []
        assert result.error is not None
        assert "unknown connector" in result.error
        mocks["sandbox"].execute.assert_not_called()

    async def test_no_connector_in_connection(self) -> None:
        engine, mocks = _make_engine()
        mocks["db"].tenant_scoped_fetch.return_value = [{"connection": {}}]

        result = await engine.query("q", _ctx(), DS_ID)

        assert result.error is not None
        assert "no 'connector'" in result.error

    async def test_records_history_on_failure(self) -> None:
        engine, mocks = _make_engine()
        mocks["db"].tenant_scoped_fetch.return_value = []

        await engine.query("q", _ctx(), DS_ID)

        mocks["db"].execute.assert_called_once()
        params = mocks["db"].execute.call_args.args[1:]
        assert params[5] is False  # executed
        assert params[6] is False  # success


class TestSchemaContext:
    async def test_describes_all_resources(self) -> None:
        engine, mocks = _make_engine()
        mocks["connector"].list_resources.return_value = [
            DataResource("products", "产品", "表", "read"),
            DataResource("customers", "客户", "表", "read"),
        ]
        mocks["connector"].describe_schema.return_value = _schema_desc()

        await engine.query("q", _ctx(), DS_ID)

        assert mocks["connector"].describe_schema.call_count == 2

    async def test_ontology_mapping_in_prompt(self) -> None:
        engine, mocks = _make_engine()
        mocks["ontology_repo"].get_schema_mapping.return_value = {
            "erp.products": {"name": {"chinese_name": "产品名称", "type": "varchar"}}
        }

        await engine.query("q", _ctx(), DS_ID)

        call = mocks["llm"].chat.call_args
        user_msg = call.args[0][1].content
        assert "产品名称" in user_msg

    async def test_schema_text_contains_table_and_columns(self) -> None:
        engine, mocks = _make_engine()
        mocks["connector"].describe_schema.return_value = SchemaDescription(
            table_name="erp.orders",
            columns=[
                {"name": "order_no", "type": "varchar", "nullable": False, "comment": "订单号"},
            ],
            relations=[],
            sample_rows=[],
        )

        await engine.query("q", _ctx(), DS_ID)

        user_msg = mocks["llm"].chat.call_args.args[0][1].content
        assert "erp.orders" in user_msg
        assert "order_no" in user_msg

    async def test_sample_rows_in_prompt(self) -> None:
        engine, mocks = _make_engine()
        mocks["connector"].describe_schema.return_value = SchemaDescription(
            table_name="erp.products",
            columns=[{"name": "id", "type": "uuid", "nullable": False, "comment": None}],
            relations=[],
            sample_rows=[{"id": "abc-123", "name": "Widget"}],
        )

        await engine.query("q", _ctx(), DS_ID)

        user_msg = mocks["llm"].chat.call_args.args[0][1].content
        assert "示例数据" in user_msg
        assert "abc-123" in user_msg


class TestEmptyResults:
    async def test_valid_query_returns_empty_rows(self) -> None:
        engine, mocks = _make_engine(sandbox_rows=[])

        result = await engine.query("q", _ctx(), DS_ID)

        assert result.rows == []
        assert result.error is None
        params = mocks["db"].execute.call_args.args[1:]
        assert params[7] == 0  # result_count

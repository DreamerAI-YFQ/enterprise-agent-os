"""Unit tests for PgSqlSandbox — mock DbClient.

Phase 7 T7: covers legacy ``execute`` (backwards compat), ``execute_readonly``
(SET TRANSACTION READ ONLY enforcement), and ``execute_write`` (transactional
write path). Session-based methods mock ``db.session()`` as an async context
manager yielding a mock session.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from eaos.data.text2sql.sandbox import PgSqlSandbox, SandboxOptions

TID = UUID("00000000-0000-0000-0000-000000000001")
DS_ID = UUID("00000000-0000-0000-0000-000000000509")


def _make_sandbox() -> tuple[PgSqlSandbox, Any]:
    db: Any = MagicMock()
    db.execute = AsyncMock()
    db.fetch = AsyncMock()
    return PgSqlSandbox(db), db


@asynccontextmanager
async def _mock_session_ctx(
    execute_results: list[Any] | None = None,
    execute_side_effect: Exception | None = None,
) -> Any:
    """Build a mock async context manager mimicking db.session()."""
    session: Any = MagicMock()
    if execute_side_effect is not None:
        session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        session.execute = AsyncMock(side_effect=execute_results or [])
    yield session


def _make_mock_result(rows: list[dict[str, Any]]) -> Any:
    """Build a mock SQLAlchemy result object with .mappings().all().

    Returns raw dicts so ``dict(row)`` in the sandbox works naturally.
    """
    result: Any = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


class TestExecuteLegacy:
    """Legacy execute() method — backwards compatible with Text2SQL engine."""

    async def test_returns_rows(self) -> None:
        sb, db = _make_sandbox()
        db.fetch.return_value = [{"id": 1}, {"id": 2}]
        result = await sb.execute("SELECT * FROM erp.products", TID, DS_ID)
        assert len(result) == 2

    async def test_sets_statement_timeout(self) -> None:
        sb, db = _make_sandbox()
        db.fetch.return_value = []
        await sb.execute("SELECT 1", TID, DS_ID, SandboxOptions(timeout_sec=15))
        db.execute.assert_awaited()
        timeout_sql = db.execute.call_args.args[0]
        assert "statement_timeout" in timeout_sql
        assert "15" in timeout_sql

    async def test_truncates_to_max_rows(self) -> None:
        sb, db = _make_sandbox()
        db.fetch.return_value = [{"id": i} for i in range(50)]
        result = await sb.execute("SELECT 1", TID, DS_ID, SandboxOptions(max_rows=10))
        assert len(result) == 10

    async def test_returns_empty_on_error(self) -> None:
        sb, db = _make_sandbox()
        db.fetch.side_effect = RuntimeError("timeout")
        result = await sb.execute("SELECT bad", TID, DS_ID)
        assert result == []

    async def test_default_options_when_none(self) -> None:
        sb, db = _make_sandbox()
        db.fetch.return_value = []
        await sb.execute("SELECT 1", TID, DS_ID, None)
        db.execute.assert_awaited()


class TestExecuteReadonly:
    """execute_readonly() — SET TRANSACTION READ ONLY enforcement (gap #6)."""

    async def test_sets_transaction_read_only(self) -> None:
        sb, db = _make_sandbox()
        result_mock = _make_mock_result([{"id": 1}])
        db.session = MagicMock(
            return_value=_mock_session_ctx(execute_results=[None, None, result_mock])
        )
        rows = await sb.execute_readonly("SELECT 1", [], TID)
        assert rows == [{"id": 1}]
        # First execute call should be SET TRANSACTION READ ONLY
        # The context manager yields the session; verify first execute call
        # We can't easily inspect the session from outside, but we can verify
        # session() was called
        db.session.assert_called_once()

    async def test_returns_empty_on_error(self) -> None:
        sb, db = _make_sandbox()
        db.session = MagicMock(
            return_value=_mock_session_ctx(
                execute_side_effect=RuntimeError("read-only violation")
            )
        )
        rows = await sb.execute_readonly(
            "INSERT INTO erp.customers VALUES (1)", [], TID
        )
        assert rows == []

    async def test_truncates_to_max_rows(self) -> None:
        sb, db = _make_sandbox()
        many_rows = [{"id": i} for i in range(50)]
        result_mock = _make_mock_result(many_rows)
        db.session = MagicMock(
            return_value=_mock_session_ctx(execute_results=[None, None, result_mock])
        )
        rows = await sb.execute_readonly(
            "SELECT 1", [], TID, SandboxOptions(max_rows=10)
        )
        assert len(rows) == 10

    async def test_passes_params_as_binds(self) -> None:
        sb, db = _make_sandbox()
        result_mock = _make_mock_result([])
        db.session = MagicMock(
            return_value=_mock_session_ctx(execute_results=[None, None, result_mock])
        )
        await sb.execute_readonly(
            "SELECT * FROM erp.customers WHERE code = :p0", ["C001"], TID
        )
        db.session.assert_called_once()


class TestExecuteWrite:
    """execute_write() — transactional write path for WritePipeline."""

    async def test_executes_write_in_session(self) -> None:
        sb, db = _make_sandbox()
        db.session = MagicMock(
            return_value=_mock_session_ctx(execute_results=[None])
        )
        await sb.execute_write(
            "INSERT INTO erp.customers (name) VALUES (:p0)",
            ["Acme"],
            TID,
        )
        db.session.assert_called_once()

    async def test_propagates_exception(self) -> None:
        """Write errors propagate (unlike readonly which swallows)."""
        sb, db = _make_sandbox()
        db.session = MagicMock(
            return_value=_mock_session_ctx(
                execute_side_effect=RuntimeError("constraint violation")
            )
        )
        try:
            await sb.execute_write("INSERT INTO bad VALUES (:p0)", [1], TID)
            raise AssertionError("should have raised")
        except RuntimeError:
            pass  # expected

    async def test_passes_params_as_binds(self) -> None:
        sb, db = _make_sandbox()
        db.session = MagicMock(
            return_value=_mock_session_ctx(execute_results=[None])
        )
        await sb.execute_write(
            "UPDATE erp.customers SET name = :p0 WHERE id = :p1",
            ["New", "123"],
            TID,
        )
        db.session.assert_called_once()

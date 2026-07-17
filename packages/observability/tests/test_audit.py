"""Tests for AuditLogger — write operation audit trail.

Unit tests mock ``DbClient`` to verify log/log_rollback/query/get operations
against the ``harness.write_audit`` table. Verifies SQL construction, param
binding, and row-to-entry conversion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.observability.audit import AuditEntry, AuditLogger, AuditQuery

TID = UUID("00000000-0000-0000-0000-000000000001")
PRINCIPAL = UUID("00000000-0000-0000-0000-000000000201")


def _mock_db(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetch_one_row: dict[str, Any] | None = None,
) -> Any:
    db: Any = MagicMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=fetch_one_row)
    db.execute = AsyncMock(return_value=None)
    return db


def _entry(**overrides: Any) -> AuditEntry:
    base: dict[str, Any] = {
        "tenant_id": TID,
        "principal_id": PRINCIPAL,
        "tool_name": "erp.write",
        "resource": "customers",
        "operation": "create",
        "success": True,
    }
    base.update(overrides)
    return AuditEntry(**base)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": TID,
        "principal_id": PRINCIPAL,
        "tool_name": "erp.write",
        "resource": "customers",
        "operation": "create",
        "before_state": None,
        "after_state": {"name": "Acme"},
        "approval_id": None,
        "trace_id": None,
        "success": True,
        "error": None,
        "rolled_back": False,
        "rollback_reason": None,
        "created_at": datetime(2026, 7, 1, 12, 0, 0),
    }
    base.update(overrides)
    return base


class TestAuditLog:
    async def test_log_inserts_into_write_audit(self) -> None:
        db = _mock_db()
        logger = AuditLogger(db)
        entry = _entry(
            before={"name": "Old"},
            after={"name": "New"},
            approval_id=uuid4(),
            trace_id=uuid4(),
            error=None,
        )
        entry_id = await logger.log(entry)
        assert isinstance(entry_id, UUID)

        db.execute.assert_called_once()
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO harness.write_audit" in sql
        assert "before_state" in sql
        assert "after_state" in sql
        assert "approval_id" in sql
        assert "trace_id" in sql

    async def test_log_serializes_before_after_as_json(self) -> None:
        db = _mock_db()
        logger = AuditLogger(db)
        await logger.log(_entry(before={"name": "Old"}, after={"name": "New"}))
        call = db.execute.call_args
        # before_state is arg index 7 (after id, tenant, principal, tool, resource, op)
        before_arg = call.args[7]
        assert isinstance(before_arg, str)  # JSON string
        assert "Old" in before_arg

    async def test_log_none_before_passes_none(self) -> None:
        db = _mock_db()
        logger = AuditLogger(db)
        await logger.log(_entry(before=None))
        call = db.execute.call_args
        before_arg = call.args[7]
        assert before_arg is None

    async def test_log_failed_write_records_error(self) -> None:
        db = _mock_db()
        logger = AuditLogger(db)
        await logger.log(_entry(success=False, error="constraint violation"))
        call = db.execute.call_args
        success_arg = call.args[13]
        error_arg = call.args[14]
        assert success_arg is False
        assert error_arg == "constraint violation"


class TestLogRollback:
    async def test_updates_rolled_back_flag(self) -> None:
        db = _mock_db()
        logger = AuditLogger(db)
        entry_id = uuid4()
        await logger.log_rollback(entry_id, "manual rollback by admin")
        db.execute.assert_called_once()
        sql = db.execute.call_args.args[0]
        assert "UPDATE harness.write_audit" in sql
        assert "rolled_back = TRUE" in sql
        assert "rollback_reason" in sql
        assert db.execute.call_args.args[1] == entry_id
        assert db.execute.call_args.args[2] == "manual rollback by admin"


class TestQuery:
    async def test_basic_query(self) -> None:
        row = _row()
        db = _mock_db(fetch_rows=[row])
        logger = AuditLogger(db)
        results = await logger.query(TID)
        assert len(results) == 1
        entry = results[0]
        assert entry.tenant_id == TID
        assert entry.tool_name == "erp.write"
        assert entry.resource == "customers"
        assert entry.operation == "create"
        assert entry.success is True

    async def test_query_with_principal_filter(self) -> None:
        row = _row()
        db = _mock_db(fetch_rows=[row])
        logger = AuditLogger(db)
        principal = uuid4()
        await logger.query(TID, AuditQuery(principal_id=principal))
        sql = db.fetch.call_args.args[0]
        assert "principal_id = :p1" in sql
        assert db.fetch.call_args.args[2] == principal

    async def test_query_with_resource_filter(self) -> None:
        db = _mock_db(fetch_rows=[])
        logger = AuditLogger(db)
        await logger.query(TID, AuditQuery(resource="orders"))
        sql = db.fetch.call_args.args[0]
        assert "resource = :p" in sql

    async def test_query_with_time_range(self) -> None:
        db = _mock_db(fetch_rows=[])
        logger = AuditLogger(db)
        start = datetime(2026, 7, 1, 0, 0, 0)
        end = datetime(2026, 7, 2, 0, 0, 0)
        await logger.query(TID, AuditQuery(time_range=(start, end)))
        sql = db.fetch.call_args.args[0]
        assert "created_at >=" in sql
        assert "created_at <=" in sql

    async def test_query_orders_by_created_at_desc(self) -> None:
        db = _mock_db(fetch_rows=[])
        logger = AuditLogger(db)
        await logger.query(TID)
        sql = db.fetch.call_args.args[0]
        assert "ORDER BY created_at DESC" in sql

    async def test_query_applies_limit_offset(self) -> None:
        db = _mock_db(fetch_rows=[])
        logger = AuditLogger(db)
        await logger.query(TID, AuditQuery(limit=50, offset=100))
        sql = db.fetch.call_args.args[0]
        assert "LIMIT" in sql
        assert "OFFSET" in sql


class TestGet:
    async def test_get_returns_entry(self) -> None:
        row = _row()
        db = _mock_db(fetch_one_row=row)
        logger = AuditLogger(db)
        entry = await logger.get(row["id"])
        assert entry is not None
        assert entry.id == row["id"]
        assert entry.resource == "customers"

    async def test_get_returns_none_if_not_found(self) -> None:
        db = _mock_db(fetch_one_row=None)
        logger = AuditLogger(db)
        result = await logger.get(uuid4())
        assert result is None


class TestRowConversion:
    async def test_rolled_back_entry_has_flag(self) -> None:
        row = _row(rolled_back=True, rollback_reason="test failure")
        db = _mock_db(fetch_rows=[row])
        logger = AuditLogger(db)
        results = await logger.query(TID)
        assert results[0].rolled_back is True
        assert results[0].rollback_reason == "test failure"

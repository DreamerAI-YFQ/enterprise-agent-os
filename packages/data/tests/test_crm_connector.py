"""Unit tests for CrmConnector — mock DbClient.

Phase 7 T6: covers read with tenant isolation, write (create/update/delete)
with parameterized SQL + access_mode enforcement, and real rollback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.data.connector import (
    DataResult,
    ReadQuery,
    SchemaDescription,
    WriteOperation,
)
from eaos.data.crm_connector import CrmConnector

TID = UUID("00000000-0000-0000-0000-000000000001")


def _make_connector() -> tuple[CrmConnector, Any]:
    db: Any = MagicMock()
    db.fetch = AsyncMock()
    db.fetch_one = AsyncMock()
    db.execute = AsyncMock()
    return CrmConnector(db), db


class TestListResources:
    async def test_returns_three_resources(self) -> None:
        c, _ = _make_connector()
        resources = await c.list_resources(TID)
        assert len(resources) == 3
        names = [r.name for r in resources]
        assert set(names) == {"leads", "opportunities", "activities"}

    async def test_all_resources_are_writable(self) -> None:
        c, _ = _make_connector()
        resources = await c.list_resources(TID)
        assert all(r.access_mode == "read_write" for r in resources)


class TestRead:
    async def test_basic_query(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = [{"id": 1, "lead_name": "Test Lead"}]
        db.fetch_one.return_value = {"total": 1}
        result = await c.read(TID, "leads", ReadQuery(limit=10))
        assert isinstance(result, DataResult)
        assert len(result.rows) == 1
        sql = db.fetch.call_args.args[0]
        assert "crm.leads" in sql

    async def test_filters_applied(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        query = ReadQuery(filters={"status": "converted"}, limit=10)
        await c.read(TID, "leads", query)
        sql = db.fetch.call_args.args[0]
        assert "WHERE" in sql
        assert "status = :p1" in sql  # p0 is tenant_id

    async def test_tenant_id_is_first_param(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        await c.read(TID, "leads", ReadQuery())
        sql = db.fetch.call_args.args[0]
        assert "tenant_id = :p0" in sql
        assert db.fetch.call_args.args[1] == TID


class TestWriteAccessMode:
    async def test_unknown_resource_rejected(self) -> None:
        c, _ = _make_connector()
        result = await c.write(
            TID,
            "nonexistent",
            WriteOperation(operation="create", data={"lead_name": "x"}),
        )
        assert not result.success
        assert "unknown resource" in (result.error or "")


class TestWriteCreate:
    async def test_create_executes_insert(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "leads",
            WriteOperation(
                operation="create",
                data={"lead_name": "New Lead", "source": "web", "status": "new"},
            ),
        )
        assert result.success
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO crm.leads" in sql
        assert "tenant_id" in sql
        assert "New Lead" not in sql  # parameterized

    async def test_create_with_disallowed_column_rejected(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "leads",
            WriteOperation(
                operation="create",
                data={"id": "123", "lead_name": "x"},  # id not whitelisted
            ),
        )
        assert not result.success
        assert "disallowed" in (result.error or "")


class TestWriteUpdate:
    async def test_update_fetches_before_and_executes(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before_row: dict[str, Any] = {"id": record_id, "status": "new"}
        after_row: dict[str, Any] = {"id": record_id, "status": "contacted"}
        db.fetch_one.side_effect = [before_row, after_row]
        result = await c.write(
            TID,
            "leads",
            WriteOperation(
                operation="update",
                record_id=record_id,
                data={"status": "contacted"},
            ),
        )
        assert result.success
        assert result.before == before_row
        assert result.after == after_row
        sql = db.execute.call_args.args[0]
        assert "UPDATE crm.leads" in sql
        assert "WHERE tenant_id = :p1 AND id = :p2" in sql

    async def test_update_record_not_found(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "leads",
            WriteOperation(
                operation="update",
                record_id=str(uuid4()),
                data={"status": "contacted"},
            ),
        )
        assert not result.success


class TestWriteDelete:
    async def test_delete_fetches_before_and_executes(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before_row: dict[str, Any] = {"id": record_id, "lead_name": "x"}
        db.fetch_one.return_value = before_row
        result = await c.write(
            TID,
            "leads",
            WriteOperation(operation="delete", record_id=record_id),
        )
        assert result.success
        assert result.before == before_row
        assert result.after is None
        sql = db.execute.call_args.args[0]
        assert "DELETE FROM crm.leads" in sql
        assert "WHERE tenant_id = :p0 AND id = :p1" in sql


class TestTenantIsolation:
    async def test_create_includes_tenant_id(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        await c.write(
            TID,
            "leads",
            WriteOperation(
                operation="create",
                data={"lead_name": "x", "source": "web", "status": "new"},
            ),
        )
        sql = db.execute.call_args.args[0]
        assert "tenant_id" in sql
        assert db.execute.call_args.args[1] == TID


class TestRollback:
    async def test_rollback_create_deletes_row(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        await c.rollback(
            TID,
            {
                "operation": "create",
                "resource": "leads",
                "record_id": record_id,
                "before": None,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "DELETE FROM crm.leads" in sql
        assert "WHERE tenant_id = :p0 AND id = :p1" in sql

    async def test_rollback_update_restores_before(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before: dict[str, Any] = {"status": "new", "lead_name": "Old"}
        await c.rollback(
            TID,
            {
                "operation": "update",
                "resource": "leads",
                "record_id": record_id,
                "before": before,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "UPDATE crm.leads" in sql

    async def test_rollback_delete_reinserts_row(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before: dict[str, Any] = {"lead_name": "Deleted", "source": "web", "status": "new"}
        await c.rollback(
            TID,
            {
                "operation": "delete",
                "resource": "leads",
                "record_id": record_id,
                "before": before,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO crm.leads" in sql
        assert "tenant_id" in sql


class TestDescribeSchema:
    async def test_returns_schema_with_crm_prefix(self) -> None:
        c, db = _make_connector()
        db.fetch.side_effect = [
            [{"column_name": "id", "data_type": "uuid", "is_nullable": "NO", "comment": None}],
            [],
        ]
        result = await c.describe_schema(TID, "leads")
        assert isinstance(result, SchemaDescription)
        assert result.table_name == "crm.leads"

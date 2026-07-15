"""Unit tests for ErpConnector — mock DbClient.

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
from eaos.data.erp_connector import ErpConnector

TID = UUID("00000000-0000-0000-0000-000000000001")


def _make_connector() -> tuple[ErpConnector, Any]:
    db: Any = MagicMock()
    db.fetch = AsyncMock()
    db.fetch_one = AsyncMock()
    db.execute = AsyncMock()
    return ErpConnector(db), db


class TestListResources:
    async def test_returns_four_resources(self) -> None:
        c, _ = _make_connector()
        resources = await c.list_resources(TID)
        assert len(resources) == 4
        names = [r.name for r in resources]
        assert set(names) == {"products", "customers", "orders", "inventory"}

    async def test_products_is_read_only(self) -> None:
        c, _ = _make_connector()
        resources = await c.list_resources(TID)
        products = next(r for r in resources if r.name == "products")
        assert products.access_mode == "read"

    async def test_orders_customers_inventory_are_writable(self) -> None:
        c, _ = _make_connector()
        resources = await c.list_resources(TID)
        for name in ("orders", "customers", "inventory"):
            res = next(r for r in resources if r.name == name)
            assert res.access_mode == "read_write"


class TestRead:
    async def test_basic_query(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = [{"id": 1, "name": "Widget"}]
        db.fetch_one.return_value = {"total": 1}
        query = ReadQuery(limit=10, offset=0)
        result = await c.read(TID, "products", query)
        assert isinstance(result, DataResult)
        assert len(result.rows) == 1
        assert result.total == 1
        sql = db.fetch.call_args.args[0]
        assert "erp.products" in sql
        assert "LIMIT" in sql

    async def test_selects_specific_fields(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        query = ReadQuery(fields=["name", "sku"], limit=5)
        await c.read(TID, "products", query)
        sql = db.fetch.call_args.args[0]
        assert "name, sku" in sql

    async def test_filters_applied(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        query = ReadQuery(filters={"status": "active"}, limit=10)
        await c.read(TID, "products", query)
        sql = db.fetch.call_args.args[0]
        assert "WHERE" in sql
        assert "status = :p1" in sql  # p0 is tenant_id

    async def test_order_by_applied(self) -> None:
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        query = ReadQuery(order_by=[("name", "asc")], limit=10)
        await c.read(TID, "products", query)
        sql = db.fetch.call_args.args[0]
        assert "ORDER BY" in sql
        assert "name asc" in sql

    async def test_tenant_id_is_first_param(self) -> None:
        """Gap #7: tenant_id must be the first parameter for isolation."""
        c, db = _make_connector()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"total": 0}
        await c.read(TID, "customers", ReadQuery())
        sql = db.fetch.call_args.args[0]
        assert "tenant_id = :p0" in sql
        # First param after SQL must be the tenant_id
        first_param = db.fetch.call_args.args[1]
        assert first_param == TID

    async def test_unknown_resource_returns_empty(self) -> None:
        c, db = _make_connector()
        result = await c.read(TID, "nonexistent", ReadQuery())
        assert result.rows == []
        assert result.total == 0
        db.fetch.assert_not_called()


class TestWriteAccessMode:
    async def test_products_read_only_rejected(self) -> None:
        c, _ = _make_connector()
        result = await c.write(
            TID, "products", WriteOperation(operation="create", data={"name": "x"})
        )
        assert not result.success
        assert "read-only" in (result.error or "")

    async def test_unknown_resource_rejected(self) -> None:
        c, _ = _make_connector()
        result = await c.write(
            TID,
            "nonexistent",
            WriteOperation(operation="create", data={"name": "x"}),
        )
        assert not result.success
        assert "unknown resource" in (result.error or "")


class TestWriteCreate:
    async def test_create_executes_insert(self) -> None:
        c, db = _make_connector()
        # fetch_one returns None for the after-snapshot lookup (id will be generated)
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="create",
                data={"code": "C001", "name": "Acme", "industry": "tech"},
            ),
        )
        assert result.success
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO erp.customers" in sql
        assert "tenant_id" in sql
        assert "C001" not in sql  # value should be parameterized, not interpolated

    async def test_create_with_disallowed_column_rejected(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        # "id" is not in _ALLOWED_COLUMNS for customers
        result = await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="create",
                data={"id": "123", "name": "Acme"},  # id not whitelisted
            ),
        )
        assert not result.success
        assert "disallowed" in (result.error or "")

    async def test_sql_injection_attempt_rejected(self) -> None:
        """Column whitelist prevents SQL injection via column names."""
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="create",
                data={"name; DROP TABLE erp.customers; --": "evil"},
            ),
        )
        assert not result.success
        assert "disallowed" in (result.error or "")


class TestWriteUpdate:
    async def test_update_fetches_before_and_executes(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before_row: dict[str, Any] = {"id": record_id, "name": "Old Name"}
        after_row: dict[str, Any] = {"id": record_id, "name": "New Name"}
        db.fetch_one.side_effect = [before_row, after_row]
        result = await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="update",
                record_id=record_id,
                data={"name": "New Name"},
            ),
        )
        assert result.success
        assert result.before == before_row
        assert result.after == after_row
        sql = db.execute.call_args.args[0]
        assert "UPDATE erp.customers" in sql
        assert "WHERE tenant_id = :p1 AND id = :p2" in sql

    async def test_update_record_not_found(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="update",
                record_id=str(uuid4()),
                data={"name": "x"},
            ),
        )
        assert not result.success
        assert "not found" in (result.error or "")

    async def test_update_requires_record_id(self) -> None:
        c, db = _make_connector()
        # before lookup returns None (record_id is None)
        result = await c.write(
            TID,
            "customers",
            WriteOperation(operation="update", data={"name": "x"}),
        )
        assert not result.success


class TestWriteDelete:
    async def test_delete_fetches_before_and_executes(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before_row: dict[str, Any] = {"id": record_id, "name": "To Delete"}
        db.fetch_one.return_value = before_row
        result = await c.write(
            TID,
            "customers",
            WriteOperation(operation="delete", record_id=record_id),
        )
        assert result.success
        assert result.before == before_row
        assert result.after is None
        sql = db.execute.call_args.args[0]
        assert "DELETE FROM erp.customers" in sql
        assert "WHERE tenant_id = :p0 AND id = :p1" in sql

    async def test_delete_record_not_found(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        result = await c.write(
            TID,
            "customers",
            WriteOperation(operation="delete", record_id=str(uuid4())),
        )
        assert not result.success


class TestTenantIsolation:
    """Gap #7: tenant_id must appear in all write SQL WHERE clauses."""

    async def test_update_sql_includes_tenant_id(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        db.fetch_one.side_effect = [
            {"id": record_id},
            {"id": record_id, "name": "New"},
        ]
        await c.write(
            TID,
            "customers",
            WriteOperation(operation="update", record_id=record_id, data={"name": "New"}),
        )
        sql = db.execute.call_args.args[0]
        assert "tenant_id = " in sql

    async def test_delete_sql_includes_tenant_id(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        db.fetch_one.return_value = {"id": record_id}
        await c.write(
            TID,
            "customers",
            WriteOperation(operation="delete", record_id=record_id),
        )
        sql = db.execute.call_args.args[0]
        assert "tenant_id = :p0" in sql

    async def test_create_includes_tenant_id_column(self) -> None:
        c, db = _make_connector()
        db.fetch_one.return_value = None
        await c.write(
            TID,
            "customers",
            WriteOperation(
                operation="create",
                data={"code": "C001", "name": "Acme", "industry": "tech"},
            ),
        )
        sql = db.execute.call_args.args[0]
        assert "tenant_id" in sql
        # First param after SQL is tenant_id
        assert db.execute.call_args.args[1] == TID


class TestRollback:
    async def test_rollback_create_deletes_row(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        await c.rollback(
            TID,
            {
                "operation": "create",
                "resource": "customers",
                "record_id": record_id,
                "before": None,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "DELETE FROM erp.customers" in sql
        assert "WHERE tenant_id = :p0 AND id = :p1" in sql
        assert db.execute.call_args.args[1] == TID
        assert db.execute.call_args.args[2] == record_id

    async def test_rollback_update_restores_before(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before: dict[str, Any] = {"name": "Old Name", "industry": "old"}
        await c.rollback(
            TID,
            {
                "operation": "update",
                "resource": "customers",
                "record_id": record_id,
                "before": before,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "UPDATE erp.customers" in sql
        assert "WHERE tenant_id = :p" in sql
        assert "Old Name" not in sql  # value parameterized

    async def test_rollback_delete_reinserts_row(self) -> None:
        c, db = _make_connector()
        record_id = str(uuid4())
        before: dict[str, Any] = {
            "name": "Deleted Customer",
            "code": "C001",
            "industry": "tech",
        }
        await c.rollback(
            TID,
            {
                "operation": "delete",
                "resource": "customers",
                "record_id": record_id,
                "before": before,
            },
        )
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO erp.customers" in sql
        assert "tenant_id" in sql

    async def test_rollback_update_without_before_is_noop(self) -> None:
        c, db = _make_connector()
        await c.rollback(
            TID,
            {
                "operation": "update",
                "resource": "customers",
                "record_id": "x",
                "before": None,
            },
        )
        db.execute.assert_not_called()

    async def test_rollback_delete_without_before_is_noop(self) -> None:
        c, db = _make_connector()
        await c.rollback(
            TID,
            {
                "operation": "delete",
                "resource": "customers",
                "record_id": "x",
                "before": None,
            },
        )
        db.execute.assert_not_called()


class TestDescribeSchema:
    async def test_returns_schema_description(self) -> None:
        c, db = _make_connector()
        db.fetch.side_effect = [
            [
                {
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "comment": None,
                },
                {
                    "column_name": "name",
                    "data_type": "varchar",
                    "is_nullable": "NO",
                    "comment": None,
                },
            ],
            [{"id": "x", "name": "sample"}],
        ]
        result = await c.describe_schema(TID, "products")
        assert isinstance(result, SchemaDescription)
        assert result.table_name == "erp.products"
        assert len(result.columns) == 2
        assert result.columns[0]["name"] == "id"
        assert len(result.sample_rows) == 1

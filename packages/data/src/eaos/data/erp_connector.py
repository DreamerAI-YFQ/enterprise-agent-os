"""ErpConnector — read/write connector for mock ERP tables (erp schema).

Phase 7 T6: implements parameterized SQL with column whitelists, tenant
isolation via ``tenant_id`` filtering on every query, real rollback, and
access_mode enforcement. Fixes gaps #1, #6, #7, #8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.data.connector import (
    DataResource,
    DataResult,
    ReadQuery,
    SchemaDescription,
    WriteOperation,
    WriteResult,
)

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient

_RESOURCES: list[DataResource] = [
    DataResource(
        name="products",
        display_name="产品",
        description="产品主数据表",
        access_mode="read",
    ),
    DataResource(
        name="customers",
        display_name="客户",
        description="客户主数据表",
        access_mode="read_write",
    ),
    DataResource(
        name="orders",
        display_name="订单",
        description="销售订单表",
        access_mode="read_write",
    ),
    DataResource(
        name="inventory",
        display_name="库存",
        description="库存记录表",
        access_mode="read_write",
    ),
]

# Column whitelists for SQL injection prevention (gap #8).
_ERP_ALLOWED_COLUMNS: dict[str, set[str]] = {
    "products": {"sku", "name", "category", "unit_price", "cost", "status"},
    "customers": {"code", "name", "industry", "contact_name", "contact_email", "credit_limit"},
    "orders": {
        "order_no",
        "customer_id",
        "product_id",
        "quantity",
        "unit_price",
        "amount",
        "status",
        "order_date",
    },
    "inventory": {"product_id", "warehouse", "quantity", "safety_stock"},
}


class ErpConnector:
    """DataConnector for mock ERP tables in the ``erp`` schema."""

    SCHEMA = "erp"
    _ALLOWED_RESOURCES: set[str] = {"products", "customers", "orders", "inventory"}
    _ALLOWED_COLUMNS = _ERP_ALLOWED_COLUMNS

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        del tenant_id  # resource catalog is shared; row-level isolation in read/write
        return list(_RESOURCES)

    async def read(
        self,
        tenant_id: UUID,
        resource: str,
        query: ReadQuery,
    ) -> DataResult:
        if resource not in self._ALLOWED_RESOURCES:
            return DataResult(rows=[], total=0)
        fields = ", ".join(query.fields) if query.fields else "*"
        sql = f"SELECT {fields} FROM {self.SCHEMA}.{resource}"
        params: list[Any] = [tenant_id]
        where_clauses: list[str] = ["tenant_id = :p0"]
        for col, val in query.filters.items():
            if col not in self._ALLOWED_COLUMNS.get(resource, set()):
                continue
            idx = len(params)
            where_clauses.append(f"{col} = :p{idx}")
            params.append(val)
        sql += " WHERE " + " AND ".join(where_clauses)
        if query.order_by:
            order_parts = [f"{col} {direction}" for col, direction in query.order_by]
            sql += " ORDER BY " + ", ".join(order_parts)
        sql += f" LIMIT :p{len(params)} OFFSET :p{len(params) + 1}"
        params.extend([query.limit, query.offset])
        rows = await self._db.fetch(sql, *params)
        count_sql = f"SELECT count(*) AS total FROM {self.SCHEMA}.{resource} WHERE tenant_id = :p0"
        count_params: list[Any] = [tenant_id]
        # Re-add filter clauses (skip tenant_id, already in count_sql)
        for col, val in query.filters.items():
            if col not in self._ALLOWED_COLUMNS.get(resource, set()):
                continue
            idx = len(count_params)
            count_sql += f" AND {col} = :p{idx}"
            count_params.append(val)
        count_row = await self._db.fetch_one(count_sql, *count_params)
        total = int(count_row["total"]) if count_row else 0
        return DataResult(rows=rows, total=total)

    async def write(
        self,
        tenant_id: UUID,
        resource: str,
        operation: WriteOperation,
    ) -> WriteResult:
        # 1. access_mode enforcement (gap #1)
        resources = await self.list_resources(tenant_id)
        res_spec = next((r for r in resources if r.name == resource), None)
        if res_spec is None:
            return WriteResult(success=False, error=f"unknown resource: {resource}")
        if res_spec.access_mode == "read":
            return WriteResult(
                success=False, error=f"resource {resource} is read-only"
            )

        # 2. before snapshot for update/delete (supplies rollback context)
        before: dict[str, Any] | None = None
        if operation.operation in ("update", "delete"):
            before = await self._fetch_one(tenant_id, resource, operation.record_id)
            if before is None:
                return WriteResult(
                    success=False,
                    error=f"record not found: {operation.record_id}",
                )

        # 3. parameterized SQL with column whitelist (gap #8)
        try:
            sql, params = self._build_write_sql(resource, operation, tenant_id)
        except ValueError as exc:
            return WriteResult(success=False, error=str(exc), before=before)

        # 4. execute with tenant isolation (gap #7)
        try:
            await self._db.execute(sql, *params)
        except Exception as exc:
            return WriteResult(success=False, error=str(exc), before=before)

        after = (
            await self._fetch_one(tenant_id, resource, operation.record_id)
            if operation.operation != "delete"
            else None
        )
        return WriteResult(success=True, before=before, after=after)

    def _build_write_sql(
        self,
        resource: str,
        operation: WriteOperation,
        tenant_id: UUID,
    ) -> tuple[str, list[Any]]:
        """Build parameterized SQL with column whitelist validation."""
        if resource not in self._ALLOWED_RESOURCES:
            raise ValueError(f"disallowed resource: {resource}")
        allowed_cols = self._ALLOWED_COLUMNS.get(resource, set())

        if operation.operation == "create":
            cols = list(operation.data.keys())
            if not cols:
                raise ValueError("create requires at least one column")
            if not all(c in allowed_cols for c in cols):
                raise ValueError("disallowed column in create data")
            # tenant_id always first param; then data values
            col_list = ", ".join(["tenant_id"] + cols)
            placeholders = ", ".join(f":p{i}" for i in range(len(cols) + 1))
            sql = (
                f"INSERT INTO {self.SCHEMA}.{resource} ({col_list}) "
                f"VALUES ({placeholders})"
            )
            params: list[Any] = [tenant_id, *operation.data.values()]
            return sql, params

        if operation.operation == "update":
            if not operation.record_id:
                raise ValueError("update requires record_id")
            cols = list(operation.data.keys())
            if not cols:
                raise ValueError("update requires at least one column")
            if not all(c in allowed_cols for c in cols):
                raise ValueError("disallowed column in update data")
            set_parts = [f"{c} = :p{i}" for i, c in enumerate(cols)]
            params = [tenant_id, *operation.data.values(), operation.record_id]
            # tenant_id = :p{len(cols)}, record_id = :p{len(cols)+1}
            tenant_idx = len(cols)
            rid_idx = len(cols) + 1
            set_clause = ", ".join(set_parts)
            sql = (
                f"UPDATE {self.SCHEMA}.{resource} SET {set_clause} "
                f"WHERE tenant_id = :p{tenant_idx} AND id = :p{rid_idx}"
            )
            return sql, params

        if operation.operation == "delete":
            if not operation.record_id:
                raise ValueError("delete requires record_id")
            sql = (
                f"DELETE FROM {self.SCHEMA}.{resource} "
                f"WHERE tenant_id = :p0 AND id = :p1"
            )
            return sql, [tenant_id, operation.record_id]

        raise ValueError(f"unknown operation: {operation.operation}")

    async def _fetch_one(
        self,
        tenant_id: UUID,
        resource: str,
        record_id: str | None,
    ) -> dict[str, Any] | None:
        if record_id is None:
            return None
        sql = (
            f"SELECT * FROM {self.SCHEMA}.{resource} "
            f"WHERE tenant_id = :p0 AND id = :p1"
        )
        return await self._db.fetch_one(sql, tenant_id, record_id)

    async def describe_schema(
        self,
        tenant_id: UUID,
        resource: str,
    ) -> SchemaDescription:
        rows = await self._db.fetch(
            "SELECT column_name, data_type, is_nullable, "
            "col_description((table_schema||'.'||table_name)::regclass, "
            "ordinal_position) AS comment "
            "FROM information_schema.columns "
            "WHERE table_schema = :p0 AND table_name = :p1 "
            "ORDER BY ordinal_position",
            self.SCHEMA,
            resource,
        )
        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "comment": r.get("comment"),
            }
            for r in rows
        ]
        sample_rows = await self._db.fetch(
            f"SELECT * FROM {self.SCHEMA}.{resource} "
            f"WHERE tenant_id = :p0 LIMIT 3",
            tenant_id,
        )
        return SchemaDescription(
            table_name=f"{self.SCHEMA}.{resource}",
            columns=columns,
            relations=[],
            sample_rows=sample_rows,
        )

    async def rollback(self, tenant_id: UUID, snapshot: dict[str, Any]) -> None:
        """Real rollback — reverse the prior write operation.

        snapshot keys: operation, resource, record_id, before (dict | None).
        """
        op = snapshot.get("operation")
        resource: str | None = snapshot.get("resource")
        record_id = snapshot.get("record_id")
        before = snapshot.get("before")
        if resource is None:
            return

        if op == "create":
            # Reverse INSERT with DELETE of the created row.
            await self._db.execute(
                f"DELETE FROM {self.SCHEMA}.{resource} "
                f"WHERE tenant_id = :p0 AND id = :p1",
                tenant_id,
                record_id,
            )
        elif op == "update" and before is not None:
            # Restore the row to its pre-update state.
            allowed = self._ALLOWED_COLUMNS.get(resource, set())
            cols = [c for c in before if c in allowed]
            if not cols:
                return
            set_parts = [f"{c} = :p{i}" for i, c in enumerate(cols)]
            params: list[Any] = [before[c] for c in cols]
            tenant_idx = len(params)
            rid_idx = len(params) + 1
            params.extend([tenant_id, record_id])
            sql = (
                f"UPDATE {self.SCHEMA}.{resource} SET {', '.join(set_parts)} "
                f"WHERE tenant_id = :p{tenant_idx} AND id = :p{rid_idx}"
            )
            await self._db.execute(sql, *params)
        elif op == "delete" and before is not None:
            # Recreate the deleted row.
            allowed = self._ALLOWED_COLUMNS.get(resource, set())
            cols = [c for c in before if c in allowed]
            if not cols:
                return
            col_list = ", ".join(["tenant_id", "id"] + cols)
            placeholders = ", ".join(f":p{i}" for i in range(len(cols) + 2))
            params = [tenant_id, record_id, *[before[c] for c in cols]]
            sql = (
                f"INSERT INTO {self.SCHEMA}.{resource} ({col_list}) "
                f"VALUES ({placeholders})"
            )
            await self._db.execute(sql, *params)

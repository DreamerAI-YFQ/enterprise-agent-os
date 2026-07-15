"""BI API — natural-language data query, SQL console, and schema browsing.

Employee route:
- ``POST /bi/query`` — Text2SQL: translate natural language to SQL, execute
  on the selected datasource, return rows + generated SQL + explanation.

Admin routes (``/admin/bi``):
- ``POST /admin/bi/sql`` — raw SQL console (read-only sandbox enforced)
- ``GET /admin/bi/datasources`` — list configured datasources
- ``GET /admin/bi/tables`` — list queryable resources across all connectors
- ``GET /admin/bi/tables/{name}`` — describe a table's schema/columns
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.core.context import TenantContext
from eaos.gateway.api.deps import (
    get_data_connectors,
    get_db,
    get_principal,
    get_sql_sandbox,
    get_text2sql_engine,
)
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.data.connector import DataConnector
    from eaos.data.text2sql.engine import Text2SQLEngine
    from eaos.data.text2sql.sandbox import SqlSandbox
    from eaos.infra.db.base import DbClient

router = APIRouter(tags=["bi"])


# -- Request / response models ------------------------------------------------


class BiQueryRequest(BaseModel):
    """Natural-language data query."""

    query: str
    datasource_id: UUID


class SqlConsoleRequest(BaseModel):
    """Admin SQL console — raw SQL executed in a read-only sandbox."""

    sql: str
    params: list[Any] = []


# -- Employee routes ----------------------------------------------------------


@router.post("/bi/query", status_code=200)
async def bi_query(
    body: BiQueryRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    engine: Text2SQLEngine = Depends(get_text2sql_engine),  # noqa: B008
) -> dict[str, Any]:
    """Translate a natural-language query to SQL, execute, and return results."""
    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=principal.user_id,  # BI queries have no agent; use user as placeholder
        agent_scope="personal",
    )
    result = await engine.query(body.query, ctx, body.datasource_id)
    return {
        "rows": result.rows,
        "sql": result.sql,
        "explanation": result.explanation,
        "truncated": result.truncated,
        "error": result.error,
    }


# -- Admin routes -------------------------------------------------------------


@router.post("/admin/bi/sql", status_code=200)
async def admin_sql_console(
    body: SqlConsoleRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    sandbox: SqlSandbox = Depends(get_sql_sandbox),  # noqa: B008
) -> dict[str, Any]:
    """Execute raw SQL in a read-only sandbox (admin only)."""
    rows = await sandbox.execute_readonly(
        body.sql, body.params, principal.tenant_id
    )
    return {"rows": rows, "row_count": len(rows)}


@router.get("/admin/bi/datasources", status_code=200)
async def list_datasources(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List configured datasources for the tenant."""
    rows = await db.fetch(
        "SELECT id, name, source_type, access_mode, status, created_at "
        "FROM data.datasources WHERE tenant_id = :p0 ORDER BY created_at DESC",
        principal.tenant_id,
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "source_type": r["source_type"],
            "access_mode": r.get("access_mode", "read"),
            "status": r.get("status", "active"),
        }
        for r in rows
    ]


@router.get("/admin/bi/tables", status_code=200)
async def list_tables(
    principal: Principal = Depends(require_admin),  # noqa: B008
    connectors: dict[str, DataConnector] = Depends(get_data_connectors),  # noqa: B008
    connector: str | None = Query(None, description="Filter by connector name"),
) -> list[dict[str, Any]]:
    """List queryable resources across all data connectors."""
    targets = (
        {connector: connectors[connector]}
        if connector and connector in connectors
        else connectors
    )
    results: list[dict[str, Any]] = []
    for name, conn in targets.items():
        resources = await conn.list_resources(principal.tenant_id)
        for res in resources:
            results.append(
                {
                    "connector": name,
                    "name": res.name,
                    "display_name": res.display_name,
                    "description": res.description,
                    "access_mode": res.access_mode,
                }
            )
    return results


@router.get("/admin/bi/tables/{table_name}", status_code=200)
async def describe_table(
    table_name: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    connectors: dict[str, DataConnector] = Depends(get_data_connectors),  # noqa: B008
    connector: str | None = Query(None, description="Connector to query"),
) -> dict[str, Any]:
    """Describe a table's schema (columns, relations, sample rows)."""
    if connector:
        if connector not in connectors:
            raise HTTPException(
                status_code=404, detail=f"connector not found: {connector}"
            )
        schema = await connectors[connector].describe_schema(
            principal.tenant_id, table_name
        )
        return _schema_to_dict(connector, schema)

    for name, conn in connectors.items():
        resources = await conn.list_resources(principal.tenant_id)
        if any(r.name == table_name for r in resources):
            schema = await conn.describe_schema(principal.tenant_id, table_name)
            return _schema_to_dict(name, schema)

    raise HTTPException(
        status_code=404, detail=f"table not found: {table_name}"
    )


def _schema_to_dict(connector: str, schema: Any) -> dict[str, Any]:
    return {
        "connector": connector,
        "table_name": schema.table_name,
        "columns": list(schema.columns),
        "relations": list(schema.relations),
        "sample_rows": list(schema.sample_rows),
    }

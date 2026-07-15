"""External connections admin API — CRUD for MCP/HTTP connection registry.

Manages external system connections stored in ``data.external_connections``
(migration 0006). All routes require the admin role. Credentials are
accepted on create/update but never returned in responses.

The ``ConnectionManager`` is fetched from ``app.state.connection_manager``.
If not wired, routes return 501.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI
from eaos.data.connection_types import ConnectionSpec
from eaos.gateway.api.routes.admin import require_admin
from eaos.gateway.api.deps import get_db
from eaos.infra.db.base import DbClient  # noqa: TC002 — runtime for FastAPI
from fastapi import APIRouter, Depends, HTTPException, Query, Request  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(prefix="/admin/connections", tags=["admin/connections"])

_VALID_TYPES = {"mcp_stdio", "mcp_sse", "mcp_http", "http_api"}


class ConnectionCreate(BaseModel):
    """Request body for POST /admin/connections."""

    name: str
    type: str  # mcp_stdio | mcp_sse | mcp_http | http_api
    config: dict[str, Any]
    credentials: dict[str, Any] | None = None


class ConnectionUpdate(BaseModel):
    """Request body for PUT /admin/connections/{id}."""

    name: str | None = None
    type: str | None = None
    config: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = None


def _manager(request: Request) -> Any:
    """Fetch ConnectionManager from app.state; 501 if not wired."""
    mgr = getattr(request.app.state, "connection_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=501, detail="connection_manager not configured on this instance"
        )
    return mgr


def _tool_registry(request: Request) -> Any:
    """Fetch ToolRegistry from app.state; None if not wired (test mode)."""
    return getattr(request.app.state, "tool_registry", None)


_MCP_TYPES = {"mcp_stdio", "mcp_sse", "mcp_http"}


async def _register_in_tool_registry(
    request: Request, conn_id: UUID, name: str, conn_type: str
) -> None:
    """Resolve an MCP connection and register it in the ToolRegistry.

    Best-effort: failures are logged but don't break the API response, so a
    connection can still be created even if the MCP subprocess fails to start.
    """
    registry = _tool_registry(request)
    if registry is None or conn_type not in _MCP_TYPES:
        return
    mgr = _manager(request)
    try:
        resolved = await mgr.resolve(conn_id)
        if resolved.mcp_client is not None:
            registry.register_mcp(name, resolved.mcp_client)
    except Exception:  # noqa: BLE001 — best-effort registration
        pass


def _record_to_dict(record: Any) -> dict[str, Any]:
    """Serialize a ConnectionRecord to a JSON-safe dict (no credentials)."""
    return {
        "id": str(record.id),
        "name": record.name,
        "type": record.type,
        "config": record.config,
        "health_status": record.health_status,
        "last_health_check": record.last_health_check.isoformat()
        if record.last_health_check
        else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.post("", status_code=201)
async def create_connection(
    body: ConnectionCreate,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    """Register a new external connection (admin only)."""
    if body.type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid type: {body.type}")
    mgr = _manager(request)
    spec = ConnectionSpec(
        tenant_id=principal.tenant_id,
        name=body.name,
        type=body.type,
        config=body.config,
        credentials=body.credentials,
    )
    try:
        conn_id = await mgr.register(spec)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _register_in_tool_registry(request, conn_id, body.name, body.type)
    return {"id": str(conn_id)}


@router.get("", status_code=200)
async def list_connections(
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all connections for the tenant (admin only, no credentials)."""
    mgr = _manager(request)
    records = await mgr.list(principal.tenant_id)
    return [_record_to_dict(r) for r in records]


@router.get("/{conn_id}", status_code=200)
async def get_connection(
    conn_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    """Get a single connection by ID (no credentials returned)."""
    mgr = _manager(request)
    record = await mgr.get(conn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if record.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    return _record_to_dict(record)


@router.put("/{conn_id}", status_code=200)
async def update_connection(
    conn_id: UUID,
    body: ConnectionUpdate,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    """Update a connection (admin only)."""
    mgr = _manager(request)
    existing = await mgr.get(conn_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")

    conn_type = body.type or existing.type
    if conn_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid type: {conn_type}")

    spec = ConnectionSpec(
        tenant_id=principal.tenant_id,
        name=body.name or existing.name,
        type=conn_type,
        config=body.config or existing.config,
        credentials=body.credentials,
    )
    await mgr.update(conn_id, spec)
    return {"id": str(conn_id)}


@router.delete("/{conn_id}", status_code=204)
async def delete_connection(
    conn_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> None:
    """Delete a connection (admin only)."""
    mgr = _manager(request)
    existing = await mgr.get(conn_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    # Unregister from ToolRegistry before deleting the connection record.
    registry = _tool_registry(request)
    if registry is not None and existing.type in _MCP_TYPES:
        registry.unregister_mcp(existing.name)
    await mgr.delete(conn_id)


@router.post("/{conn_id}/health-check", status_code=200)
async def trigger_health_check(
    conn_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    """Trigger a health check for a connection (admin only)."""
    mgr = _manager(request)
    existing = await mgr.get(conn_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    result = await mgr.health_check(conn_id)
    return {
        "id": str(conn_id),
        "status": result.status,
        "error": result.error,
    }


@router.get("/{conn_id}/call-logs", status_code=200)
async def call_logs(
    conn_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
) -> dict[str, Any]:
    """Retrieve call logs for a connection (write operations via WritePipeline)."""
    mgr = _manager(request)
    existing = await mgr.get(conn_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="connection not found")
    if existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")

    rows = await db.fetch(
        "SELECT id, tool_name, resource, operation, success, error, "
        "rolled_back, created_at "
        "FROM harness.write_audit "
        "WHERE tenant_id = :p0 AND tool_name = :p1 "
        "ORDER BY created_at DESC LIMIT :p2 OFFSET :p3",
        principal.tenant_id,
        existing.name,
        limit,
        offset,
    )
    count_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM harness.write_audit "
        "WHERE tenant_id = :p0 AND tool_name = :p1",
        principal.tenant_id,
        existing.name,
    )
    total = int(count_row["cnt"]) if count_row else 0
    items: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        uid = item.get("id")
        if uid is not None:
            item["id"] = str(uid)
        ts = item.get("created_at")
        if ts is not None and hasattr(ts, "isoformat"):
            item["created_at"] = ts.isoformat()
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}

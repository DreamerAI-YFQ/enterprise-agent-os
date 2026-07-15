"""Admin configuration API — models, MCP connectors, report templates, plugins.

- ``GET/PUT /admin/models`` — model configuration (stored in config.settings)
- ``GET /admin/mcp/connectors`` — list MCP/HTTP connections from data.external_connections
- ``GET/POST/PUT/DELETE /admin/report-templates`` — report template CRUD
- ``GET/PUT /admin/plugins`` — plugin configuration (stored in config.settings)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4  # noqa: TC003

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, HTTPException  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(tags=["config"])


# -- Helpers ------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def _get_setting(
    db: DbClient, tenant_id: UUID, key: str
) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT value FROM config.settings WHERE tenant_id = :p0 AND key = :p1",
        tenant_id,
        key,
    )
    if row is None:
        return {}
    return _load_json(row.get("value"))


async def _upsert_setting(
    db: DbClient, tenant_id: UUID, key: str, value: dict[str, Any]
) -> None:
    await db.execute(
        "INSERT INTO config.settings (tenant_id, key, value) "
        "VALUES (:p0, :p1, CAST(:p2 AS jsonb)) "
        "ON CONFLICT (tenant_id, key) DO UPDATE SET value = CAST(:p2 AS jsonb), "
        "updated_at = now()",
        tenant_id,
        key,
        json.dumps(value, ensure_ascii=False),
    )


# -- Models -------------------------------------------------------------------


class ModelsConfig(BaseModel):
    """Model configuration — default model, provider settings."""

    default_model: str = ""
    providers: dict[str, Any] = {}


@router.get("/admin/models", status_code=200)
async def get_models(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get model configuration (admin only)."""
    return await _get_setting(db, principal.tenant_id, "models")


@router.put("/admin/models", status_code=200)
async def put_models(
    body: ModelsConfig,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update model configuration (admin only)."""
    value = {"default_model": body.default_model, "providers": body.providers}
    await _upsert_setting(db, principal.tenant_id, "models", value)
    return value


# -- MCP connectors -----------------------------------------------------------


@router.get("/admin/mcp/connectors", status_code=200)
async def list_mcp_connectors(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List MCP/HTTP connections (admin only)."""
    rows = await db.fetch(
        "SELECT id, name, type, config, health_status, "
        "last_health_check, created_at, updated_at "
        "FROM data.external_connections WHERE tenant_id = :p0 "
        "ORDER BY created_at DESC",
        principal.tenant_id,
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "type": r["type"],
            "config": _load_json(r.get("config")),
            "health_status": r.get("health_status", "unknown"),
            "last_health_check": _iso(r.get("last_health_check")),
            "created_at": _iso(r.get("created_at")),
        }
        for r in rows
    ]


# -- Report templates ---------------------------------------------------------


class ReportTemplateCreate(BaseModel):
    name: str
    description: str = ""
    template_type: str = "generic"
    content: dict[str, Any] = {}


class ReportTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    template_type: str | None = None
    content: dict[str, Any] | None = None


def _template_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "description": row.get("description"),
        "template_type": row.get("template_type", "generic"),
        "content": _load_json(row.get("content")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


@router.get("/admin/report-templates", status_code=200)
async def list_report_templates(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List report templates (admin only)."""
    rows = await db.fetch(
        "SELECT id, tenant_id, name, description, template_type, content, "
        "created_at, updated_at FROM config.report_templates "
        "WHERE tenant_id = :p0 ORDER BY created_at DESC",
        principal.tenant_id,
    )
    return [_template_to_dict(r) for r in rows]


@router.post("/admin/report-templates", status_code=201)
async def create_report_template(
    body: ReportTemplateCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Create a report template (admin only)."""
    row = await db.fetch_one(
        "INSERT INTO config.report_templates "
        "(id, tenant_id, name, description, template_type, content, created_by) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, CAST(:p5 AS jsonb), :p6) "
        "RETURNING id, tenant_id, name, description, template_type, content, "
        "created_at, updated_at",
        uuid4(),
        principal.tenant_id,
        body.name,
        body.description,
        body.template_type,
        json.dumps(body.content, ensure_ascii=False),
        principal.user_id,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create template")
    return _template_to_dict(row)


@router.put("/admin/report-templates/{template_id}", status_code=200)
async def update_report_template(
    template_id: UUID,
    body: ReportTemplateUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update a report template (admin only)."""
    existing = await db.fetch_one(
        "SELECT id FROM config.report_templates WHERE id = :p0 AND tenant_id = :p1",
        template_id,
        principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="template not found")
    if body.name is not None:
        await db.execute(
            "UPDATE config.report_templates SET name = :p0, updated_at = now() "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.name, template_id, principal.tenant_id,
        )
    if body.description is not None:
        await db.execute(
            "UPDATE config.report_templates SET description = :p0, updated_at = now() "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.description, template_id, principal.tenant_id,
        )
    if body.template_type is not None:
        await db.execute(
            "UPDATE config.report_templates SET template_type = :p0, updated_at = now() "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.template_type, template_id, principal.tenant_id,
        )
    if body.content is not None:
        await db.execute(
            "UPDATE config.report_templates SET content = CAST(:p0 AS jsonb), "
            "updated_at = now() WHERE id = :p1 AND tenant_id = :p2",
            json.dumps(body.content, ensure_ascii=False),
            template_id, principal.tenant_id,
        )
    row = await db.fetch_one(
        "SELECT id, tenant_id, name, description, template_type, content, "
        "created_at, updated_at FROM config.report_templates "
        "WHERE id = :p0 AND tenant_id = :p1",
        template_id, principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="template not found after update")
    return _template_to_dict(row)


@router.delete("/admin/report-templates/{template_id}", status_code=204)
async def delete_report_template(
    template_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a report template (admin only)."""
    existing = await db.fetch_one(
        "SELECT id FROM config.report_templates WHERE id = :p0 AND tenant_id = :p1",
        template_id, principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="template not found")
    await db.execute(
        "DELETE FROM config.report_templates WHERE id = :p0 AND tenant_id = :p1",
        template_id, principal.tenant_id,
    )


# -- Plugins ------------------------------------------------------------------


class PluginsConfig(BaseModel):
    """Plugin configuration — enabled plugins and their settings."""

    plugins: dict[str, Any] = {}


@router.get("/admin/plugins", status_code=200)
async def get_plugins(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get plugin configuration (admin only)."""
    return await _get_setting(db, principal.tenant_id, "plugins")


@router.put("/admin/plugins", status_code=200)
async def put_plugins(
    body: PluginsConfig,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update plugin configuration (admin only)."""
    value = {"plugins": body.plugins}
    await _upsert_setting(db, principal.tenant_id, "plugins", value)
    return value

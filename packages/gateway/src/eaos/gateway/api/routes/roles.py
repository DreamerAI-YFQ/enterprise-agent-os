"""RBAC role-permission management routes.

Provides endpoints for managing the iam.permissions matrix:
  - GET  /admin/roles                  — list roles + descriptions
  - GET  /admin/permissions/catalog    — resource × action catalog for UI
  - GET  /admin/permissions/matrix     — full role × resource × action matrix
  - GET  /admin/roles/{role}/permissions — permissions for one role
  - PUT  /admin/roles/{role}/permissions — replace permissions for one role

All endpoints require admin role. The permission matrix is scoped to the
admin's tenant.
"""

from __future__ import annotations

from typing import Any

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI
from eaos.gateway.api.deps import get_db
from eaos.gateway.api.routes.admin import require_admin
from eaos.infra.db.base import DbClient  # noqa: TC002 — runtime for FastAPI
from fastapi import APIRouter, Depends, HTTPException  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(prefix="/admin")


# -- Catalog -----------------------------------------------------------------

# Canonical role list (matches iam.users.role CHECK values + viewer).
ROLES: list[dict[str, str]] = [
    {"role": "admin", "label": "管理员", "description": "全权访问所有模块与操作"},
    {"role": "manager", "label": "经理", "description": "部门级管理，可审批与发布"},
    {"role": "employee", "label": "员工", "description": "日常使用：对话、知识、技能"},
    {"role": "viewer", "label": "观察者", "description": "只读访问仪表盘与监控"},
]

# Resource groups for the matrix UI. Each group has a label and a list of
# resources; each resource has a label and the actions that apply to it.
PERMISSION_CATALOG: list[dict[str, Any]] = [
    {
        "group": "业务中心",
        "resources": [
            {
                "resource": "agent",
                "label": "Agent",
                "actions": ["create", "read", "update", "delete", "execute"],
            },
            {
                "resource": "workflow",
                "label": "工作流",
                "actions": ["create", "read", "update", "delete", "execute"],
            },
            {"resource": "approval", "label": "审批", "actions": ["read", "approve", "reject"]},
        ],
    },
    {
        "group": "技能与记忆",
        "resources": [
            {
                "resource": "skill",
                "label": "技能",
                "actions": ["create", "read", "update", "delete", "execute", "publish"],
            },
            {
                "resource": "memory",
                "label": "记忆",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "promotion",
                "label": "技能晋升",
                "actions": ["create", "read", "approve", "reject"],
            },
        ],
    },
    {
        "group": "知识中心",
        "resources": [
            {
                "resource": "document",
                "label": "文档",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "ontology",
                "label": "本体",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "knowledge.contribution",
                "label": "知识贡献",
                "actions": ["submit", "read", "review", "delete"],
            },
        ],
    },
    {
        "group": "BI 与数据",
        "resources": [
            {
                "resource": "datasource",
                "label": "数据源",
                "actions": ["create", "read", "update", "delete"],
            },
            {"resource": "bi_query", "label": "自然语言查询", "actions": ["read", "execute"]},
            {
                "resource": "connection",
                "label": "外部连接",
                "actions": ["create", "read", "update", "delete", "test"],
            },
        ],
    },
    {
        "group": "监控与审计",
        "resources": [
            {"resource": "audit_log", "label": "审计日志", "actions": ["read", "export"]},
            {"resource": "metric", "label": "指标", "actions": ["read", "export"]},
            {"resource": "trace", "label": "链路追踪", "actions": ["read"]},
            {
                "resource": "safety_case",
                "label": "安全评估",
                "actions": ["create", "read", "update", "delete"],
            },
        ],
    },
    {
        "group": "配置中心",
        "resources": [
            {
                "resource": "model",
                "label": "模型",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "trigger",
                "label": "调度",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "report_template",
                "label": "报告模板",
                "actions": ["create", "read", "update", "delete"],
            },
            {
                "resource": "plugin",
                "label": "插件",
                "actions": ["create", "read", "update", "delete"],
            },
        ],
    },
    {
        "group": "用户与租户",
        "resources": [
            {
                "resource": "user",
                "label": "用户",
                "actions": ["create", "read", "update", "delete", "invite", "reset_password"],
            },
            {
                "resource": "department",
                "label": "部门",
                "actions": ["create", "read", "update", "delete"],
            },
            {"resource": "role", "label": "角色权限", "actions": ["read", "update"]},
        ],
    },
    {
        "group": "会话与通知",
        "resources": [
            {
                "resource": "session",
                "label": "会话",
                "actions": ["read", "update", "delete", "export"],
            },
            {"resource": "notification", "label": "通知", "actions": ["read", "update"]},
        ],
    },
    {
        "group": "系统",
        "resources": [
            {
                "resource": "data_management",
                "label": "数据管理",
                "actions": ["read", "export", "import"],
            },
        ],
    },
]


# -- Models ------------------------------------------------------------------


class PermissionEntry(BaseModel):
    resource: str
    action: str
    constraint: dict[str, Any] | None = None


class PermissionUpdateRequest(BaseModel):
    """Replace the full permission set for one role."""

    permissions: list[PermissionEntry]


# -- Routes ------------------------------------------------------------------


@router.get("/roles", tags=["admin"])
async def list_roles(
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, str]]:
    """List all roles with labels and descriptions."""
    _ = principal
    return ROLES


@router.get("/permissions/catalog", tags=["admin"])
async def get_permission_catalog(
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return the resource × action catalog for matrix UI rendering."""
    _ = principal
    return PERMISSION_CATALOG


@router.get("/permissions/matrix", tags=["admin"])
async def get_permission_matrix(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Return the full permission matrix: {role: [{resource, action, constraint}]}."""
    rows = await db.fetch(
        'SELECT role, resource, action, "constraint" FROM iam.permissions '
        "WHERE tenant_id = :p0 ORDER BY role, resource, action",
        principal.tenant_id,
    )
    matrix: dict[str, list[dict[str, Any]]] = {r["role"]: [] for r in ROLES}
    for row in rows or []:
        role = row["role"]
        if role not in matrix:
            matrix[role] = []
        matrix[role].append(
            {
                "resource": row["resource"],
                "action": row["action"],
                "constraint": row.get("constraint"),
            }
        )
    return {"roles": [r["role"] for r in ROLES], "matrix": matrix}


@router.get("/roles/{role}/permissions", tags=["admin"])
async def get_role_permissions(
    role: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List permissions for a specific role."""
    _validate_role(role)
    rows = await db.fetch(
        'SELECT resource, action, "constraint" FROM iam.permissions '
        "WHERE tenant_id = :p0 AND role = :p1 ORDER BY resource, action",
        principal.tenant_id,
        role,
    )
    return [
        {
            "resource": r["resource"],
            "action": r["action"],
            "constraint": r.get("constraint"),
        }
        for r in rows or []
    ]


@router.put("/roles/{role}/permissions", tags=["admin"])
async def replace_role_permissions(
    role: str,
    body: PermissionUpdateRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Replace the full permission set for one role (delete + insert)."""
    _validate_role(role)
    if role == "admin":
        raise HTTPException(
            status_code=400,
            detail="admin role permissions cannot be modified (allow-all)",
        )

    # Delete existing permissions for this role.
    await db.execute(
        "DELETE FROM iam.permissions WHERE tenant_id = :p0 AND role = :p1",
        principal.tenant_id,
        role,
    )

    # Insert new permissions.
    imported = 0
    for entry in body.permissions:
        import json

        constraint_json = json.dumps(entry.constraint) if entry.constraint is not None else None
        await db.execute(
            'INSERT INTO iam.permissions (tenant_id, role, resource, action, "constraint") '
            "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb)) "
            "ON CONFLICT (tenant_id, role, resource, action) DO UPDATE "
            'SET "constraint" = EXCLUDED."constraint"',
            principal.tenant_id,
            role,
            entry.resource,
            entry.action,
            constraint_json,
        )
        imported += 1

    return {"role": role, "updated": imported}


# -- Helpers -----------------------------------------------------------------


def _validate_role(role: str) -> None:
    """Reject unknown roles with 400."""
    valid = {r["role"] for r in ROLES}
    if role not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"unknown role: {role}. Valid: {', '.join(sorted(valid))}",
        )

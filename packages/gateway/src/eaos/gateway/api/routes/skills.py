"""Skill API — personal skill CRUD + admin management.

Employee routes (``/skills``):
- ``GET /skills`` — list all skills visible to the user (personal + department + company published + own drafts)
- ``GET /skills/{id}`` — detail
- ``POST /skills`` — create a personal skill (scope=PERSONAL)
- ``PUT /skills/{id}`` — update name/description/instructions/etc.
- ``POST /skills/{id}/publish`` — publish a draft skill
- ``POST /skills/{id}/deprecate`` — deprecate a published skill (owner only)
- ``DELETE /skills/{id}`` — delete a draft skill (owner only)

Admin routes (``/admin/skills``):
- ``GET /admin/skills`` — list all tenant skills (with optional filters)
- ``POST /admin/skills`` — create a department/company-level skill
- ``PUT /admin/skills/{id}`` — update any skill (admin override)
- ``POST /admin/skills/{id}/publish`` — publish any skill
- ``POST /admin/skills/{id}/deprecate`` — deprecate a skill

Note: Published/deprecated skills are not hard-deleted — owners deprecate
them instead. Drafts may be hard-deleted by the owner via DELETE /skills/{id}.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.core.errors import NotFoundError
from eaos.gateway.api.deps import get_principal, get_skill_registry, get_skill_resolver
from eaos.gateway.api.routes.admin import require_admin
from eaos.skills.registry import SkillRegistry  # noqa: TC002
from eaos.skills.resolver import SkillResolver  # noqa: TC002
from eaos.skills.spec import RiskLevel, SkillCategory, SkillScope, SkillSpec  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(tags=["skills"])


# -- Serializers --------------------------------------------------------------


def _skill_to_dict(s: SkillSpec) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "tenant_id": str(s.tenant_id),
        "scope": s.scope.value,
        "owner_id": str(s.owner_id) if s.owner_id else None,
        "name": s.name,
        "display_name": s.display_name,
        "description": s.description,
        "category": s.category.value,
        "risk_level": s.risk_level.value,
        "instructions": s.instructions,
        "tools": list(s.tools),
        "version": s.version,
        "status": s.status,
    }


# -- Request models -----------------------------------------------------------


class SkillCreate(BaseModel):
    """Request body for POST /skills or POST /admin/skills.

    For employee route: scope must be 'personal' (or omitted); owner_id ignored.
    For admin route: scope may be 'department' or 'company'; owner_id required
    for department scope (the department id).
    """

    name: str
    display_name: str
    description: str
    category: str
    risk_level: str = "low"
    instructions: str = ""
    tools: list[str] = []
    scope: str = "personal"
    owner_id: str | None = None


class SkillUpdate(BaseModel):
    """Request body for PUT /skills/{id} or PUT /admin/skills/{id}."""

    name: str | None = None
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    risk_level: str | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    scope: str | None = None
    owner_id: str | None = None


class DeprecateRequest(BaseModel):
    """Request body for POST /admin/skills/{id}/deprecate."""

    reason: str = ""


# -- Helpers ------------------------------------------------------------------


def _parse_category(value: str) -> SkillCategory:
    normalized = value.lower()
    for member in SkillCategory:
        if member.value == normalized or member.name.lower() == normalized:
            return member
    raise HTTPException(status_code=422, detail=f"invalid category: {value}")


def _parse_risk(value: str) -> RiskLevel:
    normalized = value.lower()
    for member in RiskLevel:
        if member.value == normalized or member.name.lower() == normalized:
            return member
    raise HTTPException(status_code=422, detail=f"invalid risk_level: {value}")


def _parse_scope(value: str) -> SkillScope:
    normalized = value.lower()
    for member in SkillScope:
        if member.value == normalized or member.name.lower() == normalized:
            return member
    raise HTTPException(status_code=422, detail=f"invalid scope: {value}")


# -- Employee routes ----------------------------------------------------------


@router.get("/skills", status_code=200)
async def list_my_skills(
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
    resolver: SkillResolver = Depends(get_skill_resolver),  # noqa: B008
    scope: str | None = Query(None, description="Filter by scope (personal/department/company)"),
    status: str | None = Query(None, description="Filter by status"),
) -> list[dict[str, Any]]:
    """List skills visible to the current user.

    Returns the union of:
    - User's personal skills (including drafts)
    - Department skills for departments the user belongs to (published only)
    - Company-wide skills (published only)

    An optional ``scope`` filter narrows the result to one tier.
    """
    # resolve_for_user returns only published skills across all three scopes.
    published = await resolver.resolve_for_user(
        principal.tenant_id, principal.user_id
    )

    # Also fetch user's own draft/deprecated personal skills (not in published set).
    own_filters: dict[str, Any] = {"scope": "personal", "owner_id": principal.user_id}
    own_skills = await registry.list_by_tenant(principal.tenant_id, own_filters)

    # Merge, dedup by id, prefer published version when overlap.
    by_id: dict[UUID, SkillSpec] = {s.id: s for s in published}
    for s in own_skills:
        if s.id not in by_id:
            by_id[s.id] = s

    skills = list(by_id.values())

    # Optional scope filter
    if scope:
        scope_enum = _parse_scope(scope)
        skills = [s for s in skills if s.scope == scope_enum]

    # Optional status filter
    if status:
        skills = [s for s in skills if s.status == status]

    skills.sort(key=lambda s: s.name)
    return [_skill_to_dict(s) for s in skills]


@router.get("/skills/{skill_id}", status_code=200)
async def get_skill(
    skill_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, Any]:
    """Get a single skill by id."""
    try:
        skill = await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    return _skill_to_dict(skill)


@router.post("/skills", status_code=201)
async def create_skill(
    body: SkillCreate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Create a personal skill (scope=PERSONAL, owner=current user).

    Employees can only create personal skills. Admins should use
    POST /admin/skills to create department/company-level skills.
    """
    # Force scope=personal for employee route regardless of body.scope.
    category = _parse_category(body.category)
    risk = _parse_risk(body.risk_level)

    spec = SkillSpec(
        id=uuid4(),
        tenant_id=principal.tenant_id,
        scope=SkillScope.PERSONAL,
        owner_id=principal.user_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        category=category,
        risk_level=risk,
        instructions=body.instructions,
        tools=body.tools,
    )
    try:
        created = await registry.create(spec, principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": str(created.id)}


@router.put("/skills/{skill_id}", status_code=200)
async def update_skill(
    skill_id: UUID,
    body: SkillUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, Any]:
    """Update a skill (owner only, admins can edit any via PUT /admin/skills/{id})."""
    try:
        current = await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    if current.owner_id != principal.user_id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="only the owner can update this skill")

    updates = _build_updates(body)
    if not updates:
        return _skill_to_dict(current)

    updated = await registry.update(skill_id, principal.tenant_id, updates)
    return _skill_to_dict(updated)


@router.post("/skills/{skill_id}/publish", status_code=200)
async def publish_skill(
    skill_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Publish a draft skill (owner only)."""
    try:
        current = await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    if current.owner_id != principal.user_id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="only the owner can publish this skill")

    await registry.publish(skill_id, principal.tenant_id, principal.user_id)
    return {"status": "published"}


@router.post("/skills/{skill_id}/deprecate", status_code=200)
async def deprecate_own_skill(
    skill_id: UUID,
    body: DeprecateRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Deprecate a published skill (owner only)."""
    try:
        current = await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    if current.owner_id != principal.user_id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="only the owner can deprecate this skill")
    if current.status != "published":
        raise HTTPException(
            status_code=409, detail="only published skills can be deprecated"
        )

    await registry.deprecate(skill_id, principal.tenant_id, body.reason)
    return {"status": "deprecated"}


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_own_skill(
    skill_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> None:
    """Delete a draft skill (owner only). Published/deprecated skills must be deprecated instead."""
    try:
        current = await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    if current.owner_id != principal.user_id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="only the owner can delete this skill")
    if current.status != "draft":
        raise HTTPException(
            status_code=409, detail="only draft skills can be deleted; deprecate instead"
        )

    await registry.delete(skill_id, principal.tenant_id)


# -- Admin routes -------------------------------------------------------------


def _build_updates(body: SkillUpdate) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.description is not None:
        updates["description"] = body.description
    if body.category is not None:
        updates["category"] = _parse_category(body.category).value
    if body.risk_level is not None:
        updates["risk_level"] = _parse_risk(body.risk_level).value
    if body.instructions is not None:
        updates["instructions"] = body.instructions
    if body.tools is not None:
        updates["tools"] = body.tools
    if body.scope is not None:
        updates["scope"] = _parse_scope(body.scope).value
    if body.owner_id is not None:
        updates["owner_id"] = body.owner_id
    return updates


@router.get("/admin/skills", status_code=200)
async def list_all_skills(
    principal: Principal = Depends(require_admin),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
    scope: str | None = Query(None, description="Filter by scope"),
    category: str | None = Query(None, description="Filter by category"),
    status: str | None = Query(None, description="Filter by status"),
) -> list[dict[str, Any]]:
    """List all skills in the tenant (admin only)."""
    filters: dict[str, Any] = {}
    if scope:
        filters["scope"] = scope
    if category:
        filters["category"] = category
    if status:
        filters["status"] = status
    skills = await registry.list_by_tenant(principal.tenant_id, filters)
    return [_skill_to_dict(s) for s in skills]


@router.post("/admin/skills", status_code=201)
async def admin_create_skill(
    body: SkillCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Create a department or company-level skill (admin only).

    For scope=department, owner_id must be a department id.
    For scope=company, owner_id is None.
    For scope=personal, falls through to user-owned (admin creating on behalf).
    """
    category = _parse_category(body.category)
    risk = _parse_risk(body.risk_level)
    scope = _parse_scope(body.scope)

    owner_id: UUID | None
    if scope == SkillScope.PERSONAL:
        # Admin creating a personal skill on behalf of a user — owner_id required.
        if body.owner_id is None:
            raise HTTPException(
                status_code=422,
                detail="personal scope requires owner_id (the user id)",
            )
        owner_id = UUID(body.owner_id)
    elif scope == SkillScope.DEPARTMENT:
        if body.owner_id is None:
            raise HTTPException(
                status_code=422,
                detail="department scope requires owner_id (the department id)",
            )
        owner_id = UUID(body.owner_id)
    else:  # COMPANY
        owner_id = None

    spec = SkillSpec(
        id=uuid4(),
        tenant_id=principal.tenant_id,
        scope=scope,
        owner_id=owner_id,
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        category=category,
        risk_level=risk,
        instructions=body.instructions,
        tools=body.tools,
    )
    try:
        created = await registry.create(spec, principal.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": str(created.id)}


@router.put("/admin/skills/{skill_id}", status_code=200)
async def admin_update_skill(
    skill_id: UUID,
    body: SkillUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, Any]:
    """Update any skill (admin override)."""
    try:
        await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    updates = _build_updates(body)
    if not updates:
        return _skill_to_dict(await registry.get(skill_id, principal.tenant_id))

    updated = await registry.update(skill_id, principal.tenant_id, updates)
    return _skill_to_dict(updated)


@router.post("/admin/skills/{skill_id}/publish", status_code=200)
async def admin_publish_skill(
    skill_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Publish any skill (admin override)."""
    try:
        await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    await registry.publish(skill_id, principal.tenant_id, principal.user_id)
    return {"status": "published"}


@router.post("/admin/skills/{skill_id}/deprecate", status_code=200)
async def deprecate_skill(
    skill_id: UUID,
    body: DeprecateRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    registry: SkillRegistry = Depends(get_skill_registry),  # noqa: B008
) -> dict[str, str]:
    """Deprecate a skill (admin only)."""
    try:
        await registry.get(skill_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc

    await registry.deprecate(skill_id, principal.tenant_id, body.reason)
    return {"status": "deprecated"}

"""Agent API — list, detail, and admin CRUD.

``GET /agents`` lists agents visible to the current user (personal +
department + company scope). ``GET /agents/{id}`` resolves a single agent
with scope visibility check. ``POST /admin/agents`` creates a new agent
(admin only). ``PUT /admin/agents/{id}`` updates the capability boundary.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.agent.dispatcher import AgentDispatcher, AgentScope, CapabilityBoundary  # noqa: TC002
from eaos.core.auth import Principal  # noqa: TC002
from eaos.core.context import TenantContext
from eaos.gateway.api.deps import get_dispatcher, get_principal
from eaos.gateway.api.routes.admin import require_admin
from eaos.skills.spec import SkillCategory  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(tags=["agents"])


# -- Serializers -------------------------------------------------------------


def _capability_to_dict(cap: Any) -> dict[str, Any]:
    return {
        "allowed_models": list(cap.allowed_models),
        "allowed_datasources": [str(d) for d in cap.allowed_datasources],
        "writable_datasources": [str(d) for d in cap.writable_datasources],
        "allowed_skill_categories": [str(c) for c in cap.allowed_skill_categories],
        "max_task_duration_sec": cap.max_task_duration_sec,
        "max_iterations": cap.max_iterations,
    }


def _agent_to_dict(agent: Any) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "tenant_id": str(agent.tenant_id),
        "scope": str(agent.scope.value) if hasattr(agent.scope, "value") else str(agent.scope),
        "owner_id": str(agent.owner_id) if agent.owner_id else None,
        "name": agent.name,
        "description": agent.description,
        "model_config": dict(agent.model_config) if agent.model_config else {},
        "capability": _capability_to_dict(agent.capability),
        "assigned_skills": [str(s) for s in agent.assigned_skills],
        "status": agent.status,
    }


# -- Request models ----------------------------------------------------------


class AgentCreate(BaseModel):
    """Request body for POST /admin/agents."""

    name: str
    scope: str  # personal | department | company
    description: str | None = None
    model_settings: dict[str, Any] = {}
    owner_id: UUID | None = None


class CapabilityUpdate(BaseModel):
    """Request body for PUT /admin/agents/{id}."""

    allowed_models: list[str] | None = None
    allowed_datasources: list[UUID] | None = None
    writable_datasources: list[UUID] | None = None
    allowed_skill_categories: list[str] | None = None
    max_task_duration_sec: int | None = None
    max_iterations: int | None = None


# -- Employee routes ---------------------------------------------------------


@router.get("/agents", status_code=200)
async def list_agents(
    principal: Principal = Depends(get_principal),  # noqa: B008
    dispatcher: AgentDispatcher = Depends(get_dispatcher),  # noqa: B008
) -> list[dict[str, Any]]:
    """List agents available to the current user."""
    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=UUID(int=0),
        agent_scope="personal",
        department_ids=principal.departments,
    )
    agents = await dispatcher.list_available(ctx)
    return [_agent_to_dict(a) for a in agents]


@router.get("/agents/{agent_id}", status_code=200)
async def get_agent(
    agent_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    dispatcher: AgentDispatcher = Depends(get_dispatcher),  # noqa: B008
) -> dict[str, Any]:
    """Get a single agent (with scope visibility check)."""
    try:
        agent = await dispatcher.resolve_agent_for_user(
            principal.tenant_id, principal.user_id, agent_id
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    return _agent_to_dict(agent)


# -- Admin routes ------------------------------------------------------------


@router.post("/admin/agents", status_code=201)
async def create_agent(
    body: AgentCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    dispatcher: AgentDispatcher = Depends(get_dispatcher),  # noqa: B008
) -> dict[str, str]:
    """Create a new agent (admin only)."""
    try:
        scope = AgentScope(body.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid scope: {body.scope}") from exc

    agent = await dispatcher.create_agent(
        tenant_id=principal.tenant_id,
        scope=scope,
        owner_id=body.owner_id or principal.user_id,
        name=body.name,
        creator=principal.user_id,
    )
    return {"id": str(agent.id)}


@router.put("/admin/agents/{agent_id}", status_code=200)
async def update_agent_capability(
    agent_id: UUID,
    body: CapabilityUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    dispatcher: AgentDispatcher = Depends(get_dispatcher),  # noqa: B008
) -> dict[str, Any]:
    """Update an agent's capability boundary (admin only)."""
    # Fetch current to merge partial updates
    try:
        current = await dispatcher.get(agent_id, principal.tenant_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc

    boundary = CapabilityBoundary(
        allowed_models=body.allowed_models
        if body.allowed_models is not None
        else list(current.capability.allowed_models),
        allowed_datasources=body.allowed_datasources
        if body.allowed_datasources is not None
        else list(current.capability.allowed_datasources),
        writable_datasources=body.writable_datasources
        if body.writable_datasources is not None
        else list(current.capability.writable_datasources),
        allowed_skill_categories=[SkillCategory(c) for c in body.allowed_skill_categories]
        if body.allowed_skill_categories is not None
        else list(current.capability.allowed_skill_categories),
        max_task_duration_sec=body.max_task_duration_sec
        if body.max_task_duration_sec is not None
        else current.capability.max_task_duration_sec,
        max_iterations=body.max_iterations
        if body.max_iterations is not None
        else current.capability.max_iterations,
    )
    updated = await dispatcher.update_capability(agent_id, principal.tenant_id, boundary)
    return _agent_to_dict(updated)

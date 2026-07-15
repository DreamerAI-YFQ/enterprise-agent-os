"""Agent dispatcher — three-tier agent creation and visibility resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from eaos.core.errors import NotFoundError, PermissionDeniedError
from eaos.skills.spec import SkillCategory

if TYPE_CHECKING:
    from eaos.core.context import TenantContext
    from eaos.infra.db.base import DbClient


class AgentScope(StrEnum):
    """Three agent tiers."""

    PERSONAL = "personal"
    DEPARTMENT = "department"
    COMPANY = "company"


@dataclass(frozen=True)
class CapabilityBoundary:
    """Agent capability boundary (L7 Harness config).

    Defines what an agent CAN and CANNOT do. Enforced by Harness before each
    action.
    """

    allowed_models: list[str] = field(default_factory=list)
    allowed_datasources: list[UUID] = field(default_factory=list)
    writable_datasources: list[UUID] = field(default_factory=list)
    allowed_skill_categories: list[SkillCategory] = field(default_factory=list)
    max_task_duration_sec: int = 600
    max_iterations: int = 10


@dataclass(frozen=True)
class AgentConfig:
    """Agent configuration."""

    id: UUID
    tenant_id: UUID
    scope: AgentScope
    owner_id: UUID | None
    name: str
    description: str | None
    model_config: dict[str, Any]  # routing preferences
    capability: CapabilityBoundary
    assigned_skills: list[UUID] = field(default_factory=list)
    status: str = "active"


class AgentDispatcher(Protocol):
    """Three-tier agent dispatch: create, resolve, list."""

    async def create_agent(
        self,
        tenant_id: UUID,
        scope: AgentScope,
        owner_id: UUID | None,
        name: str,
        creator: UUID,
    ) -> AgentConfig:
        """Create an agent. Harness checks creator's permission for scope."""
        ...

    async def get(self, agent_id: UUID, tenant_id: UUID) -> AgentConfig:
        """Fetch by id."""
        ...

    async def resolve_agent_for_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        agent_id: UUID,
    ) -> AgentConfig:
        """Resolve agent for user, enforcing visibility (scope-based)."""
        ...

    async def list_available(
        self,
        ctx: TenantContext,
    ) -> list[AgentConfig]:
        """List agents visible to the user (for orchestrator routing)."""
        ...

    async def assign_skill(
        self,
        agent_id: UUID,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """Assign a skill to an agent."""
        ...

    async def update_capability(
        self,
        agent_id: UUID,
        tenant_id: UUID,
        boundary: CapabilityBoundary,
    ) -> AgentConfig:
        """Update agent capability boundary (admin only)."""
        ...


def _boundary_to_dict(boundary: CapabilityBoundary) -> dict[str, Any]:
    """Serialize CapabilityBoundary to a JSON-safe dict (UUIDs/enums -> str)."""
    return {
        "allowed_models": list(boundary.allowed_models),
        "allowed_datasources": [str(d) for d in boundary.allowed_datasources],
        "writable_datasources": [str(d) for d in boundary.writable_datasources],
        "allowed_skill_categories": [
            str(c.value) for c in boundary.allowed_skill_categories
        ],
        "max_task_duration_sec": boundary.max_task_duration_sec,
        "max_iterations": boundary.max_iterations,
    }


def _row_to_config(row: dict[str, Any]) -> AgentConfig:
    """Map a DB row to AgentConfig, parsing the JSONB capability column."""
    cap_raw = row.get("capability") or {}
    if isinstance(cap_raw, str):
        cap_raw = json.loads(cap_raw)
    boundary = CapabilityBoundary(
        allowed_models=list(cap_raw.get("allowed_models", [])),
        allowed_datasources=[
            UUID(d) if isinstance(d, str) else d
            for d in cap_raw.get("allowed_datasources", [])
        ],
        writable_datasources=[
            UUID(d) if isinstance(d, str) else d
            for d in cap_raw.get("writable_datasources", [])
        ],
        allowed_skill_categories=[
            SkillCategory(c) if isinstance(c, str) else c
            for c in cap_raw.get("allowed_skill_categories", [])
        ],
        max_task_duration_sec=int(cap_raw.get("max_task_duration_sec", 600)),
        max_iterations=int(cap_raw.get("max_iterations", 10)),
    )
    model_raw = row.get("model_config") or {}
    if isinstance(model_raw, str):
        model_raw = json.loads(model_raw)
    return AgentConfig(
        id=row["id"],
        tenant_id=row["tenant_id"],
        scope=AgentScope(row["scope"]),
        owner_id=row.get("owner_id"),
        name=row["name"],
        description=row.get("description"),
        model_config=dict(model_raw),
        capability=boundary,
        status=row.get("status", "active"),
    )


class PgAgentDispatcher:
    """AgentDispatcher backed by PostgreSQL with three-tier scope visibility.

    scope visibility rules:
      - PERSONAL: owner_id is a user_id; visible only to that user.
      - DEPARTMENT: owner_id is a dept_id; visible to members of that dept.
      - COMPANY: owner_id is None; visible to all users in the tenant.
    """

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def create_agent(
        self,
        tenant_id: UUID,
        scope: AgentScope,
        owner_id: UUID | None,
        name: str,
        creator: UUID,
    ) -> AgentConfig:
        del creator  # Phase 3: no Harness permission check; recorded via trace only.
        boundary = CapabilityBoundary()
        rows = await self._db.fetch(
            "INSERT INTO agent.agents "
            "(tenant_id, scope, owner_id, name, model_config, capability, status) "
            "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb), CAST(:p5 AS jsonb), 'active') "
            "RETURNING id, tenant_id, scope, owner_id, name, description, "
            "model_config, capability, status",
            tenant_id,
            str(scope.value),
            owner_id,
            name,
            "{}",
            json.dumps(_boundary_to_dict(boundary)),
        )
        return _row_to_config(rows[0])

    async def get(self, agent_id: UUID, tenant_id: UUID) -> AgentConfig:
        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, tenant_id, scope, owner_id, name, description, "
            "model_config, capability, status FROM agent.agents "
            "WHERE id = :p0 AND tenant_id = :tenant_id",
            tenant_id,
            agent_id,
        )
        if not rows:
            raise NotFoundError(f"agent {agent_id} not found")
        return _row_to_config(rows[0])

    async def resolve_agent_for_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        agent_id: UUID,
    ) -> AgentConfig:
        agent = await self.get(agent_id, tenant_id)
        if agent.scope == AgentScope.PERSONAL:
            if agent.owner_id != user_id:
                raise PermissionDeniedError(
                    f"user {user_id} cannot access personal agent {agent_id}"
                )
        elif agent.scope == AgentScope.DEPARTMENT:
            if agent.owner_id is None:
                raise PermissionDeniedError(
                    f"department agent {agent_id} has no owning department"
                )
            rows = await self._db.fetch(
                "SELECT 1 FROM iam.memberships "
                "WHERE user_id = :p0 AND department_id = :p1",
                user_id,
                agent.owner_id,
            )
            if not rows:
                raise PermissionDeniedError(
                    f"user {user_id} is not a member of department {agent.owner_id}"
                )
        # COMPANY: visible to any user in the tenant (no extra check).
        return agent

    async def list_available(self, ctx: TenantContext) -> list[AgentConfig]:
        clauses = ["tenant_id = :tenant_id"]
        params: list[Any] = []
        param_idx = 0

        # Personal: owned by the user.
        clauses.append(f"(scope = 'personal' AND owner_id = :p{param_idx})")
        params.append(ctx.user_id)
        param_idx += 1

        # Department: owned by one of the user's departments.
        if ctx.department_ids:
            placeholders = ", ".join(f":p{param_idx + i}" for i in range(len(ctx.department_ids)))
            clauses.append(f"(scope = 'department' AND owner_id IN ({placeholders}))")
            params.extend(ctx.department_ids)
            param_idx += len(ctx.department_ids)

        # Company: visible to all.
        clauses.append("scope = 'company'")

        sql = (
            "SELECT id, tenant_id, scope, owner_id, name, description, "
            "model_config, capability, status FROM agent.agents "
            f"WHERE {' OR '.join(clauses)} ORDER BY name"
        )
        rows = await self._db.tenant_scoped_fetch(sql, ctx.tenant_id, *params)
        return [_row_to_config(r) for r in rows]

    async def assign_skill(
        self,
        agent_id: UUID,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> None:
        await self._db.execute(
            "INSERT INTO agent.agent_skills (agent_id, skill_id, enabled) "
            "VALUES (:p0, :p1, TRUE) ON CONFLICT DO NOTHING",
            agent_id,
            skill_id,
        )

    async def update_capability(
        self,
        agent_id: UUID,
        tenant_id: UUID,
        boundary: CapabilityBoundary,
    ) -> AgentConfig:
        rows = await self._db.tenant_scoped_fetch(
            "UPDATE agent.agents SET capability = CAST(:p0 AS jsonb) "
            "WHERE id = :p1 AND tenant_id = :tenant_id "
            "RETURNING id, tenant_id, scope, owner_id, name, description, "
            "model_config, capability, status",
            tenant_id,
            json.dumps(_boundary_to_dict(boundary)),
            agent_id,
        )
        if not rows:
            raise NotFoundError(f"agent {agent_id} not found")
        return _row_to_config(rows[0])

"""Pillar 1: Capability boundary checker.

Enforces: allowed models, allowed datasources, writable datasources, allowed
skill categories, max task duration, max iterations. Configured per-agent at
creation time, enforced before every action.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from eaos.agent.dispatcher import CapabilityBoundary
from eaos.core.errors import HarnessViolationError, NotFoundError
from eaos.skills.spec import SkillCategory

if TYPE_CHECKING:
    from eaos.harness.context import GuardContext


class CapabilityDb(Protocol):
    """Minimal DB subset for capability checking."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class CapabilityChecker(Protocol):
    """Pillar 1: capability boundary enforcement."""

    async def check(self, ctx: GuardContext) -> None:
        """Check agent's action against its capability boundary.

        Raises HarnessViolationError if:
        - model not in allowed_models
        - datasource not in allowed_datasources (read) / writable (write)
        - skill category not in allowed_skill_categories
        """
        ...

    async def get_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> CapabilityBoundary:
        """Fetch the capability boundary for an agent."""
        ...

    async def update_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
        boundary: CapabilityBoundary,
    ) -> CapabilityBoundary:
        """Update boundary (admin only, audited)."""
        ...


def _boundary_to_dict(boundary: CapabilityBoundary) -> dict[str, Any]:
    """Serialize a CapabilityBoundary to a JSON-compatible dict."""
    return {
        "allowed_models": list(boundary.allowed_models),
        "allowed_datasources": [str(ds) for ds in boundary.allowed_datasources],
        "writable_datasources": [str(ds) for ds in boundary.writable_datasources],
        "allowed_skill_categories": [
            str(cat) for cat in boundary.allowed_skill_categories
        ],
        "max_task_duration_sec": boundary.max_task_duration_sec,
        "max_iterations": boundary.max_iterations,
    }


def _dict_to_boundary(data: dict[str, Any]) -> CapabilityBoundary:
    """Deserialize a dict into a CapabilityBoundary."""
    return CapabilityBoundary(
        allowed_models=list(data.get("allowed_models", [])),
        allowed_datasources=[
            ds if isinstance(ds, UUID) else UUID(str(ds))
            for ds in data.get("allowed_datasources", [])
        ],
        writable_datasources=[
            ds if isinstance(ds, UUID) else UUID(str(ds))
            for ds in data.get("writable_datasources", [])
        ],
        allowed_skill_categories=[
            SkillCategory(cat) if not isinstance(cat, SkillCategory) else cat
            for cat in data.get("allowed_skill_categories", [])
        ],
        max_task_duration_sec=int(data.get("max_task_duration_sec", 600)),
        max_iterations=int(data.get("max_iterations", 10)),
    )


class CapabilityCheckerImpl:
    """Concrete CapabilityChecker backed by PostgreSQL.

    Reads/writes the ``capability`` JSONB column on ``agent.agents``.
    """

    def __init__(self, db: CapabilityDb) -> None:
        self._db = db

    async def check(self, ctx: GuardContext) -> None:
        """Check agent's action against its capability boundary."""
        boundary = await self.get_boundary(ctx.agent_id, ctx.tenant_id)

        model = ctx.attributes.get("model")
        if model and boundary.allowed_models and model not in boundary.allowed_models:
            raise HarnessViolationError(
                f"model '{model}' not in allowed_models for agent {ctx.agent_id}"
            )

        datasource = ctx.attributes.get("datasource")
        if datasource:
            ds_uuid = datasource if isinstance(datasource, UUID) else UUID(str(datasource))
            mode = ctx.attributes.get("datasource_mode", "read")
            if mode == "write":
                if (
                    boundary.writable_datasources
                    and ds_uuid not in boundary.writable_datasources
                ):
                    raise HarnessViolationError(
                        f"datasource {ds_uuid} not writable for agent {ctx.agent_id}"
                    )
            else:
                if (
                    boundary.allowed_datasources
                    and ds_uuid not in boundary.allowed_datasources
                ):
                    raise HarnessViolationError(
                        f"datasource {ds_uuid} not in allowed_datasources for agent {ctx.agent_id}"
                    )

        skill_category = ctx.attributes.get("skill_category")
        if (
            skill_category
            and boundary.allowed_skill_categories
        ):
            cat = (
                skill_category
                if isinstance(skill_category, SkillCategory)
                else SkillCategory(str(skill_category))
            )
            if cat not in boundary.allowed_skill_categories:
                raise HarnessViolationError(
                    f"skill category '{cat}' not allowed for agent {ctx.agent_id}"
                )

    async def get_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> CapabilityBoundary:
        """Fetch the capability boundary for an agent from agent.agents."""
        row = await self._db.fetch_one(
            "SELECT capability FROM agent.agents WHERE id = :p0 AND tenant_id = :p1",
            agent_id,
            tenant_id,
        )
        if row is None:
            raise NotFoundError(f"agent {agent_id} not found in tenant {tenant_id}")
        capability = row.get("capability")
        if capability is None:
            return CapabilityBoundary()
        if isinstance(capability, str):
            capability = json.loads(capability)
        return _dict_to_boundary(capability)

    async def update_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
        boundary: CapabilityBoundary,
    ) -> CapabilityBoundary:
        """Update the capability boundary for an agent."""
        await self._db.execute(
            """UPDATE agent.agents SET capability = :p0, updated_at = now()
               WHERE id = :p1 AND tenant_id = :p2""",
            json.dumps(_boundary_to_dict(boundary)),
            agent_id,
            tenant_id,
        )
        return boundary

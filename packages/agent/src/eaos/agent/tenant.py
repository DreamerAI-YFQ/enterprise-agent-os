"""Tenant manager protocol — multi-tenant Agent lifecycle.

Every graph thread is isolated by tenant, agent, and session.  Agent scope
controls capability visibility; it must never collapse unrelated user sessions
onto one LangGraph checkpoint.  Explicit collaboration uses its own session id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.agent.dispatcher import AgentConfig, AgentDispatcher, AgentScope
    from eaos.infra.db.base import DbClient


class TenantManager(Protocol):
    """Multi-tenant Agent management."""

    async def create_tenant(self, name: str) -> UUID:
        """Create a new tenant."""
        ...

    async def create_agent_in_tenant(
        self,
        tenant_id: UUID,
        scope: AgentScope,
        owner_id: UUID | None,
        name: str,
        creator: UUID,
    ) -> AgentConfig:
        """Create an agent within a tenant (delegates to AgentDispatcher)."""
        ...

    async def resolve_thread_id(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        session_id: UUID | None,
        scope: AgentScope,
    ) -> str:
        """Compute LangGraph thread_id for an agent invocation."""
        ...


class PgTenantManager:
    """TenantManager backed by PostgreSQL.

    Thread id format for every scope:
    ``tenant:{tenant_id}:agent:{agent_id}:session:{session_id}``.

    A missing session id gets a fresh UUID for that one resolution, so callers
    cannot accidentally share a checkpoint. HTTP entry points create a
    concrete session before invoking the runner.
    """

    def __init__(self, db: DbClient, dispatcher: AgentDispatcher) -> None:
        self._db = db
        self._dispatcher = dispatcher

    async def create_tenant(self, name: str) -> UUID:
        slug = f"{name.lower().replace(' ', '-')[:40]}-{uuid4().hex[:8]}"
        rows = await self._db.fetch(
            "INSERT INTO iam.tenants (name, slug, status, settings) "
            "VALUES (:p0, :p1, 'active', CAST(:p2 AS jsonb)) RETURNING id",
            name,
            slug,
            "{}",
        )
        return cast("UUID", rows[0]["id"])

    async def create_agent_in_tenant(
        self,
        tenant_id: UUID,
        scope: AgentScope,
        owner_id: UUID | None,
        name: str,
        creator: UUID,
    ) -> AgentConfig:
        return await self._dispatcher.create_agent(tenant_id, scope, owner_id, name, creator)

    async def resolve_thread_id(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        session_id: UUID | None,
        scope: AgentScope,
    ) -> str:
        del scope  # capability scope does not weaken conversation isolation
        suffix = str(session_id or uuid4())
        return f"tenant:{tenant_id}:agent:{agent_id}:session:{suffix}"

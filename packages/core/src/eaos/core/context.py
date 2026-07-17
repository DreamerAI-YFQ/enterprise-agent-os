"""Tenant context for multi-tenant isolation.

Explicitly passed through call chains (no global singletons for tenant state).
Also mirrored into contextvars so middleware, tracing, and logging can pick it
up automatically without polluting business function signatures.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from uuid import UUID, uuid4

# contextvars populated by API/IM middleware; business code reads them via
# get_tenant_context() but PREFER receiving TenantContext as an explicit arg.
tenant_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "tenant_id", default=None
)
user_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar("user_id", default=None)


@dataclass(frozen=True)
class TenantContext:
    """Agent execution tenant context, passed explicitly through call chains."""

    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    agent_scope: str  # personal/department/company
    session_id: UUID | None = None
    department_ids: list[UUID] = field(default_factory=list)
    mode: str | None = None  # eval hint: "rag" forces knowledge-base retrieval

    def for_agent(
        self,
        agent_id: UUID,
        *,
        scope: str | None = None,
        session_id: UUID | None = None,
    ) -> TenantContext:
        """Derive a context for another agent (multi-agent collaboration)."""
        return TenantContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=agent_id,
            agent_scope=scope or self.agent_scope,
            session_id=session_id or self.session_id,
            department_ids=list(self.department_ids),
            mode=self.mode,
        )

    @property
    def thread_id(self) -> str:
        """LangGraph thread_id composite ID: tenant:agent:session.

        Department shared agents use a fixed 'shared' session suffix so all
        members access the same checkpoint (relay collaboration).
        """
        if self.agent_scope == "department":
            return f"{self.tenant_id}:{self.agent_id}:shared"
        session = self.session_id or uuid4()
        return f"{self.tenant_id}:{self.agent_id}:{session}"


def get_tenant_context() -> TenantContext | None:
    """Read tenant context from contextvars (middleware-injected).

    Prefer passing TenantContext explicitly; use this only in cross-cutting
    code (logging, tracing) that cannot receive explicit args.
    """
    tenant_id = tenant_id_var.get()
    user_id = user_id_var.get()
    if tenant_id is None or user_id is None:
        return None
    # Note: this returns a minimal context without agent_id; only use for
    # logging/tracing metadata, not for business logic.
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=uuid4(),  # placeholder, not meaningful here
        agent_scope="personal",
    )


def set_tenant_context(tenant_id: UUID, user_id: UUID) -> None:
    """Set tenant context in contextvars (called by middleware)."""
    tenant_id_var.set(tenant_id)
    user_id_var.set(user_id)

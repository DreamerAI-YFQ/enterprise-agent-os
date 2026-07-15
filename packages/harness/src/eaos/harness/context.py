"""GuardContext — governance context for all Harness decisions.

Explicitly passed to all governance checks. Carries: who (user), what (agent),
which action, on which resource, at what risk level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class GuardContext:
    """Governance context for a single agent action."""

    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    agent_scope: str  # personal/department/company
    department_ids: list[UUID] = field(default_factory=list)
    action: str = ""  # invoke/execute_skill/read_data/write_data/collaborate/...
    resource: str = ""  # agent/skill/datasource/...
    resource_id: UUID | None = None
    risk_level: str = "low"  # low/medium/high
    attributes: dict[str, Any] = field(default_factory=dict)  # ABAC extra attrs

    def with_action(self, action: str, resource: str) -> GuardContext:
        """Derive a context with action/resource set (for @guarded decorator)."""
        return GuardContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            agent_scope=self.agent_scope,
            department_ids=list(self.department_ids),
            action=action,
            resource=resource,
            resource_id=self.resource_id,
            risk_level=self.risk_level,
            attributes=dict(self.attributes),
        )

    def with_risk(self, risk_level: str) -> GuardContext:
        """Derive a context with updated risk level."""
        return GuardContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            agent_scope=self.agent_scope,
            department_ids=list(self.department_ids),
            action=self.action,
            resource=self.resource,
            resource_id=self.resource_id,
            risk_level=risk_level,
            attributes=dict(self.attributes),
        )

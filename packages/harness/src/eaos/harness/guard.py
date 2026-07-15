"""HarnessGuard — unified entry point composing six governance pillars.

Business code never calls pillars directly; it calls HarnessGuard.guard()
(pre-action) and HarnessGuard.post_guard() (post-action) via @guarded decorator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.agent.dispatcher import CapabilityBoundary
    from eaos.harness.context import GuardContext


class HarnessGuard(Protocol):
    """Platform-level harness unified entry point."""

    async def guard(self, ctx: GuardContext) -> None:
        """Pre-action governance: capability + permission + quota + compliance_pre.

        Raises HarnessViolationError or PermissionDeniedError or
        QuotaExceededError on violation.
        """
        ...

    async def post_guard(self, ctx: GuardContext, result: Any) -> Any:
        """Post-action governance: compliance_post + quota_consume + quality + audit.

        Returns possibly-modified result (e.g. PII-redacted output).
        """
        ...

    async def get_capability_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> CapabilityBoundary:
        """Fetch agent's capability boundary."""
        ...

    async def check_permission(
        self,
        ctx: GuardContext,
        *,
        action: str | None = None,
        resource: str | None = None,
        constraint: dict[str, Any] | None = None,
    ) -> None:
        """Standalone permission check (for dispatcher, marketplace)."""
        ...

    async def audit(
        self,
        ctx: GuardContext,
        detail: dict[str, Any],
    ) -> None:
        """Write an audit log entry (append-only, immutable)."""
        ...

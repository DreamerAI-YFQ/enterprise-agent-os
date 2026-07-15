"""HarnessImpl — unified entry point composing six governance pillars.

Business code never calls pillars directly; it calls HarnessImpl.guard()
(pre-action) and HarnessImpl.post_guard() (post-action) via the @guarded
decorator. The global harness instance is registered via set_global_harness().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.agent.dispatcher import CapabilityBoundary
    from eaos.harness.capability.checker import CapabilityCheckerImpl
    from eaos.harness.compliance.guard import ComplianceGuardImpl
    from eaos.harness.context import GuardContext
    from eaos.harness.cost.governor import CostGovernorImpl
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.evolution.governor import EvolutionGovernorImpl
    from eaos.harness.permission.evaluator import PermissionEvaluatorImpl
    from eaos.harness.policy import PolicyEngineImpl
    from eaos.harness.quality.guard import QualityGuardImpl


class HarnessImpl:
    """Composes six governance pillars into a unified guard.

    Implements the HarnessGuard Protocol. Pre-action checks (guard) run
    permission, capability, and quota enforcement. Post-action checks
    (post_guard) run compliance PII redaction, quality metric recording, and
    audit logging. The evolution approval gate is invoked separately when a
    high-risk action requires human-in-the-loop approval.
    """

    def __init__(
        self,
        permission: PermissionEvaluatorImpl,
        capability: CapabilityCheckerImpl,
        cost: CostGovernorImpl,
        compliance: ComplianceGuardImpl,
        quality: QualityGuardImpl,
        evolution: EvolutionGovernorImpl,
        approval: ApprovalGateImpl,
        policy: PolicyEngineImpl,
    ) -> None:
        self._permission = permission
        self._capability = capability
        self._cost = cost
        self._compliance = compliance
        self._quality = quality
        self._evolution = evolution
        self._approval = approval
        self._policy = policy

    async def guard(self, ctx: GuardContext) -> None:
        """Pre-action governance: permission + capability + quota.

        Raises PermissionDeniedError, HarnessViolationError, or
        QuotaExceededError on violation.
        """
        await self._permission.evaluate(ctx)
        await self._capability.check(ctx)
        await self._cost.check_quota(ctx)

    async def post_guard(self, ctx: GuardContext, result: Any) -> Any:
        """Post-action governance: compliance + quality + audit.

        Returns possibly-modified result (e.g. PII-redacted output).
        """
        if isinstance(result, str):
            result = await self._compliance.post_check(ctx, result)
        await self._quality.evaluate(ctx, result)
        return result

    async def get_capability_boundary(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> CapabilityBoundary:
        """Fetch agent's capability boundary."""
        return await self._capability.get_boundary(agent_id, tenant_id)

    async def check_permission(
        self,
        ctx: GuardContext,
        *,
        action: str | None = None,
        resource: str | None = None,
        constraint: dict[str, Any] | None = None,
    ) -> None:
        """Standalone permission check (for dispatcher, marketplace)."""
        del constraint  # PermissionEvaluatorImpl.evaluate uses ctx directly
        if action is not None or resource is not None:
            ctx = ctx.with_action(action or ctx.action, resource or ctx.resource)
        await self._permission.evaluate(ctx)

    async def audit(
        self,
        ctx: GuardContext,
        detail: dict[str, Any],
    ) -> None:
        """Write an audit log entry (append-only, immutable)."""
        await self._compliance.audit(ctx, detail)


# ============================================================
# Global harness registry — used by @guarded decorator
# ============================================================

_global_harness: HarnessImpl | None = None


def set_global_harness(harness: HarnessImpl) -> None:
    """Register the global HarnessImpl instance for @guarded decorator."""
    global _global_harness
    _global_harness = harness


def get_global_harness() -> HarnessImpl | None:
    """Return the registered global harness, or None if not set."""
    return _global_harness

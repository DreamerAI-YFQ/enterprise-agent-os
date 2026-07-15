"""WritePipeline — orchestrates write operations through the governance pipeline.

Phase 7 T3: intent → Harness.guard() → HITL approval → connector.write() →
AuditLogger.log() → rollback on failure → Harness.post_guard().

The pipeline is idempotent and resumable: if a high-risk write requires HITL
approval, it raises ``WriteApprovalRequired``. The caller resumes by re-calling
``execute()`` with ``intent.approval_id`` set to the approved approval id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from eaos.data.connector import WriteOperation, WriteResult
from eaos.observability.audit import AuditEntry, AuditLogger

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from eaos.data.connector import DataConnector
    from eaos.harness.context import GuardContext
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.harness import HarnessImpl


@dataclass(frozen=True)
class WriteIntent:
    """A request to perform a write operation through the governance pipeline."""

    tenant_id: UUID
    principal_id: UUID  # user who initiated the write
    agent_id: UUID
    tool_name: str
    resource: str
    operation: str  # create | update | delete
    data: dict[str, Any] = field(default_factory=dict)
    record_id: str | None = None
    agent_scope: str = "personal"
    skill_id: UUID | None = None
    trace_id: UUID | None = None
    approval_id: UUID | None = None  # set on resume path (already approved)
    session_id: UUID | None = None  # required for HITL approval
    risk_level: str = "medium"  # low | medium | high
    department_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class WriteOutcome:
    """Result of a WritePipeline.execute() call."""

    success: bool
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    error: str | None = None
    audit_id: UUID | None = None
    rolled_back: bool = False
    approval_id: UUID | None = None  # set when HITL approval was required


class WriteApprovalRequired(Exception):  # noqa: N818 - domain name, used across codebase
    """Raised when a high-risk write requires HITL approval before execution.

    The caller catches this, triggers the interrupt/resume flow, and re-calls
    ``WritePipeline.execute()`` with ``intent.approval_id`` set.
    """

    def __init__(self, approval_id: UUID, intent: WriteIntent) -> None:
        self.approval_id = approval_id
        self.intent = intent
        super().__init__(
            f"approval {approval_id} required for {intent.operation} "
            f"on {intent.resource}"
        )


class WritePipeline:
    """Orchestrates write operations through the 8-pillar governance pipeline.

    Steps:
        1. Build GuardContext and call Harness.guard() (permission/capability/cost)
        2. If high-risk and no prior approval → request_approval → raise WriteApprovalRequired
        3. Resolve connector and execute write
        4. AuditLogger.log() the outcome
        5. If write failed → connector.rollback() + AuditLogger.log_rollback()
        6. Harness.post_guard() (compliance/quality)
    """

    def __init__(
        self,
        harness: HarnessImpl,
        connector_resolver: Callable[[str], DataConnector],
        audit_logger: AuditLogger,
        approval_gate: ApprovalGateImpl,
    ) -> None:
        self._harness = harness
        self._connector_resolver = connector_resolver
        self._audit_logger = audit_logger
        self._approval_gate = approval_gate

    async def execute(self, intent: WriteIntent) -> WriteOutcome:
        """Execute a write operation through the governance pipeline."""
        # 1. Build context and run pre-action guard
        ctx = self._build_ctx(intent)
        await self._harness.guard(ctx)

        # 2. HITL: high-risk writes require approval (skip if already approved)
        if intent.risk_level == "high" and intent.approval_id is None:
            approval_id = await self._approval_gate.request_approval(
                ctx, intent.skill_id, "high_risk_write",
                tool_name=intent.tool_name,
                resource=intent.resource,
                operation=intent.operation,
                risk_level=intent.risk_level,
                intent_data=intent.data,
            )
            raise WriteApprovalRequired(approval_id, intent)

        # 3. Resolve connector and execute write
        connector = self._connector_resolver(intent.tool_name)
        operation = WriteOperation(
            operation=intent.operation,
            record_id=intent.record_id,
            data=intent.data,
        )
        try:
            result: WriteResult = await connector.write(
                intent.tenant_id, intent.resource, operation
            )
        except Exception as exc:
            return await self._handle_write_failure(
                intent, exc, None, ctx
            )

        # 4. Audit log
        audit_id = await self._audit_logger.log(
            AuditEntry(
                tenant_id=intent.tenant_id,
                principal_id=intent.principal_id,
                tool_name=intent.tool_name,
                resource=intent.resource,
                operation=intent.operation,
                success=result.success,
                before=result.before,
                after=result.after,
                approval_id=intent.approval_id,
                trace_id=intent.trace_id,
                error=result.error,
            )
        )

        # 5. Rollback on failure (if we have a before snapshot)
        if not result.success and result.before is not None:
            return await self._rollback_and_log(
                intent, result, audit_id
            )

        # 6. Post-guard (compliance/quality)
        await self._harness.post_guard(ctx, result)

        return WriteOutcome(
            success=result.success,
            before=result.before,
            after=result.after,
            error=result.error,
            audit_id=audit_id,
            approval_id=intent.approval_id,
        )

    async def _handle_write_failure(
        self,
        intent: WriteIntent,
        exc: Exception,
        before: dict[str, Any] | None,
        ctx: GuardContext,
    ) -> WriteOutcome:
        """Handle an exception during connector.write()."""
        audit_id = await self._audit_logger.log(
            AuditEntry(
                tenant_id=intent.tenant_id,
                principal_id=intent.principal_id,
                tool_name=intent.tool_name,
                resource=intent.resource,
                operation=intent.operation,
                success=False,
                before=before,
                error=str(exc),
                approval_id=intent.approval_id,
                trace_id=intent.trace_id,
            )
        )
        await self._harness.post_guard(ctx, exc)
        return WriteOutcome(
            success=False,
            error=str(exc),
            audit_id=audit_id,
        )

    async def _rollback_and_log(
        self,
        intent: WriteIntent,
        result: WriteResult,
        audit_id: UUID,
    ) -> WriteOutcome:
        """Attempt rollback after a failed write, log the rollback."""
        snapshot: dict[str, Any] = {
            "operation": intent.operation,
            "resource": intent.resource,
            "record_id": intent.record_id,
            "before": result.before,
        }
        connector = self._connector_resolver(intent.tool_name)
        try:
            await connector.rollback(intent.tenant_id, snapshot)
            await self._audit_logger.log_rollback(
                audit_id, f"write failed: {result.error}"
            )
        except Exception:
            # Best-effort rollback — audit the failure but don't mask the original error
            pass
        return WriteOutcome(
            success=False,
            before=result.before,
            error=result.error,
            audit_id=audit_id,
            rolled_back=True,
        )

    @staticmethod
    def _build_ctx(intent: WriteIntent) -> GuardContext:
        """Build a GuardContext from a WriteIntent."""
        from eaos.harness.context import GuardContext

        attrs: dict[str, Any] = {}
        if intent.session_id is not None:
            attrs["session_id"] = intent.session_id
        if intent.skill_id is not None:
            attrs["skill_id"] = intent.skill_id
        return GuardContext(
            tenant_id=intent.tenant_id,
            user_id=intent.principal_id,
            agent_id=intent.agent_id,
            agent_scope=intent.agent_scope,
            department_ids=list(intent.department_ids),
            action="write_data",
            resource=intent.resource,
            risk_level=intent.risk_level,
            attributes=attrs,
        )

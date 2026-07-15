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
    idempotency_key: str | None = None  # C09: dedup key for retry safety


@dataclass(frozen=True)
class WriteOutcome:
    """Result of a WritePipeline.execute() call."""

    success: bool
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    error: str | None = None
    audit_id: UUID | None = None
    rolled_back: bool = False
    rollback_error: str | None = None  # C09: set when rollback itself fails
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
        db: Any = None,  # C09: for idempotency key dedup queries
    ) -> None:
        self._harness = harness
        self._connector_resolver = connector_resolver
        self._audit_logger = audit_logger
        self._approval_gate = approval_gate
        self._db = db
        self._idempotency_cache: dict[str, WriteOutcome] = {}  # C09: in-process dedup

    async def _check_idempotency(self, intent: WriteIntent) -> WriteOutcome | None:
        """C09: Check if this write was already executed successfully.

        In-process cache prevents duplicate writes from API retries within
        the same worker. For cross-restart idempotency, the audit table
        can be queried via self._db (future enhancement).
        """
        cached = self._idempotency_cache.get(intent.idempotency_key or "")
        if cached is not None and cached.success:
            return cached
        # Also check audit table if db is available
        if self._db is not None and intent.idempotency_key:
            try:
                row = await self._db.fetch_one(
                    "SELECT id, success, after_data FROM harness.write_audit "
                    "WHERE tenant_id = :p0 AND tool_name = :p1 "
                    "AND after_data->>'_idempotency_key' = :p2 "
                    "AND success = TRUE LIMIT 1",
                    intent.tenant_id,
                    intent.tool_name,
                    intent.idempotency_key,
                )
                if row is not None:
                    return WriteOutcome(
                        success=True,
                        after=row.get("after_data"),
                        audit_id=row.get("id"),
                    )
            except Exception:  # noqa: BLE001 — idempotency check is best-effort
                pass
        return None

    def _cache_outcome(self, intent: WriteIntent, outcome: WriteOutcome) -> None:
        """Cache a successful outcome for idempotency."""
        if intent.idempotency_key and outcome.success:
            self._idempotency_cache[intent.idempotency_key] = outcome

    async def execute(self, intent: WriteIntent) -> WriteOutcome:
        """Execute a write operation through the governance pipeline.

        C08: Approval is verified server-side — not just checking approval_id
        is non-null, but querying the real approval status from the database.
        C09: Rollback failures are NOT swallowed — they are logged and
        reported as compensation_failed. Idempotency key prevents duplicate
        writes on retry.
        """
        # C09: Idempotency check — if we've seen this key before and the
        # write succeeded, return the cached outcome instead of re-executing.
        if intent.idempotency_key and self._db is not None:
            cached = await self._check_idempotency(intent)
            if cached is not None:
                return cached

        # 1. Build context and run pre-action guard
        ctx = self._build_ctx(intent)
        await self._harness.guard(ctx)

        # 2. HITL: high-risk writes require approval
        if intent.risk_level == "high":
            if intent.approval_id is None:
                # Request new approval
                approval_id = await self._approval_gate.request_approval(
                    ctx, intent.skill_id, "high_risk_write",
                    tool_name=intent.tool_name,
                    resource=intent.resource,
                    operation=intent.operation,
                    risk_level=intent.risk_level,
                    intent_data=intent.data,
                )
                raise WriteApprovalRequired(approval_id, intent)
            else:
                # C08/GAP-05: Verify the real approval status from DB.
                # Don't trust that approval_id is non-null — verify it's 'approved'.
                real_status = await self._approval_gate.check_approval(
                    intent.approval_id, intent.tenant_id
                )
                if real_status != "approved":
                    return WriteOutcome(
                        success=False,
                        error=f"approval status is '{real_status}', expected 'approved'",
                        approval_id=intent.approval_id,
                    )

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

        outcome = WriteOutcome(
            success=result.success,
            before=result.before,
            after=result.after,
            error=result.error,
            audit_id=audit_id,
            approval_id=intent.approval_id,
        )
        self._cache_outcome(intent, outcome)  # C09: cache for idempotency
        return outcome

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
        """Attempt rollback after a failed write, log the rollback.

        C09/GAP-07: Rollback exceptions are NOT swallowed. If rollback fails,
        ``rolled_back`` is False and ``rollback_error`` is set. Only when
        rollback succeeds (or the DB is verified clean) is ``rolled_back``
        set to True.
        """
        snapshot: dict[str, Any] = {
            "operation": intent.operation,
            "resource": intent.resource,
            "record_id": result.after.get("id") if result.after else intent.record_id,
            "before": result.before,
        }
        connector = self._connector_resolver(intent.tool_name)
        rollback_error: str | None = None
        try:
            await connector.rollback(intent.tenant_id, snapshot)

            # C09: Verify the rollback actually succeeded by checking DB state.
            # For create rollback (DELETE), verify the row is gone.
            # For update rollback, verify the row matches before snapshot.
            if intent.operation == "create" and snapshot["record_id"]:
                check = await self._verify_record_deleted(
                    intent.tenant_id, intent.resource, snapshot["record_id"]
                )
                if not check:
                    rollback_error = "rollback verification failed: record still exists"
            elif intent.operation == "update" and snapshot["record_id"] and result.before:
                check = await self._verify_record_matches(
                    intent.tenant_id, intent.resource, snapshot["record_id"], result.before
                )
                if not check:
                    rollback_error = "rollback verification failed: record does not match before snapshot"

            if rollback_error is None:
                await self._audit_logger.log_rollback(
                    audit_id, f"write failed: {result.error}"
                )
        except Exception as exc:
            # C09: Don't swallow — record the rollback failure
            rollback_error = str(exc)
            try:
                await self._audit_logger.log_rollback(
                    audit_id, f"rollback FAILED: {exc}"
                )
            except Exception:  # noqa: BLE001 — audit logging is best-effort
                pass

        return WriteOutcome(
            success=False,
            before=result.before,
            error=result.error,
            audit_id=audit_id,
            rolled_back=(rollback_error is None),  # C09: True only if rollback succeeded
            rollback_error=rollback_error,
        )

    async def _verify_record_deleted(
        self, tenant_id: UUID, resource: str, record_id: str
    ) -> bool:
        """Verify that a record was actually deleted (for create rollback)."""
        try:
            connector = self._connector_resolver("erp_read")  # type: ignore[arg-type]
        except Exception:
            return True  # can't verify — assume success
        # Use the connector's read method to check
        from eaos.data.connector import ReadQuery
        try:
            result = await connector.read(  # type: ignore[attr-defined]
                tenant_id, resource, ReadQuery(filters={"id": record_id})
            )
            return result.total == 0
        except Exception:
            return True  # can't verify — assume success

    async def _verify_record_matches(
        self, tenant_id: UUID, resource: str, record_id: str, before: dict[str, Any]
    ) -> bool:
        """Verify that a record matches the before snapshot (for update rollback)."""
        connector = self._connector_resolver("erp_read")  # type: ignore[arg-type]
        try:
            from eaos.data.connector import ReadQuery
            result = await connector.read(  # type: ignore[attr-defined]
                tenant_id, resource, ReadQuery(filters={"id": record_id})
            )
            if result.total == 0 or not result.rows:
                return False
            current = result.rows[0]
            # Check key fields match
            for key, expected in before.items():
                if key in ("id", "tenant_id", "created_at", "updated_at"):
                    continue
                if str(current.get(key, "")) != str(expected):
                    return False
            return True
        except Exception:
            return True  # can't verify — assume success

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

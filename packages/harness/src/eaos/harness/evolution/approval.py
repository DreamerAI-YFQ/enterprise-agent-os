"""HITL approval gate — human-in-the-loop approval for high-risk actions.

When a skill execution is flagged as high-risk (or a policy requires approval),
the harness creates an ApprovalRequest and interrupts the agent run. A human
admin approves or rejects via the admin API, after which the run resumes.

The approvals table (``harness.approvals``) is created by migration 0004.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from eaos.harness.context import GuardContext


@dataclass(frozen=True)
class ApprovalRequest:
    """A human approval ticket for a high-risk agent action."""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    skill_id: UUID | None
    session_id: UUID
    reason: str  # high_risk / cost_threshold / quality_degraded
    status: str  # pending / approved / executing / consumed / rejected / expired
    requested_by: UUID
    decided_by: UUID | None
    decided_at: datetime | None
    created_at: datetime
    # Operation context (populated by WritePipeline for high_risk_write)
    tool_name: str | None = None
    resource: str | None = None
    operation: str | None = None
    risk_level: str | None = None
    intent_data: dict[str, Any] | None = None


class ApprovalDb(Protocol):
    """Minimal DB subset for the HITL approval gate."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


def _row_to_request(row: dict[str, Any]) -> ApprovalRequest:
    """Map a DB row to an ApprovalRequest."""
    decided_at = row.get("decided_at")
    return ApprovalRequest(
        id=row["id"],
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        skill_id=row.get("skill_id"),
        session_id=row["session_id"],
        reason=str(row["reason"]),
        status=str(row["status"]),
        requested_by=row["requested_by"],
        decided_by=row.get("decided_by"),
        decided_at=decided_at if isinstance(decided_at, datetime) else None,
        created_at=row["created_at"],
        tool_name=row.get("tool_name"),
        resource=row.get("resource"),
        operation=row.get("operation"),
        risk_level=row.get("risk_level"),
        intent_data=row.get("intent_data"),
    )


class ApprovalGateImpl:
    """HITL approval gate backed by PostgreSQL.

    Creates approval tickets in ``harness.approvals`` for high-risk actions.
    The LangGraph interrupt/resume wiring is handled by the runner (T11); this
    class is the persistence layer for approval tickets.
    """

    def __init__(self, db: ApprovalDb) -> None:
        self._db = db

    async def request_approval(
        self,
        ctx: GuardContext,
        skill_id: UUID | None,
        reason: str,
        *,
        tool_name: str | None = None,
        resource: str | None = None,
        operation: str | None = None,
        risk_level: str | None = None,
        intent_data: dict[str, Any] | None = None,
    ) -> UUID:
        """Create a pending approval ticket and return its id.

        A LangGraph interrupt resumes by re-entering the interrupted node.  The
        idempotency key stored in ``intent_data`` lets that re-entry reuse the
        original pending/approved ticket instead of creating a second ticket.
        """
        import json

        approval_id = uuid4()
        session_id = ctx.attributes.get("session_id")
        if session_id is None:
            raise ValueError("session_id is required in ctx.attributes for approval")

        idempotency_key = (
            intent_data.get("idempotency_key") if isinstance(intent_data, dict) else None
        )
        if idempotency_key:
            existing = await self._db.fetch_one(
                """SELECT id FROM harness.approvals
                   WHERE tenant_id = :p0 AND agent_id = :p1
                     AND session_id = :p2 AND requested_by = :p3
                     AND reason = :p4 AND tool_name = :p5
                     AND resource = :p6 AND operation = :p7
                     AND intent_data->>'idempotency_key' = :p8
                     AND status IN ('pending', 'approved', 'executing')
                   ORDER BY created_at DESC LIMIT 1""",
                ctx.tenant_id,
                ctx.agent_id,
                session_id,
                ctx.user_id,
                reason,
                tool_name,
                resource,
                operation,
                str(idempotency_key),
            )
            if existing is not None and existing.get("id") is not None:
                return UUID(str(existing["id"]))

        await self._db.execute(
            """INSERT INTO harness.approvals
                   (id, tenant_id, agent_id, skill_id, session_id, reason,
                    status, requested_by, created_at,
                     tool_name, resource, operation, risk_level, intent_data)
               VALUES (:p0, :p1, :p2, :p3, :p4, :p5, 'pending', :p6, :p7,
                       :p8, :p9, :p10, :p11, CAST(:p12 AS JSONB))""",
            approval_id,
            ctx.tenant_id,
            ctx.agent_id,
            skill_id,
            session_id,
            reason,
            ctx.user_id,
            datetime.now(UTC),
            tool_name,
            resource,
            operation,
            risk_level,
            json.dumps(intent_data, default=str) if intent_data is not None else None,
        )
        return approval_id

    async def claim_for_execution(
        self,
        approval_id: UUID,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        session_id: UUID | None,
        requested_by: UUID,
        tool_name: str,
        resource: str,
        operation: str,
        idempotency_key: str | None,
    ) -> str:
        """Atomically reserve an approved ticket for exactly one execution.

        ``executing`` is deliberately distinct from ``consumed``: the approval
        is reserved before the connector call (blocking concurrent replays),
        and is only marked consumed after the write has an audit record.
        """
        import json

        from eaos.core.errors import NotFoundError, PermissionDeniedError

        row = await self._db.fetch_one(
            """SELECT id, tenant_id, agent_id, session_id, requested_by,
                      tool_name, resource, operation, status, intent_data
               FROM harness.approvals
               WHERE id = :p0 AND tenant_id = :p1""",
            approval_id,
            tenant_id,
        )
        if row is None:
            raise NotFoundError(f"approval {approval_id} not found")

        stored_data = row.get("intent_data")
        if isinstance(stored_data, str):
            try:
                stored_data = json.loads(stored_data)
            except json.JSONDecodeError:
                stored_data = {}
        stored_key = stored_data.get("idempotency_key") if isinstance(stored_data, dict) else None
        expected = (
            row.get("agent_id") == agent_id
            and row.get("session_id") == session_id
            and row.get("requested_by") == requested_by
            and row.get("tool_name") == tool_name
            and row.get("resource") == resource
            and row.get("operation") == operation
            and stored_key == idempotency_key
        )
        if not expected:
            raise PermissionDeniedError(f"approval {approval_id} is not bound to this write intent")

        status = str(row.get("status", "unknown"))
        if status != "approved":
            return status

        claimed = await self._db.fetch_one(
            """UPDATE harness.approvals
               SET status = 'executing'
               WHERE id = :p0 AND tenant_id = :p1 AND status = 'approved'
               RETURNING status""",
            approval_id,
            tenant_id,
        )
        if claimed is not None:
            return str(claimed.get("status", "executing"))

        current = await self._db.fetch_one(
            "SELECT status FROM harness.approvals WHERE id = :p0 AND tenant_id = :p1",
            approval_id,
            tenant_id,
        )
        return str(current.get("status", "unknown")) if current else "unknown"

    async def consume_after_execution(
        self,
        approval_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """Mark a reserved ticket consumed after execution has been audited."""
        await self._db.execute(
            """UPDATE harness.approvals
               SET status = 'consumed'
               WHERE id = :p0 AND tenant_id = :p1 AND status = 'executing'""",
            approval_id,
            tenant_id,
        )

    async def check_approval(
        self,
        approval_id: UUID,
        tenant_id: UUID,
    ) -> str:
        """Return the status of an approval ticket. Raises NotFoundError if missing."""
        from eaos.core.errors import NotFoundError

        row = await self._db.fetch_one(
            """SELECT status FROM harness.approvals
               WHERE id = :p0 AND tenant_id = :p1""",
            approval_id,
            tenant_id,
        )
        if row is None:
            raise NotFoundError(f"approval {approval_id} not found")
        return str(row["status"])

    async def approve(
        self,
        approval_id: UUID,
        decided_by: UUID,
        tenant_id: UUID | None = None,
    ) -> None:
        """Mark an approval ticket as approved.

        C02/GAP-05: Only pending approvals can be approved. The SQL
        condition ``status = 'pending'`` prevents approving an already-
        decided, expired, or consumed ticket.
        """
        from eaos.core.errors import NotFoundError, PermissionDeniedError

        if tenant_id is None:
            row = await self._db.fetch_one(
                "SELECT requested_by FROM harness.approvals WHERE id = :p0",
                approval_id,
            )
        else:
            row = await self._db.fetch_one(
                """SELECT requested_by FROM harness.approvals
                   WHERE id = :p0 AND tenant_id = :p1""",
                approval_id,
                tenant_id,
            )
        if row is None:
            raise NotFoundError(f"approval {approval_id} not found")
        if row.get("requested_by") == decided_by:
            raise PermissionDeniedError(
                "separation of duties violation: requester cannot approve own write"
            )

        if tenant_id is None:
            await self._db.execute(
                """UPDATE harness.approvals
                   SET status = 'approved', decided_by = :p0, decided_at = :p1
                   WHERE id = :p2 AND status = 'pending'""",
                decided_by,
                datetime.now(UTC),
                approval_id,
            )
        else:
            await self._db.execute(
                """UPDATE harness.approvals
                   SET status = 'approved', decided_by = :p0, decided_at = :p1
                   WHERE id = :p2 AND tenant_id = :p3 AND status = 'pending'""",
                decided_by,
                datetime.now(UTC),
                approval_id,
                tenant_id,
            )

    async def reject(
        self,
        approval_id: UUID,
        decided_by: UUID,
        reason: str,
        tenant_id: UUID | None = None,
    ) -> None:
        """Mark an approval ticket as rejected.

        C02/GAP-05: Only pending approvals can be rejected.
        """
        if tenant_id is None:
            await self._db.execute(
                """UPDATE harness.approvals
                   SET status = 'rejected', decided_by = :p0, decided_at = :p1
                   WHERE id = :p2 AND status = 'pending'""",
                decided_by,
                datetime.now(UTC),
                approval_id,
            )
        else:
            await self._db.execute(
                """UPDATE harness.approvals
                   SET status = 'rejected', decided_by = :p0, decided_at = :p1
                   WHERE id = :p2 AND tenant_id = :p3 AND status = 'pending'""",
                decided_by,
                datetime.now(UTC),
                approval_id,
                tenant_id,
            )

    async def list_pending(self, tenant_id: UUID) -> list[ApprovalRequest]:
        """List all pending approvals for a tenant."""
        rows = await self._db.fetch(
            """SELECT id, tenant_id, agent_id, skill_id, session_id, reason,
                      status, requested_by, decided_by, decided_at, created_at,
                      tool_name, resource, operation, risk_level, intent_data
               FROM harness.approvals
               WHERE tenant_id = :p0 AND status = 'pending'
               ORDER BY created_at DESC""",
            tenant_id,
        )
        return [_row_to_request(r) for r in rows]

    async def list_all(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """List approvals for a tenant with optional status filter + pagination."""
        if status is not None:
            rows = await self._db.fetch(
                """SELECT id, tenant_id, agent_id, skill_id, session_id, reason,
                          status, requested_by, decided_by, decided_at, created_at,
                          tool_name, resource, operation, risk_level, intent_data
                   FROM harness.approvals
                   WHERE tenant_id = :p0 AND status = :p1
                   ORDER BY created_at DESC
                   LIMIT :p2 OFFSET :p3""",
                tenant_id,
                status,
                limit,
                offset,
            )
        else:
            rows = await self._db.fetch(
                """SELECT id, tenant_id, agent_id, skill_id, session_id, reason,
                          status, requested_by, decided_by, decided_at, created_at,
                          tool_name, resource, operation, risk_level, intent_data
                   FROM harness.approvals
                   WHERE tenant_id = :p0
                   ORDER BY created_at DESC
                   LIMIT :p1 OFFSET :p2""",
                tenant_id,
                limit,
                offset,
            )
        return [_row_to_request(r) for r in rows]

    async def count(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
    ) -> int:
        """Count approvals for a tenant, optionally filtered by status."""
        if status is not None:
            row = await self._db.fetch_one(
                """SELECT COUNT(*) AS n FROM harness.approvals
                   WHERE tenant_id = :p0 AND status = :p1""",
                tenant_id,
                status,
            )
        else:
            row = await self._db.fetch_one(
                """SELECT COUNT(*) AS n FROM harness.approvals
                   WHERE tenant_id = :p0""",
                tenant_id,
            )
        return int(row["n"]) if row is not None else 0

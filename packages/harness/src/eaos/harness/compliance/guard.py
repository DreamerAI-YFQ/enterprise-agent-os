"""Pillar 4: Compliance guard — PII redaction, audit, rollback.

Pre-check: input PII redaction (agent never sees raw PII), permission
interception. Post-check: output PII redaction (user never sees leaked PII),
audit log append, rollback snapshot for writable operations.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.harness.context import GuardContext


@dataclass(frozen=True)
class AuditLog:
    """Immutable audit log entry (append-only)."""

    tenant_id: UUID
    actor_type: str  # user/agent/system
    actor_id: UUID
    action: str
    resource_type: str
    resource_id: UUID | None
    detail: dict[str, Any]
    ip_address: str | None = None


class ComplianceDb(Protocol):
    """Minimal DB subset for compliance guard."""

    async def execute(self, sql: str, *params: Any) -> None: ...


# PII redaction patterns
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b\d{16,19}\b"), "[REDACTED_CARD]"),
]


def _redact_pii(text: str) -> str:
    """Redact PII patterns from text."""
    result = text
    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class ComplianceGuard(Protocol):
    """Pillar 4: security compliance."""

    async def pre_check(
        self,
        ctx: GuardContext,
        text: str | None = None,
    ) -> str | None:
        """Pre-action: redact PII from input, check data residency."""
        ...

    async def post_check(
        self,
        ctx: GuardContext,
        text: str,
    ) -> str:
        """Post-action: redact PII from output before returning to user."""
        ...

    async def audit(
        self,
        ctx: GuardContext,
        result: Any,
    ) -> None:
        """Append audit log (immutable, append-only)."""
        ...

    async def snapshot_before(
        self,
        ctx: GuardContext,
        datasource_id: UUID,
        record_id: str,
    ) -> str:
        """Snapshot a record before write (for rollback). Returns snapshot id."""
        ...

    async def rollback(self, snapshot_id: str) -> None:
        """Rollback to a previously snapshotted state."""
        ...


class ComplianceGuardImpl:
    """Concrete ComplianceGuard backed by PostgreSQL + in-memory snapshots.

    PII redaction uses regex patterns for email, phone, SSN, and card numbers.
    Audit logs are written to ``harness.audit_logs`` (append-only).
    Snapshots are stored in-memory (Phase 4 simplification; production would
    use Redis or a dedicated snapshots table).
    """

    def __init__(self, db: ComplianceDb) -> None:
        self._db = db
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def pre_check(
        self,
        ctx: GuardContext,
        text: str | None = None,
    ) -> str | None:
        """Redact PII from input text before the agent sees it."""
        if text is None:
            return None
        return _redact_pii(text)

    async def post_check(
        self,
        ctx: GuardContext,
        text: str,
    ) -> str:
        """Redact PII from output text before returning to user."""
        return _redact_pii(text)

    async def audit(
        self,
        ctx: GuardContext,
        result: Any,
    ) -> None:
        """Append an audit log entry to harness.audit_logs."""
        detail: dict[str, Any]
        if isinstance(result, dict):
            detail = {"result": result}
        elif isinstance(result, str):
            detail = {"result": result[:500]}
        else:
            detail = {"result_type": type(result).__name__}

        await self._db.execute(
            """INSERT INTO harness.audit_logs
               (tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, detail, ip_address)
               VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7)""",
            ctx.tenant_id,
            "agent",
            ctx.agent_id,
            ctx.action or "unknown",
            ctx.resource or "unknown",
            ctx.resource_id,
            json.dumps(detail),
            ctx.attributes.get("ip_address"),
        )

    async def snapshot_before(
        self,
        ctx: GuardContext,
        datasource_id: UUID,
        record_id: str,
    ) -> str:
        """Record a snapshot reference before a writable operation."""
        snapshot_id = str(uuid.uuid4())
        self._snapshots[snapshot_id] = {
            "tenant_id": str(ctx.tenant_id),
            "datasource_id": str(datasource_id),
            "record_id": record_id,
            "action": ctx.action,
            "created_at": ctx.attributes.get("timestamp"),
        }
        return snapshot_id

    async def rollback(self, snapshot_id: str) -> None:
        """Rollback to a previously snapshotted state.

        Phase 4: logs the rollback intent. Actual data restoration is the
        caller's responsibility (datasource connector handles the restore).
        """
        if snapshot_id not in self._snapshots:
            from eaos.core.errors import NotFoundError

            raise NotFoundError(f"snapshot {snapshot_id} not found")
        # In production, this would trigger a datasource-specific rollback.
        # Phase 4: just remove the snapshot (rollback consumed).
        del self._snapshots[snapshot_id]

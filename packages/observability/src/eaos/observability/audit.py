"""AuditLogger — write operation audit trail (Phase 7 T7, Harness pillar #8).

Logs every write operation (create/update/delete) to ``harness.write_audit``
with before/after snapshots, HITL approval linkage, and trace correlation.
Provides query API for admin monitoring and rollback tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from eaos.infra.db.base import DbClient


@dataclass(frozen=True)
class AuditEntry:
    """A single write operation audit record."""

    tenant_id: UUID
    principal_id: UUID
    tool_name: str
    resource: str
    operation: str  # create | update | delete
    success: bool
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    approval_id: UUID | None = None
    trace_id: UUID | None = None
    error: str | None = None
    id: UUID | None = None
    created_at: datetime | None = None
    rolled_back: bool = False
    rollback_reason: str | None = None


@dataclass(frozen=True)
class AuditQuery:
    """Filters for querying audit entries."""

    principal_id: UUID | None = None
    resource: str | None = None
    operation: str | None = None
    time_range: tuple[datetime, datetime] | None = None
    limit: int = 100
    offset: int = 0


class AuditLogger:
    """Persists write operation audit entries to ``harness.write_audit``."""

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def log(self, entry: AuditEntry) -> UUID:
        """Insert an audit entry, return the generated id."""
        import json
        from uuid import uuid4

        entry_id = uuid4()
        await self._db.execute(
            "INSERT INTO harness.write_audit "
            "(id, tenant_id, principal_id, tool_name, resource, operation, "
            "before_state, after_state, approval_id, trace_id, success, error) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11)",
            entry_id,
            entry.tenant_id,
            entry.principal_id,
            entry.tool_name,
            entry.resource,
            entry.operation,
            json.dumps(entry.before) if entry.before is not None else None,
            json.dumps(entry.after) if entry.after is not None else None,
            entry.approval_id,
            entry.trace_id,
            entry.success,
            entry.error,
        )
        return entry_id

    async def log_rollback(
        self,
        original_entry_id: UUID,
        reason: str,
    ) -> None:
        """Mark an audit entry as rolled back with a reason."""
        await self._db.execute(
            "UPDATE harness.write_audit "
            "SET rolled_back = TRUE, rollback_reason = :p1 "
            "WHERE id = :p0",
            original_entry_id,
            reason,
        )

    async def query(
        self,
        tenant_id: UUID,
        filters: AuditQuery | None = None,
    ) -> list[AuditEntry]:
        """Query audit entries for a tenant with optional filters."""
        f = filters or AuditQuery()
        params: list[Any] = [tenant_id]
        sql = (
            "SELECT id, tenant_id, principal_id, tool_name, resource, operation, "
            "before_state, after_state, approval_id, trace_id, success, error, "
            "rolled_back, rollback_reason, created_at "
            "FROM harness.write_audit WHERE tenant_id = :p0"
        )
        if f.principal_id is not None:
            params.append(f.principal_id)
            sql += f" AND principal_id = :p{len(params) - 1}"
        if f.resource is not None:
            params.append(f.resource)
            sql += f" AND resource = :p{len(params) - 1}"
        if f.operation is not None:
            params.append(f.operation)
            sql += f" AND operation = :p{len(params) - 1}"
        if f.time_range is not None:
            params.extend(f.time_range)
            start_idx = len(params) - 2
            end_idx = len(params) - 1
            sql += f" AND created_at >= :p{start_idx} AND created_at <= :p{end_idx}"
        sql += f" ORDER BY created_at DESC LIMIT :p{len(params)} OFFSET :p{len(params) + 1}"
        params.extend([f.limit, f.offset])
        rows = await self._db.fetch(sql, *params)
        return [self._row_to_entry(r) for r in rows]

    async def get(self, entry_id: UUID) -> AuditEntry | None:
        """Fetch a single audit entry by id."""
        row = await self._db.fetch_one(
            "SELECT id, tenant_id, principal_id, tool_name, resource, operation, "
            "before_state, after_state, approval_id, trace_id, success, error, "
            "rolled_back, rollback_reason, created_at "
            "FROM harness.write_audit WHERE id = :p0",
            entry_id,
        )
        return self._row_to_entry(row) if row else None

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> AuditEntry:
        """Convert a DB row dict to an AuditEntry."""
        return AuditEntry(
            id=row["id"],
            tenant_id=row["tenant_id"],
            principal_id=row["principal_id"],
            tool_name=row["tool_name"],
            resource=row["resource"],
            operation=row["operation"],
            success=row["success"],
            before=row.get("before_state"),
            after=row.get("after_state"),
            approval_id=row.get("approval_id"),
            trace_id=row.get("trace_id"),
            error=row.get("error"),
            rolled_back=row.get("rolled_back", False),
            rollback_reason=row.get("rollback_reason"),
            created_at=row.get("created_at"),
        )

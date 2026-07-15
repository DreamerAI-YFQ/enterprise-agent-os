"""harness.write_audit — write operation audit trail (Phase 7 T7).

Independent table for auditing write operations (create/update/delete) through
the WritePipeline. Captures before/after snapshots, HITL approval linkage, and
trace correlation for full accountability and rollback support.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE harness.write_audit (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL,
            principal_id  UUID NOT NULL,
            tool_name     TEXT NOT NULL,
            resource      TEXT NOT NULL,
            operation     TEXT NOT NULL,
            before_state  JSONB,
            after_state   JSONB,
            approval_id   UUID,
            trace_id      UUID,
            success       BOOLEAN NOT NULL,
            error         TEXT,
            rolled_back   BOOLEAN NOT NULL DEFAULT FALSE,
            rollback_reason TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_write_audit_op CHECK (operation IN ('create', 'update', 'delete'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_write_audit_tenant_time "
        "ON harness.write_audit(tenant_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_write_audit_principal "
        "ON harness.write_audit(principal_id)"
    )
    op.execute(
        "CREATE INDEX idx_write_audit_trace "
        "ON harness.write_audit(trace_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS harness.write_audit CASCADE")

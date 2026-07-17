"""Persist write execution context for evidence and restart-safe idempotency.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-17
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE harness.write_audit
            ADD COLUMN IF NOT EXISTS session_id UUID,
            ADD COLUMN IF NOT EXISTS idempotency_key TEXT
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_write_audit_session "
        "ON harness.write_audit(tenant_id, session_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_write_audit_idempotency "
        "ON harness.write_audit(tenant_id, tool_name, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        """
        INSERT INTO iam.permissions (tenant_id, role, resource, action, "constraint")
        SELECT id, 'employee', 'orders', action, '{}'::jsonb
        FROM iam.tenants
        CROSS JOIN (VALUES ('submit_write'), ('execute_approved_write')) AS a(action)
        ON CONFLICT (tenant_id, role, resource, action) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """DELETE FROM iam.permissions
           WHERE role = 'employee' AND resource = 'orders'
             AND action IN ('submit_write', 'execute_approved_write')"""
    )
    op.execute("DROP INDEX IF EXISTS harness.idx_write_audit_idempotency")
    op.execute("DROP INDEX IF EXISTS harness.idx_write_audit_session")
    op.execute(
        """
        ALTER TABLE harness.write_audit
            DROP COLUMN IF EXISTS idempotency_key,
            DROP COLUMN IF EXISTS session_id
        """
    )

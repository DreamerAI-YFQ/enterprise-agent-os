"""harness.policies and harness.approvals — policy lifecycle and HITL approvals.

Policies are versioned governance rules (draft -> shadow -> active -> rollback).
Approvals are human-in-the-loop tickets for high-risk agent actions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE harness.policies (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name        VARCHAR(100) NOT NULL,
            version     VARCHAR(30) NOT NULL,
            content     JSONB NOT NULL,
            status      VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name, version)
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_policies_name ON harness.policies(tenant_id, name, status)'
    )

    op.execute(
        """
        CREATE TABLE harness.approvals (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL,
            agent_id     UUID NOT NULL,
            skill_id     UUID,
            session_id   UUID NOT NULL,
            reason       VARCHAR(200) NOT NULL,
            status       VARCHAR(20) NOT NULL DEFAULT 'pending',
            requested_by UUID NOT NULL,
            decided_by   UUID,
            decided_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_approvals_pending '
        "ON harness.approvals(tenant_id, status) WHERE status='pending'"
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS harness.approvals CASCADE')
    op.execute('DROP TABLE IF EXISTS harness.policies CASCADE')

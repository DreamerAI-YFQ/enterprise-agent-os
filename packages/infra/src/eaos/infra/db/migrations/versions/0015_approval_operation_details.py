"""harness.approvals — add operation detail columns for HITL visibility.

P0-T2: Approval cards must show the full operation context (tool, resource,
operation, risk level, intent data) — not just the trigger reason. These
columns are populated by ApprovalGateImpl.request_approval() when a high-risk
write is intercepted.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE harness.approvals
            ADD COLUMN IF NOT EXISTS tool_name   VARCHAR(200),
            ADD COLUMN IF NOT EXISTS resource    VARCHAR(200),
            ADD COLUMN IF NOT EXISTS operation   VARCHAR(50),
            ADD COLUMN IF NOT EXISTS risk_level  VARCHAR(20),
            ADD COLUMN IF NOT EXISTS intent_data JSONB
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE harness.approvals
            DROP COLUMN IF EXISTS intent_data,
            DROP COLUMN IF EXISTS risk_level,
            DROP COLUMN IF EXISTS operation,
            DROP COLUMN IF EXISTS resource,
            DROP COLUMN IF EXISTS tool_name
        """
    )

"""harness.safety_cases — dynamic compliance test cases for evolution guardrail.

Stores tenant-specific safety benchmark cases that override/augment the
default YAML file. The guardrail checker queries DB first (enabled cases
for the tenant), falling back to the bundled safety_cases.yaml when no
DB cases exist.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE harness.safety_cases (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL,
            category    VARCHAR(50) NOT NULL,
            prompt      TEXT NOT NULL,
            expected    VARCHAR(20) NOT NULL,
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_safety_cases_tenant '
        "ON harness.safety_cases(tenant_id, enabled) WHERE enabled = TRUE"
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS harness.safety_cases CASCADE')

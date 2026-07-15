"""erp/crm tenant isolation — add tenant_id to mock ERP/CRM tables (Phase 7 T6).

Fixes gap #7: connectors had no tenant isolation. Adds ``tenant_id`` column
to all erp/crm tables so ErpConnector/CrmConnector can enforce row-level
isolation in read/write/rollback SQL. Existing rows backfilled to a sentinel
default tenant.

Tables affected:
  erp.products, erp.customers, erp.orders, erp.inventory
  crm.leads, crm.opportunities, crm.activities

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_TENANT = "'00000000-0000-0000-0000-000000000001'::uuid"

_TABLES = [
    "erp.products",
    "erp.customers",
    "erp.orders",
    "erp.inventory",
    "crm.leads",
    "crm.opportunities",
    "crm.activities",
]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL "
            f"DEFAULT {_DEFAULT_TENANT}"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table.replace('.', '_')}_tenant "
            f"ON {table}(tenant_id)"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"DROP INDEX IF EXISTS idx_{table.replace('.', '_')}_tenant"
        )
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id")

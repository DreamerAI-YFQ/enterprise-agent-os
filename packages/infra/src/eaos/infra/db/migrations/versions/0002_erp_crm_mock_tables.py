"""erp/crm mock tables: simulated external ERP/CRM data sources.

Creates two schemas (erp, crm) with 7 tables total for M2 verification.
These tables simulate external ERP/CRM data sources — they intentionally
have NO tenant_id column (mock shared data). Phase 3 will add tenant isolation.

Tables:
  erp.products, erp.customers, erp.orders, erp.inventory
  crm.leads, crm.opportunities, crm.activities

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ERP schema ---
    op.execute('CREATE SCHEMA IF NOT EXISTS erp')

    op.execute(
        """
        CREATE TABLE erp.products (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sku          VARCHAR(50) NOT NULL UNIQUE,
            name         VARCHAR(200) NOT NULL,
            category     VARCHAR(50) NOT NULL,
            unit_price   NUMERIC(12,2) NOT NULL,
            cost         NUMERIC(12,2) NOT NULL,
            status       VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_product_status CHECK (status IN ('active', 'discontinued'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE erp.customers (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code          VARCHAR(50) NOT NULL UNIQUE,
            name          VARCHAR(200) NOT NULL,
            industry      VARCHAR(50) NOT NULL,
            contact_name  VARCHAR(100),
            contact_email VARCHAR(255),
            credit_limit  NUMERIC(14,2) NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE erp.orders (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            order_no    VARCHAR(50) NOT NULL UNIQUE,
            customer_id UUID NOT NULL REFERENCES erp.customers(id) ON DELETE RESTRICT,
            product_id  UUID NOT NULL REFERENCES erp.products(id) ON DELETE RESTRICT,
            quantity    INTEGER NOT NULL CHECK (quantity > 0),
            unit_price  NUMERIC(12,2) NOT NULL,
            amount      NUMERIC(14,2) NOT NULL,
            status      VARCHAR(20) NOT NULL DEFAULT 'pending',
            order_date  DATE NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_order_status CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'overdue', 'cancelled'))
        )
        """
    )
    op.execute('CREATE INDEX idx_orders_customer ON erp.orders(customer_id)')
    op.execute('CREATE INDEX idx_orders_product ON erp.orders(product_id)')
    op.execute('CREATE INDEX idx_orders_date ON erp.orders(order_date)')
    op.execute('CREATE INDEX idx_orders_status ON erp.orders(status)')

    op.execute(
        """
        CREATE TABLE erp.inventory (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id   UUID NOT NULL REFERENCES erp.products(id) ON DELETE CASCADE,
            warehouse    VARCHAR(50) NOT NULL,
            quantity     INTEGER NOT NULL DEFAULT 0,
            safety_stock INTEGER NOT NULL DEFAULT 0,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX idx_inventory_product ON erp.inventory(product_id)')

    # --- CRM schema ---
    op.execute('CREATE SCHEMA IF NOT EXISTS crm')

    op.execute(
        """
        CREATE TABLE crm.leads (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            lead_name                   VARCHAR(200) NOT NULL,
            source                      VARCHAR(50) NOT NULL,
            status                      VARCHAR(20) NOT NULL DEFAULT 'new',
            converted_to_opportunity_id UUID,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_lead_status CHECK (status IN ('new', 'contacted', 'qualified', 'converted', 'lost'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE crm.opportunities (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            opp_name           VARCHAR(200) NOT NULL,
            customer_id        UUID,  -- soft ref to erp.customers (cross-schema, not FK-enforced)
            amount             NUMERIC(14,2) NOT NULL DEFAULT 0,
            stage              VARCHAR(20) NOT NULL DEFAULT 'prospecting',
            expected_close_date DATE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_opp_stage CHECK (stage IN ('prospecting', 'qualification', 'proposal', 'negotiation', 'won', 'lost'))
        )
        """
    )
    op.execute('CREATE INDEX idx_opp_customer ON crm.opportunities(customer_id)')
    op.execute('CREATE INDEX idx_opp_stage ON crm.opportunities(stage)')

    # Back-link leads.converted_to_opportunity_id now that opportunities exists.
    op.execute(
        'ALTER TABLE crm.leads '
        'ADD CONSTRAINT fk_lead_converted '
        'FOREIGN KEY (converted_to_opportunity_id) REFERENCES crm.opportunities(id) ON DELETE SET NULL'
    )

    op.execute(
        """
        CREATE TABLE crm.activities (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            opportunity_id  UUID NOT NULL REFERENCES crm.opportunities(id) ON DELETE CASCADE,
            activity_type   VARCHAR(30) NOT NULL,
            description     TEXT,
            performed_by    VARCHAR(100),
            activity_date   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute('CREATE INDEX idx_activities_opp ON crm.activities(opportunity_id)')
    op.execute('CREATE INDEX idx_activities_date ON crm.activities(activity_date)')


def downgrade() -> None:
    op.execute('DROP SCHEMA IF EXISTS crm CASCADE')
    op.execute('DROP SCHEMA IF EXISTS erp CASCADE')

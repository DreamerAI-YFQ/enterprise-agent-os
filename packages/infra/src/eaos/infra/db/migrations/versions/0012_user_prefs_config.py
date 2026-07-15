"""user preferences + config schema (settings, report_templates) — Phase 8 F0-T12/T14.

Adds a ``preferences`` JSONB column to ``iam.users`` for personal settings
(theme, language, notification rules). Creates a ``config`` schema with:
- ``config.settings`` — key-value store for model configs and plugin configs
- ``config.report_templates`` — reusable report template definitions

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. User preferences column
    op.execute(
        "ALTER TABLE iam.users "
        "ADD COLUMN IF NOT EXISTS preferences JSONB NOT NULL DEFAULT '{}'::jsonb"
    )

    # 2. Config schema — system-wide settings and templates
    op.execute('CREATE SCHEMA IF NOT EXISTS config')

    op.execute(
        """
        CREATE TABLE config.settings (
            tenant_id    UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            key          VARCHAR(100) NOT NULL,
            value        JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(tenant_id, key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE config.report_templates (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name          VARCHAR(200) NOT NULL,
            description   TEXT,
            template_type VARCHAR(50) NOT NULL DEFAULT 'generic',
            content       JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by    UUID,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name)
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_report_templates_tenant '
        'ON config.report_templates(tenant_id, template_type)'
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS config.report_templates CASCADE")
    op.execute("DROP TABLE IF EXISTS config.settings CASCADE")
    op.execute('DROP SCHEMA IF EXISTS config')
    op.execute("ALTER TABLE iam.users DROP COLUMN IF EXISTS preferences")

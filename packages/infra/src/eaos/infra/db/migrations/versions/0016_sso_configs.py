"""SSO configurations table — Phase 4 P3-T1.

Adds ``iam.sso_configs`` to store per-tenant SSO/OIDC/LDAP identity provider
configurations. Each row is one provider binding (tenant may have multiple).
The ``config`` JSONB column stores provider-specific payload (e.g. OIDC
client_secret, LDAP bind DN, SAML IdP metadata XML).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-14
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE iam.sso_configs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name          VARCHAR(100) NOT NULL,
            provider_type VARCHAR(20) NOT NULL,
            provider_key  VARCHAR(100) NOT NULL,
            config        JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled       BOOLEAN NOT NULL DEFAULT true,
            jit_provision BOOLEAN NOT NULL DEFAULT true,
            default_role  VARCHAR(30) NOT NULL DEFAULT 'employee',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, provider_type, provider_key),
            CONSTRAINT chk_sso_provider_type CHECK (
                provider_type IN ('oidc', 'saml', 'ldap')
            )
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_sso_configs_tenant ON iam.sso_configs(tenant_id, enabled)'
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.sso_configs CASCADE")

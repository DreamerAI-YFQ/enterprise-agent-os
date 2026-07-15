"""data.external_connections — external MCP/HTTP connection registry (Phase 7 T5).

Stores tenant-scoped external connections (MCP servers, HTTP API endpoints)
with encrypted credentials. ConnectionManager (T5) reads this table to
resolve live McpClient / HttpApiConnector instances.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.external_connections (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            name                   TEXT NOT NULL,
            type                   TEXT NOT NULL,
            config                 JSONB NOT NULL DEFAULT '{}'::jsonb,
            credentials_encrypted  BYTEA,
            health_status          TEXT NOT NULL DEFAULT 'unknown',
            last_health_check      TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(tenant_id, name),
            CONSTRAINT chk_ext_conn_type CHECK (type IN ('mcp_stdio', 'mcp_sse', 'mcp_http', 'http_api')),
            CONSTRAINT chk_ext_conn_health CHECK (health_status IN ('healthy', 'unhealthy', 'unknown'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_external_connections_tenant "
        "ON data.external_connections(tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.external_connections CASCADE")

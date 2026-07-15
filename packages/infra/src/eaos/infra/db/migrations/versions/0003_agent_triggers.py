"""agent.triggers table — proactive ambient monitoring configuration.

Stores trigger configs for ambient agents: threshold breaches, stale tasks,
new events, scheduled reports. Each trigger binds an agent to a notify channel
and a check interval.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent.triggers (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            agent_id      UUID NOT NULL REFERENCES agent.agents(id) ON DELETE CASCADE,
            trigger_type  VARCHAR(32) NOT NULL,
            condition     JSONB NOT NULL,
            notify_channel VARCHAR(64) NOT NULL,
            interval_sec  INTEGER NOT NULL DEFAULT 300,
            enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            last_fired_at TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_trigger_type CHECK (
                trigger_type IN ('threshold', 'stale_task', 'new_event', 'scheduled')
            )
        )
        """
    )
    op.execute(
        'CREATE INDEX idx_triggers_tenant_agent '
        'ON agent.triggers(tenant_id, agent_id) WHERE enabled'
    )
    op.execute(
        'CREATE INDEX idx_triggers_type '
        'ON agent.triggers(trigger_type) WHERE enabled'
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS agent.triggers CASCADE')

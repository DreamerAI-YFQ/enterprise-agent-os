"""agent.messages — persistent chat history for agent sessions.

Phase 8 F0-T3: previously messages existed only in LangGraph's in-memory
MemorySaver and were lost on restart. This table persists the user prompt and
the agent's final response for each turn so the frontend can render history,
and users can resume conversations across restarts.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent.messages (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id    UUID NOT NULL REFERENCES agent.sessions(id) ON DELETE CASCADE,
            tenant_id     UUID NOT NULL,
            role          VARCHAR(20) NOT NULL,
            content       TEXT NOT NULL,
            event_type    VARCHAR(40),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_message_role CHECK (role IN ('user', 'assistant', 'system', 'tool'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_messages_session "
        "ON agent.messages(session_id, created_at ASC)"
    )
    op.execute(
        "CREATE INDEX idx_messages_tenant_time "
        "ON agent.messages(tenant_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent.messages CASCADE")

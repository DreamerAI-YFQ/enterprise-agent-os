"""iam.notifications — persisted user notifications.

Phase 8 F0-T5: the existing ``Notifier`` Protocol was push-only (fire-and-forget
to IM channels). This table adds persistence so the frontend can list unread
notifications, mark them read, and show a notification bell badge. Ambient
triggers, approval status changes, and system alerts can all write rows here.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE iam.notifications (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL,
            user_id             UUID NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
            type                VARCHAR(40) NOT NULL,
            title               VARCHAR(200) NOT NULL,
            body                TEXT,
            related_entity_type VARCHAR(40),
            related_entity_id   UUID,
            read_at             TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_notifications_user_unread "
        "ON iam.notifications(user_id, created_at DESC) "
        "WHERE read_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_notifications_user_all "
        "ON iam.notifications(user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_notifications_tenant "
        "ON iam.notifications(tenant_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.notifications CASCADE")

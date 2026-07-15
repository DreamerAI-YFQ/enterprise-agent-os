"""knowledge.contributions — employee knowledge contribution submissions + admin review.

Employees submit knowledge documents (text or file-backed) for admin review.
Approved submissions are ingested into the indexed ``knowledge.documents``
table via the RAG pipeline; rejected submissions retain the reviewer's
comment for the submitter to see. The row remains as an audit record even
after approval (the indexed document lives separately in ``knowledge.documents``).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.contributions (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES iam.tenants(id) ON DELETE CASCADE,
            submitter_id   UUID NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
            source_type    VARCHAR(50) NOT NULL DEFAULT 'manual',
            source_uri     TEXT,
            title          VARCHAR(200) NOT NULL,
            content        TEXT NOT NULL,
            status         VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewer_id    UUID REFERENCES iam.users(id),
            review_comment TEXT,
            submitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at    TIMESTAMPTZ,
            metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_contrib_tenant_status "
        "ON knowledge.contributions(tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_contrib_submitter "
        "ON knowledge.contributions(submitter_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge.contributions CASCADE")

"""add scope/owner_id to knowledge.documents and knowledge.chunks

Adds three-tier scope (personal/department/enterprise) to knowledge documents
and chunks, enabling scope-based RAG retrieval filtering.

Default 'enterprise' ensures existing documents remain visible to all users.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add scope + owner_id to documents
    op.execute(
        "ALTER TABLE knowledge.documents "
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(20) NOT NULL DEFAULT 'enterprise'"
    )
    op.execute(
        "ALTER TABLE knowledge.documents "
        "ADD COLUMN IF NOT EXISTS owner_id UUID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_scope "
        "ON knowledge.documents(tenant_id, scope)"
    )

    # Add scope + owner_id to chunks (denormalized for vector search filtering)
    op.execute(
        "ALTER TABLE knowledge.chunks "
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(20) NOT NULL DEFAULT 'enterprise'"
    )
    op.execute(
        "ALTER TABLE knowledge.chunks "
        "ADD COLUMN IF NOT EXISTS owner_id UUID"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_scope "
        "ON knowledge.chunks(tenant_id, scope)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_scope")
    op.execute("ALTER TABLE knowledge.chunks DROP COLUMN IF EXISTS owner_id")
    op.execute("ALTER TABLE knowledge.chunks DROP COLUMN IF EXISTS scope")

    op.execute("DROP INDEX IF EXISTS idx_documents_scope")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS owner_id")
    op.execute("ALTER TABLE knowledge.documents DROP COLUMN IF EXISTS scope")

"""Add local password hashes to IAM users.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-17

The column remains nullable because SSO/JIT-provisioned identities do not have
local credentials.  Local login treats NULL and malformed values as invalid,
so existing accounts fail closed until an administrator resets their password
or the demo seed is rerun.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE iam.users "
        "ADD COLUMN IF NOT EXISTS password_hash TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE iam.users "
        "DROP COLUMN IF EXISTS password_hash"
    )

"""skills.skills — extract instructions/tools/tool_bindings to independent columns.

Phase 7 T4: previously ``instructions`` and ``tools`` were packed inside the
``guardrail`` JSONB blob, making them unindexable and unvalidatable. This
migration adds three independent columns (``instructions``, ``tools``,
``tool_bindings``) and migrates data out of the blob. The ``guardrail`` column
is retained but now stores only guardrail config (confirm_required, notify
channels, rollback_enabled).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE skills.skills
          ADD COLUMN instructions TEXT,
          ADD COLUMN tools JSONB NOT NULL DEFAULT '[]'::jsonb,
          ADD COLUMN tool_bindings JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )
    # Migrate instructions + tools out of the guardrail blob (if present).
    op.execute(
        """
        UPDATE skills.skills
        SET instructions = (guardrail->>'instructions')::text,
            tools = COALESCE((guardrail->>'tools')::jsonb, '[]'::jsonb)
        WHERE guardrail IS NOT NULL
          AND guardrail->>'instructions' IS NOT NULL
        """
    )
    # Fill instructions for rows that had no guardrail blob.
    op.execute(
        """
        UPDATE skills.skills
        SET instructions = ''
        WHERE instructions IS NULL
        """
    )
    # Strip instructions/tools keys from guardrail blob; keep only guardrail config.
    op.execute(
        """
        UPDATE skills.skills
        SET guardrail = guardrail - 'instructions' - 'tools'
        WHERE guardrail IS NOT NULL
        """
    )


def downgrade() -> None:
    # Push instructions/tools back into the guardrail blob for compat.
    op.execute(
        """
        UPDATE skills.skills
        SET guardrail = CASE
            WHEN guardrail IS NULL THEN jsonb_build_object(
                'instructions', instructions,
                'tools', tools
            )
            ELSE guardrail
                || jsonb_build_object('instructions', instructions)
                || jsonb_build_object('tools', tools)
        END
        """
    )
    op.execute(
        """
        ALTER TABLE skills.skills
          DROP COLUMN tool_bindings,
          DROP COLUMN tools,
          DROP COLUMN instructions
        """
    )

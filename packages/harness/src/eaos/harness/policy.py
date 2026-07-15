"""Policy engine — policy as code with lifecycle (draft -> shadow -> active -> rollback).

Policies are YAML-defined, version-controlled, and go through: local test ->
PR review -> shadow mode (observe only) -> canary -> full -> rollback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class PolicyDb(Protocol):
    """Minimal DB subset for policy management."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class PolicyStatus(StrEnum):
    """Policy lifecycle status."""

    DRAFT = "draft"
    SHADOW = "shadow"  # observe-only, no enforcement
    ACTIVE = "active"  # enforcing
    ROLLBACK = "rollback"  # rolled back to previous version


@dataclass(frozen=True)
class Policy:
    """A governance policy definition."""

    name: str  # e.g. "capability.personal_agent"
    version: str
    content: dict[str, Any]  # YAML parsed
    status: PolicyStatus = PolicyStatus.DRAFT
    tenant_id: UUID | None = None  # None = global default


class PolicyEngine(Protocol):
    """Policy as code engine."""

    async def load(
        self,
        policy_name: str,
        version: str | None = None,
        tenant_id: UUID | None = None,
    ) -> Policy:
        """Load a policy (latest or specific version)."""
        ...

    async def publish(self, policy: Policy) -> None:
        """Publish a new policy version."""
        ...

    async def activate(self, policy_name: str, version: str) -> None:
        """Activate a published version (deactivates previous)."""
        ...

    async def rollback(self, policy_name: str, to_version: str) -> None:
        """Rollback to a previous version."""
        ...

    async def shadow_mode(self, policy: Policy) -> None:
        """Put policy in shadow mode (observe, don't enforce)."""
        ...

    async def list_versions(
        self,
        policy_name: str,
        tenant_id: UUID | None = None,
    ) -> list[Policy]:
        """List all versions of a policy."""
        ...


def _row_to_policy(row: dict[str, Any]) -> Policy:
    """Map a DB row to a Policy."""
    content = row.get("content")
    if isinstance(content, str):
        content = json.loads(content)
    tenant_id = row.get("tenant_id")
    return Policy(
        name=str(row["name"]),
        version=str(row["version"]),
        content=dict(content) if content else {},
        status=PolicyStatus(str(row["status"])),
        tenant_id=tenant_id,
    )


class PolicyEngineImpl:
    """Concrete PolicyEngine backed by PostgreSQL.

    Stores versioned policies in ``harness.policies``. Only one version per
    (tenant, name) may be ``active`` at a time; activating a new version
    rolls back the previous active one.
    """

    def __init__(self, db: PolicyDb) -> None:
        self._db = db

    async def load(
        self,
        policy_name: str,
        version: str | None = None,
        tenant_id: UUID | None = None,
    ) -> Policy:
        """Load a policy (latest active or specific version)."""
        if version is not None:
            row = await self._db.fetch_one(
                """SELECT id, tenant_id, name, version, content, status
                   FROM harness.policies
                   WHERE name = :p0 AND version = :p1
                     AND (tenant_id IS NOT DISTINCT FROM :p2)""",
                policy_name,
                version,
                tenant_id,
            )
        else:
            row = await self._db.fetch_one(
                """SELECT id, tenant_id, name, version, content, status
                   FROM harness.policies
                   WHERE name = :p0 AND status = 'active'
                     AND (tenant_id IS NOT DISTINCT FROM :p1)
                   ORDER BY created_at DESC LIMIT 1""",
                policy_name,
                tenant_id,
            )
        if row is None:
            from eaos.core.errors import NotFoundError

            raise NotFoundError(f"policy '{policy_name}' not found")
        return _row_to_policy(row)

    async def publish(self, policy: Policy) -> None:
        """Publish a new policy version (status=draft)."""
        await self._db.execute(
            """INSERT INTO harness.policies (tenant_id, name, version, content, status)
               VALUES (:p0, :p1, :p2, CAST(:p3 AS jsonb), :p4)""",
            policy.tenant_id,
            policy.name,
            policy.version,
            json.dumps(policy.content),
            str(PolicyStatus.DRAFT.value),
        )

    async def activate(self, policy_name: str, version: str) -> None:
        """Activate a published version (deactivates previous active)."""
        await self._db.execute(
            """UPDATE harness.policies SET status = 'rollback'
               WHERE name = :p0 AND status = 'active'""",
            policy_name,
        )
        await self._db.execute(
            """UPDATE harness.policies SET status = 'active'
               WHERE name = :p0 AND version = :p1""",
            policy_name,
            version,
        )

    async def rollback(self, policy_name: str, to_version: str) -> None:
        """Rollback to a previous version."""
        await self._db.execute(
            """UPDATE harness.policies SET status = 'rollback'
               WHERE name = :p0 AND status = 'active'""",
            policy_name,
        )
        await self._db.execute(
            """UPDATE harness.policies SET status = 'active'
               WHERE name = :p0 AND version = :p1""",
            policy_name,
            to_version,
        )

    async def shadow_mode(self, policy: Policy) -> None:
        """Put policy in shadow mode (observe, don't enforce)."""
        await self._db.execute(
            """UPDATE harness.policies SET status = 'shadow'
               WHERE name = :p0 AND version = :p1""",
            policy.name,
            policy.version,
        )

    async def list_versions(
        self,
        policy_name: str,
        tenant_id: UUID | None = None,
    ) -> list[Policy]:
        """List all versions of a policy."""
        rows = await self._db.fetch(
            """SELECT id, tenant_id, name, version, content, status
               FROM harness.policies
               WHERE name = :p0 AND (tenant_id IS NOT DISTINCT FROM :p1)
               ORDER BY created_at DESC""",
            policy_name,
            tenant_id,
        )
        return [_row_to_policy(r) for r in rows]

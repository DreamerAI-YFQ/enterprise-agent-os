"""Pillar 2: Permission evaluator (RBAC + ABAC).

RBAC: role-based (employee/manager/admin) controls what resources a user can
access. ABAC: attribute-based (data sensitivity, risk level, time window)
controls individual operation execution. Combined: user -> RBAC -> agent ->
ABAC -> skill -> datasource.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from eaos.core.errors import PermissionDeniedError

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.harness.context import GuardContext


@dataclass(frozen=True)
class Permission:
    """A single permission entry in the RBAC matrix."""

    tenant_id: UUID
    role: str  # employee/manager/admin
    resource: str  # agent/skill/datasource/...
    action: str  # read/create/update/delete/execute
    constraint: dict[str, Any] | None = None  # ABAC constraint (e.g. {"scope": "personal"})


@dataclass(frozen=True)
class DelegationRecord:
    """Audit record: who authorized agent to do what."""

    tenant_id: UUID
    delegator_id: UUID  # the user
    delegatee_id: UUID  # the agent
    action: str
    resource: str
    resource_id: UUID | None
    permission_basis: str  # "rbac:manager" or "abac:rule_xxx"
    timestamp: object  # datetime


class PermissionDb(Protocol):
    """Minimal DB subset for permission evaluation."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class PermissionEvaluator(Protocol):
    """Pillar 2: RBAC + ABAC permission evaluation."""

    async def evaluate(self, ctx: GuardContext) -> None:
        """Evaluate both RBAC and ABAC. Raises PermissionDeniedError on failure."""
        ...

    async def check_rbac(
        self,
        tenant_id: UUID,
        role: str,
        resource: str,
        action: str,
    ) -> bool:
        """Check RBAC matrix."""
        ...

    async def check_abac(
        self,
        ctx: GuardContext,
        constraints: list[dict[str, Any]],
    ) -> bool:
        """Check ABAC rules against context attributes."""
        ...

    async def record_delegation(
        self,
        record: DelegationRecord,
    ) -> None:
        """Record a delegation for audit trail."""
        ...


class PermissionEvaluatorImpl:
    """Concrete PermissionEvaluator backed by PostgreSQL.

    Queries ``iam.permissions`` for RBAC checks and ``iam.users`` for role
    resolution. Admin role short-circuits to allow-all (performance optimisation).
    ABAC constraints supported: ``{"scope":"own"}`` (owner check),
    ``{"scope":"personal"}`` (agent_scope check), ``{"dept":true}`` (department
    membership check).
    """

    def __init__(self, db: PermissionDb) -> None:
        self._db = db

    async def evaluate(self, ctx: GuardContext) -> None:
        """Evaluate RBAC + ABAC. Raises PermissionDeniedError on failure."""
        role = await self._lookup_role(ctx.tenant_id, ctx.user_id)

        if role == "admin":
            return  # admin short-circuit

        if not ctx.resource or not ctx.action:
            raise PermissionDeniedError(
                f"missing resource/action in guard context for user {ctx.user_id}"
            )

        row = await self._db.fetch_one(
            """SELECT "constraint" FROM iam.permissions
               WHERE tenant_id = :p0 AND role = :p1
                 AND resource = :p2 AND action = :p3""",
            ctx.tenant_id,
            role,
            ctx.resource,
            ctx.action,
        )

        if row is None:
            raise PermissionDeniedError(
                f"RBAC denied: role='{role}' resource='{ctx.resource}' action='{ctx.action}'"
            )

        constraint = row.get("constraint")
        if constraint:
            constraints = [constraint] if isinstance(constraint, dict) else []
            if constraints and not await self.check_abac(ctx, constraints):
                raise PermissionDeniedError(
                    f"ABAC denied: role='{role}' resource='{ctx.resource}' "
                    f"action='{ctx.action}' constraint={constraint}"
                )

    async def check_rbac(
        self,
        tenant_id: UUID,
        role: str,
        resource: str,
        action: str,
    ) -> bool:
        """Check RBAC matrix. Admin role short-circuits to True."""
        if role == "admin":
            return True
        row = await self._db.fetch_one(
            """SELECT 1 FROM iam.permissions
               WHERE tenant_id = :p0 AND role = :p1
                 AND resource = :p2 AND action = :p3""",
            tenant_id,
            role,
            resource,
            action,
        )
        return row is not None

    async def check_abac(
        self,
        ctx: GuardContext,
        constraints: list[dict[str, Any]],
    ) -> bool:
        """Check ABAC rules against context attributes.

        Supported constraints:
        - ``{"scope": "own"}``: resource owner must be the current user
        - ``{"scope": "personal"}``: agent_scope must be 'personal'
        - ``{"dept": true}``: resource department must be in user's departments
        """
        for constraint in constraints:
            scope = constraint.get("scope")
            if scope == "own":
                owner_id = ctx.attributes.get("owner_id")
                if owner_id is None or str(owner_id) != str(ctx.user_id):
                    return False
            elif scope == "personal":
                if ctx.agent_scope != "personal":
                    return False

            if constraint.get("dept"):
                res_dept = ctx.attributes.get("department_id")
                if res_dept is None:
                    return False
                if not any(str(res_dept) == str(d) for d in ctx.department_ids):
                    return False

        return True

    async def record_delegation(self, record: DelegationRecord) -> None:
        """Record a delegation audit entry in harness.audit_logs."""
        detail = {
            "delegator_id": str(record.delegator_id),
            "delegatee_id": str(record.delegatee_id),
            "permission_basis": record.permission_basis,
            "resource_id": str(record.resource_id) if record.resource_id else None,
        }
        await self._db.execute(
            """INSERT INTO harness.audit_logs
               (tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, detail)
               VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)""",
            record.tenant_id,
            "user",
            record.delegator_id,
            record.action,
            record.resource,
            record.resource_id,
            json.dumps(detail),
        )

    async def _lookup_role(self, tenant_id: UUID, user_id: UUID) -> str:
        """Look up a user's role from iam.users."""
        row = await self._db.fetch_one(
            "SELECT role FROM iam.users WHERE tenant_id = :p0 AND id = :p1",
            tenant_id,
            user_id,
        )
        if row is None:
            raise PermissionDeniedError(f"user {user_id} not found in tenant {tenant_id}")
        return str(row["role"])

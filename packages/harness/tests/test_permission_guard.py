"""Tests for PermissionEvaluatorImpl — RBAC + ABAC enforcement."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from eaos.core.errors import PermissionDeniedError
from eaos.harness.context import GuardContext
from eaos.harness.permission.evaluator import (
    DelegationRecord,
    PermissionEvaluatorImpl,
)


class FakePermissionDb:
    """In-memory PermissionDb with configurable query results."""

    def __init__(
        self,
        user_role: str | None = "employee",
        permission_row: dict[str, Any] | None = None,
    ) -> None:
        self._user_role = user_role
        self._permission_row = permission_row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        if "iam.users" in sql:
            if self._user_role is None:
                return None
            return {"role": self._user_role}
        if "iam.permissions" in sql:
            return self._permission_row
        return None

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _ctx(
    *,
    user_id: Any = None,
    role: str = "employee",
    resource: str = "agent",
    action: str = "invoke",
    agent_scope: str = "personal",
    department_ids: list[Any] | None = None,
    attributes: dict[str, Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=user_id or uuid4(),
        agent_id=uuid4(),
        agent_scope=agent_scope,
        department_ids=department_ids or [],
        action=action,
        resource=resource,
        attributes=attributes or {},
    )


class TestEvaluate:
    async def test_admin_short_circuits(self) -> None:
        db = FakePermissionDb(user_role="admin")
        evaluator = PermissionEvaluatorImpl(db)
        ctx = _ctx()

        await evaluator.evaluate(ctx)  # should not raise

    async def test_rbac_allowed_no_constraint(self) -> None:
        db = FakePermissionDb(
            user_role="manager",
            permission_row={"constraint": None},
        )
        evaluator = PermissionEvaluatorImpl(db)

        await evaluator.evaluate(_ctx(role="manager"))  # should not raise

    async def test_rbac_denied_raises(self) -> None:
        db = FakePermissionDb(
            user_role="employee",
            permission_row=None,
        )
        evaluator = PermissionEvaluatorImpl(db)

        with pytest.raises(PermissionDeniedError, match="RBAC denied"):
            await evaluator.evaluate(_ctx(role="employee"))

    async def test_user_not_found_raises(self) -> None:
        db = FakePermissionDb(user_role=None)
        evaluator = PermissionEvaluatorImpl(db)

        with pytest.raises(PermissionDeniedError, match="not found"):
            await evaluator.evaluate(_ctx())

    async def test_missing_resource_raises(self) -> None:
        db = FakePermissionDb(user_role="employee")
        evaluator = PermissionEvaluatorImpl(db)
        ctx = GuardContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="personal",
            action="invoke",
            resource="",
        )

        with pytest.raises(PermissionDeniedError, match="missing resource"):
            await evaluator.evaluate(ctx)

    async def test_abac_scope_own_allowed(self) -> None:
        user_id = uuid4()
        db = FakePermissionDb(
            user_role="employee",
            permission_row={"constraint": {"scope": "own"}},
        )
        evaluator = PermissionEvaluatorImpl(db)
        ctx = _ctx(user_id=user_id, attributes={"owner_id": user_id})

        await evaluator.evaluate(ctx)  # should not raise

    async def test_abac_scope_own_denied(self) -> None:
        db = FakePermissionDb(
            user_role="employee",
            permission_row={"constraint": {"scope": "own"}},
        )
        evaluator = PermissionEvaluatorImpl(db)
        ctx = _ctx(user_id=uuid4(), attributes={"owner_id": uuid4()})

        with pytest.raises(PermissionDeniedError, match="ABAC denied"):
            await evaluator.evaluate(ctx)

    async def test_abac_scope_personal_allowed(self) -> None:
        db = FakePermissionDb(
            user_role="employee",
            permission_row={"constraint": {"scope": "personal"}},
        )
        evaluator = PermissionEvaluatorImpl(db)

        await evaluator.evaluate(_ctx(agent_scope="personal"))

    async def test_abac_scope_personal_denied(self) -> None:
        db = FakePermissionDb(
            user_role="employee",
            permission_row={"constraint": {"scope": "personal"}},
        )
        evaluator = PermissionEvaluatorImpl(db)

        with pytest.raises(PermissionDeniedError, match="ABAC denied"):
            await evaluator.evaluate(_ctx(agent_scope="department"))

    async def test_abac_dept_allowed(self) -> None:
        dept = uuid4()
        db = FakePermissionDb(
            user_role="manager",
            permission_row={"constraint": {"dept": True}},
        )
        evaluator = PermissionEvaluatorImpl(db)
        ctx = _ctx(
            role="manager",
            department_ids=[dept],
            attributes={"department_id": dept},
        )

        await evaluator.evaluate(ctx)

    async def test_abac_dept_denied(self) -> None:
        db = FakePermissionDb(
            user_role="manager",
            permission_row={"constraint": {"dept": True}},
        )
        evaluator = PermissionEvaluatorImpl(db)
        ctx = _ctx(
            role="manager",
            department_ids=[uuid4()],
            attributes={"department_id": uuid4()},
        )

        with pytest.raises(PermissionDeniedError, match="ABAC denied"):
            await evaluator.evaluate(ctx)


class TestCheckRbac:
    async def test_admin_short_circuits(self) -> None:
        db = FakePermissionDb(user_role="admin")
        evaluator = PermissionEvaluatorImpl(db)

        result = await evaluator.check_rbac(uuid4(), "admin", "agent", "delete")
        assert result is True

    async def test_permission_exists(self) -> None:
        db = FakePermissionDb(permission_row={"constraint": None})
        evaluator = PermissionEvaluatorImpl(db)

        result = await evaluator.check_rbac(uuid4(), "manager", "agent", "invoke")
        assert result is True

    async def test_permission_missing(self) -> None:
        db = FakePermissionDb(permission_row=None)
        evaluator = PermissionEvaluatorImpl(db)

        result = await evaluator.check_rbac(uuid4(), "employee", "agent", "delete")
        assert result is False


class TestCheckAbac:
    async def test_empty_constraints_passes(self) -> None:
        evaluator = PermissionEvaluatorImpl(FakePermissionDb())
        assert await evaluator.check_abac(_ctx(), []) is True

    async def test_scope_own_no_owner_id_fails(self) -> None:
        evaluator = PermissionEvaluatorImpl(FakePermissionDb())
        ctx = _ctx(attributes={})

        result = await evaluator.check_abac(ctx, [{"scope": "own"}])
        assert result is False

    async def test_dept_no_department_id_fails(self) -> None:
        evaluator = PermissionEvaluatorImpl(FakePermissionDb())
        ctx = _ctx(department_ids=[uuid4()], attributes={})

        result = await evaluator.check_abac(ctx, [{"dept": True}])
        assert result is False

    async def test_multiple_constraints_all_pass(self) -> None:
        user_id = uuid4()
        dept = uuid4()
        evaluator = PermissionEvaluatorImpl(FakePermissionDb())
        ctx = _ctx(
            user_id=user_id,
            department_ids=[dept],
            attributes={"owner_id": user_id, "department_id": dept},
        )

        result = await evaluator.check_abac(
            ctx, [{"scope": "own"}, {"dept": True}]
        )
        assert result is True

    async def test_multiple_constraints_one_fails(self) -> None:
        user_id = uuid4()
        evaluator = PermissionEvaluatorImpl(FakePermissionDb())
        ctx = _ctx(
            user_id=user_id,
            department_ids=[uuid4()],
            attributes={"owner_id": user_id, "department_id": uuid4()},
        )

        result = await evaluator.check_abac(
            ctx, [{"scope": "own"}, {"dept": True}]
        )
        assert result is False


class TestRecordDelegation:
    async def test_inserts_audit_log(self) -> None:
        db = FakePermissionDb()
        evaluator = PermissionEvaluatorImpl(db)
        record = DelegationRecord(
            tenant_id=uuid4(),
            delegator_id=uuid4(),
            delegatee_id=uuid4(),
            action="invoke",
            resource="agent",
            resource_id=uuid4(),
            permission_basis="rbac:manager",
            timestamp=datetime.now(),
        )

        await evaluator.record_delegation(record)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.audit_logs" in sql
        assert record.tenant_id in params
        assert record.delegator_id in params
        assert record.action in params
        assert record.resource in params

    async def test_null_resource_id_handled(self) -> None:
        db = FakePermissionDb()
        evaluator = PermissionEvaluatorImpl(db)
        record = DelegationRecord(
            tenant_id=uuid4(),
            delegator_id=uuid4(),
            delegatee_id=uuid4(),
            action="read",
            resource="datasource",
            resource_id=None,
            permission_basis="abac:rule_x",
            timestamp=datetime.now(),
        )

        await evaluator.record_delegation(record)

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert None in params

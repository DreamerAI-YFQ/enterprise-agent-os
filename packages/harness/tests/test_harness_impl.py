"""Tests for HarnessImpl — six-pillar orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from eaos.core.errors import PermissionDeniedError, QuotaExceededError
from eaos.harness.context import GuardContext
from eaos.harness.harness import HarnessImpl


def _ctx() -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        action="execute",
        resource="skill",
    )


def _make_harness(
    *,
    permission_raises: Exception | None = None,
    capability_raises: Exception | None = None,
    cost_raises: Exception | None = None,
    quality_raises: Exception | None = None,
) -> tuple[HarnessImpl, dict[str, AsyncMock]]:
    """Build a HarnessImpl with mocked pillars."""
    permission = AsyncMock()
    if permission_raises:
        permission.evaluate.side_effect = permission_raises
    else:
        permission.evaluate.return_value = None

    capability = AsyncMock()
    if capability_raises:
        capability.check.side_effect = capability_raises
    else:
        capability.check.return_value = None

    cost = AsyncMock()
    if cost_raises:
        cost.check_quota.side_effect = cost_raises
    else:
        cost.check_quota.return_value = None

    compliance = AsyncMock()
    compliance.post_check.return_value = None  # no redaction by default

    quality = AsyncMock()
    if quality_raises:
        quality.evaluate.side_effect = quality_raises
    else:
        quality.evaluate.return_value = None

    evolution = AsyncMock()
    approval = AsyncMock()
    policy = AsyncMock()

    harness = HarnessImpl(
        permission=permission,
        capability=capability,
        cost=cost,
        compliance=compliance,
        quality=quality,
        evolution=evolution,
        approval=approval,
        policy=policy,
    )
    return harness, {
        "permission": permission,
        "capability": capability,
        "cost": cost,
        "compliance": compliance,
        "quality": quality,
        "evolution": evolution,
        "approval": approval,
        "policy": policy,
    }


class TestGuard:
    async def test_calls_permission_capability_cost_in_sequence(self) -> None:
        harness, mocks = _make_harness()
        ctx = _ctx()

        await harness.guard(ctx)

        mocks["permission"].evaluate.assert_called_once_with(ctx)
        mocks["capability"].check.assert_called_once_with(ctx)
        mocks["cost"].check_quota.assert_called_once_with(ctx)

    async def test_permission_denied_propagates(self) -> None:
        harness, _ = _make_harness(permission_raises=PermissionDeniedError("no access"))

        with pytest.raises(PermissionDeniedError, match="no access"):
            await harness.guard(_ctx())

    async def test_capability_violation_propagates(self) -> None:
        harness, mocks = _make_harness(
            capability_raises=PermissionDeniedError("boundary exceeded")
        )

        with pytest.raises(PermissionDeniedError, match="boundary exceeded"):
            await harness.guard(_ctx())

        # Permission was still called (runs first)
        mocks["permission"].evaluate.assert_called_once()

    async def test_quota_exceeded_propagates(self) -> None:
        harness, _ = _make_harness(cost_raises=QuotaExceededError("limit reached"))

        with pytest.raises(QuotaExceededError, match="limit reached"):
            await harness.guard(_ctx())


class TestPostGuard:
    async def test_redacts_string_result(self) -> None:
        harness, mocks = _make_harness()
        mocks["compliance"].post_check.return_value = "redacted output"
        ctx = _ctx()

        result = await harness.post_guard(ctx, "original output")

        assert result == "redacted output"
        mocks["compliance"].post_check.assert_called_once_with(ctx, "original output")
        mocks["quality"].evaluate.assert_called_once_with(ctx, "redacted output")

    async def test_skips_redaction_for_non_string_result(self) -> None:
        harness, mocks = _make_harness()
        ctx = _ctx()
        result_obj = {"status": "ok", "data": [1, 2, 3]}

        result = await harness.post_guard(ctx, result_obj)

        assert result is result_obj
        mocks["compliance"].post_check.assert_not_called()
        mocks["quality"].evaluate.assert_called_once_with(ctx, result_obj)

    async def test_quality_violation_propagates(self) -> None:
        harness, _ = _make_harness(
            quality_raises=PermissionDeniedError("skill deprecated")
        )

        with pytest.raises(PermissionDeniedError, match="skill deprecated"):
            await harness.post_guard(_ctx(), "output")


class TestAudit:
    async def test_delegates_to_compliance_audit(self) -> None:
        harness, mocks = _make_harness()
        ctx = _ctx()
        detail = {"action": "execute", "result": "success"}

        await harness.audit(ctx, detail)

        mocks["compliance"].audit.assert_called_once_with(ctx, detail)


class TestCheckPermission:
    async def test_delegates_to_permission_evaluate(self) -> None:
        harness, mocks = _make_harness()
        ctx = _ctx()

        await harness.check_permission(ctx, action="write", resource="datasource")

        mocks["permission"].evaluate.assert_called_once()
        called_ctx = mocks["permission"].evaluate.call_args[0][0]
        assert called_ctx.action == "write"
        assert called_ctx.resource == "datasource"

    async def test_no_action_or_resource_uses_ctx_as_is(self) -> None:
        harness, mocks = _make_harness()
        ctx = _ctx()

        await harness.check_permission(ctx)

        mocks["permission"].evaluate.assert_called_once_with(ctx)

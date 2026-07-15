"""Tests for @guarded decorator — no-op fallback, enforce, audit."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from eaos.core.errors import PermissionDeniedError
from eaos.harness.context import GuardContext
from eaos.harness.decorators import guarded
from eaos.harness.harness import HarnessImpl, set_global_harness


def _ctx() -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        action="execute",
        resource="skill",
    )


def _make_mock_harness(
    *,
    guard_raises: Exception | None = None,
) -> HarnessImpl:
    """Build a HarnessImpl with all mocked pillars."""
    permission = AsyncMock()
    if guard_raises:
        permission.evaluate.side_effect = guard_raises
    else:
        permission.evaluate.return_value = None

    capability = AsyncMock()
    capability.check.return_value = None

    cost = AsyncMock()
    cost.check_quota.return_value = None

    compliance = AsyncMock()
    compliance.post_check.side_effect = lambda ctx, text: text  # passthrough by default

    quality = AsyncMock()
    quality.evaluate.return_value = None

    evolution = AsyncMock()
    approval = AsyncMock()
    policy = AsyncMock()

    return HarnessImpl(
        permission=permission,
        capability=capability,
        cost=cost,
        compliance=compliance,
        quality=quality,
        evolution=evolution,
        approval=approval,
        policy=policy,
    )


@pytest.fixture(autouse=True)
def _reset_global_harness() -> Any:
    """Reset global harness before and after each test."""
    from eaos.harness import harness as harness_mod

    old = harness_mod._global_harness
    harness_mod._global_harness = None
    yield
    harness_mod._global_harness = old


class TestNoOpFallback:
    async def test_passthrough_when_no_harness_registered(self) -> None:
        @guarded
        async def my_func(ctx: GuardContext) -> str:
            return "result"

        result = await my_func(_ctx())

        assert result == "result"

    async def test_passthrough_with_keyword_args(self) -> None:
        @guarded(action="write", resource="datasource", risk_level="high")
        async def my_func(ctx: GuardContext) -> str:
            return "written"

        result = await my_func(_ctx())

        assert result == "written"


class TestEnforceRejection:
    async def test_guard_raises_denies_execution(self) -> None:
        h = _make_mock_harness(guard_raises=PermissionDeniedError("denied"))
        set_global_harness(h)

        @guarded
        async def my_func(ctx: GuardContext) -> str:
            return "should not reach"

        with pytest.raises(PermissionDeniedError, match="denied"):
            await my_func(_ctx())

    async def test_guard_called_before_function(self) -> None:
        h = _make_mock_harness()
        set_global_harness(h)

        call_order: list[str] = []

        @guarded
        async def my_func(ctx: GuardContext) -> str:
            call_order.append("fn")
            return "done"

        await my_func(_ctx())

        # guard (permission.evaluate) runs before fn
        assert call_order == ["fn"]
        h._permission.evaluate.assert_called_once()  # type: ignore[attr-defined]


class TestPostGuard:
    async def test_post_guard_called_after_function(self) -> None:
        h = _make_mock_harness()
        set_global_harness(h)

        @guarded
        async def my_func(ctx: GuardContext) -> str:
            return "output"

        result = await my_func(_ctx())

        assert result == "output"
        h._quality.evaluate.assert_called_once()  # type: ignore[attr-defined]

    async def test_post_guard_redacts_string_result(self) -> None:
        h = _make_mock_harness()
        h._compliance.post_check.side_effect = lambda ctx, text: "redacted"  # type: ignore[attr-defined]
        set_global_harness(h)

        @guarded
        async def my_func(ctx: GuardContext) -> str:
            return "original"

        result = await my_func(_ctx())

        assert result == "redacted"


class TestNoCtxArg:
    async def test_passthrough_when_no_ctx_in_args(self) -> None:
        h = _make_mock_harness()
        set_global_harness(h)

        @guarded
        async def my_func(x: int, y: int) -> int:
            return x + y

        result = await my_func(1, 2)

        assert result == 3
        h._permission.evaluate.assert_not_called()  # type: ignore[attr-defined]

"""@guarded decorator — auto-applies Harness governance.

Usage:
    @guarded
    async def my_func(...): ...

    @guarded(action="write", resource="data_source", risk_level="high")
    async def my_func(...): ...

The decorator builds a GuardContext from the function's ctx argument + the
action/resource/risk_level args, then calls the global HarnessImpl.guard()
before the function and HarnessImpl.post_guard() after. If no global harness
is registered, the decorator is a passthrough (no-op).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar, overload

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from eaos.harness.context import GuardContext

T = TypeVar("T")


def _extract_ctx(args: tuple[Any, ...]) -> GuardContext | None:
    """Find the GuardContext in positional args (first arg with tenant_id)."""
    for arg in args:
        if hasattr(arg, "tenant_id") and hasattr(arg, "user_id"):
            return arg  # type: ignore[no-any-return]
    return None


def _build_ctx(
    base: GuardContext,
    *,
    action: str,
    resource: str,
    risk_level: str,
) -> GuardContext:
    """Derive a GuardContext with action/resource/risk set."""
    ctx = base.with_action(action, resource) if action or resource else base
    return ctx.with_risk(risk_level) if risk_level != ctx.risk_level else ctx


@overload
def guarded(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]: ...


@overload
def guarded(
    *,
    action: str = "read",
    resource: str = "agent",
    risk_level: str = "low",
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]: ...


def guarded(
    func: Callable[..., Awaitable[T]] | None = None,
    *,
    action: str = "read",
    resource: str = "agent",
    risk_level: str = "low",
) -> Any:
    """Decorator factory for Harness governance."""

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            from eaos.harness.harness import get_global_harness

            harness = get_global_harness()
            if harness is None:
                return await fn(*args, **kwargs)

            base_ctx = _extract_ctx(args)
            if base_ctx is None:
                return await fn(*args, **kwargs)

            ctx = _build_ctx(base_ctx, action=action, resource=resource, risk_level=risk_level)
            await harness.guard(ctx)
            result = await fn(*args, **kwargs)
            return await harness.post_guard(ctx, result)  # type: ignore[no-any-return]

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator

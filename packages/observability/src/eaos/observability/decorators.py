"""@traced decorator — auto-traces async functions.

Usage:
    @traced
    async def my_func(...): ...

    @traced(name="custom_name", granularity=Granularity.TOOL)
    async def my_func(...): ...

The decorator reads TenantContext from the first positional arg (if it is a
TenantContext) or from contextvars. It opens a span via the global Tracer.
If no Tracer is registered, the decorator is a no-op (transparent passthrough).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar, overload

from eaos.core.context import TenantContext
from eaos.observability._global import get_global_tracer
from eaos.observability.span import Granularity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


def _extract_ctx(args: tuple[Any, ...]) -> TenantContext | None:
    """Find a TenantContext in positional args, else check contextvars."""
    for arg in args:
        if isinstance(arg, TenantContext):
            return arg
    from eaos.core.context import get_tenant_context

    return get_tenant_context()


@overload
def traced(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]: ...


@overload
def traced(
    *,
    name: str | None = None,
    granularity: Granularity = Granularity.TASK,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]: ...


def traced(
    func: Callable[..., Awaitable[T]] | None = None,
    *,
    name: str | None = None,
    granularity: Granularity = Granularity.TASK,
) -> Any:
    """Decorator factory for tracing async functions.

    Can be used as @traced or @traced(name="...", granularity=...).
    When no global tracer is registered, acts as transparent passthrough.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            tracer = get_global_tracer()
            if tracer is None:
                return await fn(*args, **kwargs)

            ctx = _extract_ctx(args)
            span_name = name or fn.__qualname__

            gen = tracer.span(span_name, granularity, ctx)
            try:
                handle = await gen.__anext__()
            except StopAsyncIteration:
                return await fn(*args, **kwargs)

            try:
                result = await fn(*args, **kwargs)
                handle.set_status("ok")
                return result
            except Exception:
                handle.set_status("error")
                raise
            finally:
                await gen.aclose()

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator

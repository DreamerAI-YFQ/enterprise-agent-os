"""Tracer — four-granularity trace collector with context manager API.

Business code uses @traced decorator or ``async with tracer.span(...)`` to
automatically record spans. The Tracer writes to TraceStore asynchronously.

TracerImpl maintains trace_id/span_id via contextvars so nested spans
automatically link to their parent.
"""

from __future__ import annotations

import contextvars
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from eaos.observability.span import Granularity, Span

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from eaos.core.context import TenantContext
    from eaos.observability.store import TraceStore


@dataclass
class SpanHandle:
    """Handle to an in-flight span, used to add attributes/events."""

    span: Span

    def set_attribute(self, key: str, value: Any) -> None:
        self.span.attributes[key] = value

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        from eaos.observability.span import SpanEvent

        self.span.events.append(
            SpanEvent(name=name, timestamp=datetime.now(UTC), attributes=attributes or {})
        )

    def set_status(self, status: str) -> None:
        self.span.status = status

    def set_cost(self, tokens: int, usd: float | None = None) -> None:
        self.span.cost_tokens = tokens
        self.span.cost_usd = usd


class Tracer(Protocol):
    """Four-granularity trace collector."""

    def span(
        self,
        name: str,
        granularity: Granularity,
        ctx: TenantContext | None = None,
        parent_span_id: UUID | None = None,
        **attributes: Any,
    ) -> AsyncGenerator[SpanHandle, None]:
        """Open a span, yield handle, close on exit (auto-record duration/status).

        Declared as ``def`` (not ``async def``) because it is an async generator
        function: calling it returns an ``AsyncGenerator[SpanHandle, None]``
        directly (no await needed to obtain the iterator).
        """
        ...

    async def current_trace_id(self) -> UUID | None:
        """Get the current trace_id (for nesting spans within a task)."""
        ...

    async def current_span_id(self) -> UUID | None:
        """Get the current span_id (for parent linking)."""
        ...


class TracerImpl:
    """Concrete Tracer backed by a TraceStore.

    Uses contextvars to track the current trace_id and span_id so that
    nested ``span()`` calls automatically link to their parent without
    explicit parent_span_id passing.

    Usage::

        tracer = TracerImpl(store)
        async with asynccontextmanager(tracer.span)("name", Granularity.TASK, ctx) as h:
            ...  # span auto-closed on exit
    """

    def __init__(self, store: TraceStore) -> None:
        self._store = store
        self._trace_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
            "eaos_trace_id", default=None
        )
        self._span_id_var: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
            "eaos_span_id", default=None
        )

    async def span(
        self,
        name: str,
        granularity: Granularity,
        ctx: TenantContext | None = None,
        parent_span_id: UUID | None = None,
        **attributes: Any,
    ) -> AsyncGenerator[SpanHandle, None]:
        """Open a span, yield handle, close on exit.

        Auto-generates trace_id if none exists in context. Links to parent
        span via contextvar if parent_span_id not given explicitly.
        """
        if ctx is None:
            from eaos.core.context import get_tenant_context

            ctx = get_tenant_context()

        existing_trace = self._trace_id_var.get()
        trace_id = existing_trace if existing_trace is not None else uuid4()

        if parent_span_id is None:
            parent_span_id = self._span_id_var.get()

        now = datetime.now(UTC)
        span = Span(
            tenant_id=ctx.tenant_id if ctx is not None else UUID(int=0),
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            agent_id=ctx.agent_id if ctx is not None else UUID(int=0),
            session_id=ctx.session_id if ctx is not None else None,
            user_id=ctx.user_id if ctx is not None else None,
            granularity=granularity,
            name=name,
            start_time=now,
            status="ok",
            attributes=dict(attributes) if attributes else {},
        )

        await self._store.start(span)

        token_span = self._span_id_var.set(span.id)
        token_trace = self._trace_id_var.set(trace_id)

        handle = SpanHandle(span=span)
        try:
            yield handle
        except Exception:
            span.status = "error"
            raise
        finally:
            end_now = datetime.now(UTC)
            span.end_time = end_now
            span.duration_ms = int((end_now - span.start_time).total_seconds() * 1000)
            await self._store.end(span)
            with suppress(Exception):
                from eaos.infra.telemetry.otel import bridge_span

                bridge_span(span)
            self._span_id_var.reset(token_span)
            self._trace_id_var.reset(token_trace)

    async def current_trace_id(self) -> UUID | None:
        """Return the current trace_id from contextvar, or None."""
        return self._trace_id_var.get()

    async def current_span_id(self) -> UUID | None:
        """Return the current span_id from contextvar, or None."""
        return self._span_id_var.get()

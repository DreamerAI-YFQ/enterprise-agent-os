"""OpenTelemetry initialization and shutdown.

When OTelConfig.endpoint is None (dev environments without a collector),
init_telemetry is a no-op so the app runs without a tracing backend. When an
endpoint is configured, a TracerProvider is created with a BatchSpanProcessor
exporting via OTLP HTTP to the configured collector.

Usage: call init_telemetry once at app startup, get_tracer() in modules that
need spans, and shutdown_telemetry() at app shutdown to flush pending spans.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from eaos.core.config import OTelConfig
    from eaos.observability.span import Span


_provider: TracerProvider | None = None


def init_telemetry(config: OTelConfig) -> None:
    """Initialize the global tracer provider.

    No-op when ``config.endpoint`` is None (dev mode, no collector).
    """
    global _provider
    if config.endpoint is None:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": config.service_name})
    )
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=config.endpoint))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider


def shutdown_telemetry() -> None:
    """Shutdown the tracer provider, flushing pending spans. Safe to call multiple times."""
    global _provider
    if _provider is not None:
        with contextlib.suppress(Exception):
            _provider.shutdown()
        _provider = None


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer for the given instrumentation name."""
    return trace.get_tracer(name)


def bridge_span(span: Span) -> None:
    """Mirror an internal Span to the OpenTelemetry backend (dual-write).

    Creates a complete OTel span with the same name, time range, attributes,
    and status. Called after the internal span is finalized (end_time set).
    Safe to call when OTel is not initialized — creates no-op spans.
    """
    if span.end_time is None:
        return

    tracer = get_tracer("eaos")
    start_ns = int(span.start_time.timestamp() * 1e9)
    end_ns = int(span.end_time.timestamp() * 1e9)

    otel_span = tracer.start_span(span.name, start_time=start_ns)

    otel_span.set_attribute("tenant_id", str(span.tenant_id))
    otel_span.set_attribute("trace_id", str(span.trace_id))
    otel_span.set_attribute("agent_id", str(span.agent_id))
    otel_span.set_attribute("granularity", span.granularity.value)
    otel_span.set_attribute("status", span.status)

    if span.duration_ms is not None:
        otel_span.set_attribute("duration_ms", span.duration_ms)
    if span.cost_tokens is not None:
        otel_span.set_attribute("cost_tokens", span.cost_tokens)
    if span.cost_usd is not None:
        otel_span.set_attribute("cost_usd", span.cost_usd)
    if span.session_id is not None:
        otel_span.set_attribute("session_id", str(span.session_id))
    if span.user_id is not None:
        otel_span.set_attribute("user_id", str(span.user_id))

    for key, value in span.attributes.items():
        otel_span.set_attribute(f"attr.{key}", str(value))

    if span.status == "error":
        otel_span.set_status(trace.Status(trace.StatusCode.ERROR))
    else:
        otel_span.set_status(trace.Status(trace.StatusCode.OK))

    otel_span.end(end_time=end_ns)

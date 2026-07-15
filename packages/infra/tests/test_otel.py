"""Unit tests for OpenTelemetry initialization and span bridging.

Mocks the OTLP exporter and trace.set_tracer_provider to avoid global state
pollution between tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from eaos.core.config import OTelConfig
from eaos.infra.telemetry.otel import bridge_span, get_tracer, init_telemetry, shutdown_telemetry
from eaos.observability.span import Granularity, Span
from opentelemetry.sdk.trace import TracerProvider


class TestInitTelemetry:
    def test_noop_when_endpoint_none(self) -> None:
        config = OTelConfig(endpoint=None)
        with patch("eaos.infra.telemetry.otel.trace.set_tracer_provider") as mock_set:
            init_telemetry(config)
            mock_set.assert_not_called()

    def test_registers_provider_when_endpoint_set(self) -> None:
        config = OTelConfig(
            endpoint="http://localhost:4318/v1/traces",
            service_name="eaos-test",
        )
        with (
            patch("eaos.infra.telemetry.otel.OTLPSpanExporter") as mock_exporter_cls,
            patch("eaos.infra.telemetry.otel.trace.set_tracer_provider") as mock_set,
        ):
            init_telemetry(config)
            mock_exporter_cls.assert_called_once_with(
                endpoint="http://localhost:4318/v1/traces"
            )
            mock_set.assert_called_once()
            registered_provider = mock_set.call_args.args[0]
            assert isinstance(registered_provider, TracerProvider)
        # cleanup global state
        shutdown_telemetry()

    def test_service_name_applied_to_resource(self) -> None:
        config = OTelConfig(
            endpoint="http://localhost:4318/v1/traces",
            service_name="custom-service",
        )
        with (
            patch("eaos.infra.telemetry.otel.OTLPSpanExporter"),
            patch("eaos.infra.telemetry.otel.trace.set_tracer_provider") as mock_set,
        ):
            init_telemetry(config)
            provider = mock_set.call_args.args[0]
            resource_attrs = provider.resource.attributes
            assert resource_attrs.get("service.name") == "custom-service"
        shutdown_telemetry()


class TestShutdown:
    def test_shutdown_is_safe_when_not_initialized(self) -> None:
        # Should not raise even if nothing was initialized
        shutdown_telemetry()

    def test_shutdown_clears_global_provider(self) -> None:
        config = OTelConfig(endpoint="http://localhost:4318/v1/traces")
        with (
            patch("eaos.infra.telemetry.otel.OTLPSpanExporter"),
            patch("eaos.infra.telemetry.otel.trace.set_tracer_provider"),
        ):
            init_telemetry(config)
        shutdown_telemetry()
        # Calling shutdown again should be a no-op
        shutdown_telemetry()


class TestGetTracer:
    def test_returns_tracer(self) -> None:
        tracer = get_tracer("eaos.test")
        assert tracer is not None


_UNSET: Any = object()


def _make_span(
    *,
    end_time: datetime | None | Any = _UNSET,
    status: str = "ok",
    cost_tokens: int | None = None,
    cost_usd: float | None = None,
    attributes: dict[str, object] | None = None,
) -> Span:
    start = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    if end_time is _UNSET:
        effective_end: datetime | None = datetime(2026, 1, 15, 12, 0, 5, tzinfo=UTC)
    else:
        effective_end = end_time
    return Span(
        tenant_id=uuid4(),
        trace_id=uuid4(),
        agent_id=uuid4(),
        granularity=Granularity.TASK,
        name="bridged-span",
        start_time=start,
        end_time=effective_end,
        duration_ms=5000,
        status=status,
        cost_tokens=cost_tokens,
        cost_usd=cost_usd,
        attributes=attributes or {},
    )


class TestBridgeSpan:
    def test_creates_otel_span_with_name_and_time(self) -> None:
        span = _make_span()
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        mock_tracer.start_span.assert_called_once()
        call_args = mock_tracer.start_span.call_args
        assert call_args.args == ("bridged-span",)
        start_ns = call_args.kwargs["start_time"]
        expected_ns = int(span.start_time.timestamp() * 1e9)
        assert start_ns == expected_ns
        mock_otel_span.end.assert_called_once()

    def test_sets_core_attributes(self) -> None:
        span = _make_span(cost_tokens=1500, cost_usd=0.03)
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        set_attr_calls = {c.args[0]: c.args[1] for c in mock_otel_span.set_attribute.call_args_list}
        assert set_attr_calls["tenant_id"] == str(span.tenant_id)
        assert set_attr_calls["trace_id"] == str(span.trace_id)
        assert set_attr_calls["agent_id"] == str(span.agent_id)
        assert set_attr_calls["granularity"] == "task"
        assert set_attr_calls["status"] == "ok"
        assert set_attr_calls["duration_ms"] == 5000
        assert set_attr_calls["cost_tokens"] == 1500
        assert set_attr_calls["cost_usd"] == 0.03

    def test_sets_optional_attributes_when_present(self) -> None:
        session_id = uuid4()
        user_id = uuid4()
        span = _make_span()
        span.session_id = session_id
        span.user_id = user_id

        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        set_attr_calls = {c.args[0]: c.args[1] for c in mock_otel_span.set_attribute.call_args_list}
        assert set_attr_calls["session_id"] == str(session_id)
        assert set_attr_calls["user_id"] == str(user_id)

    def test_sets_custom_attributes_with_prefix(self) -> None:
        span = _make_span(attributes={"model": "gpt-4", "temperature": 0.7})
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        set_attr_keys = {c.args[0] for c in mock_otel_span.set_attribute.call_args_list}
        assert "attr.model" in set_attr_keys
        assert "attr.temperature" in set_attr_keys

    def test_sets_error_status_for_failed_span(self) -> None:
        span = _make_span(status="error")
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.trace") as mock_trace_mod:
            from opentelemetry import trace as real_trace

            mock_trace_mod.Status = real_trace.Status
            mock_trace_mod.StatusCode = real_trace.StatusCode
            with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
                bridge_span(span)

        mock_otel_span.set_status.assert_called_once()
        status_arg = mock_otel_span.set_status.call_args.args[0]
        assert status_arg.status_code == real_trace.StatusCode.ERROR

    def test_sets_ok_status_for_success_span(self) -> None:
        span = _make_span(status="ok")
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.trace") as mock_trace_mod:
            from opentelemetry import trace as real_trace

            mock_trace_mod.Status = real_trace.Status
            mock_trace_mod.StatusCode = real_trace.StatusCode
            with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
                bridge_span(span)

        status_arg = mock_otel_span.set_status.call_args.args[0]
        assert status_arg.status_code == real_trace.StatusCode.OK

    def test_skips_when_end_time_is_none(self) -> None:
        span = _make_span(end_time=None)
        mock_tracer = MagicMock()

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        mock_tracer.start_span.assert_not_called()

    def test_end_called_with_end_time_ns(self) -> None:
        span = _make_span()
        mock_otel_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_span.return_value = mock_otel_span

        with patch("eaos.infra.telemetry.otel.get_tracer", return_value=mock_tracer):
            bridge_span(span)

        mock_otel_span.end.assert_called_once()
        end_ns = mock_otel_span.end.call_args.kwargs["end_time"]
        expected_ns = int(span.end_time.timestamp() * 1e9)  # type: ignore[union-attr]
        assert end_ns == expected_ns

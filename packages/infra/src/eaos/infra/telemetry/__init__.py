"""Telemetry initialization and shutdown."""

from eaos.infra.telemetry.otel import get_tracer, init_telemetry, shutdown_telemetry

__all__ = ["get_tracer", "init_telemetry", "shutdown_telemetry"]

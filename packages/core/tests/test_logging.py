"""Tests for structlog logging configuration."""

from __future__ import annotations

from uuid import uuid4

from eaos.core.config import AppConfig
from eaos.core.context import set_tenant_context, tenant_id_var, user_id_var
from eaos.core.logging import _inject_tenant_context, configure_logging, get_logger


def _config(environment: str = "local", debug: bool = False) -> AppConfig:
    return AppConfig(environment=environment, debug=debug)


class TestConfigureLogging:
    def test_local_environment_does_not_raise(self) -> None:
        configure_logging(_config(environment="local", debug=True))

    def test_prod_environment_does_not_raise(self) -> None:
        configure_logging(_config(environment="prod", debug=False))

    def test_get_logger_returns_bound_logger(self) -> None:
        configure_logging(_config())
        logger = get_logger("test")
        # structlog BoundLogger exposes .info / .debug / .error
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")


class TestInjectTenantContext:
    def test_injects_when_context_set(self) -> None:
        tenant = uuid4()
        user = uuid4()
        set_tenant_context(tenant, user)
        try:
            result = _inject_tenant_context(None, "info", {"event": "x"})
        finally:
            # Reset contextvars to avoid leaking into other tests
            tenant_id_var.set(None)
            user_id_var.set(None)

        assert result["tenant_id"] == str(tenant)
        assert result["user_id"] == str(user)

    def test_no_inject_when_context_unset(self) -> None:
        tenant_id_var.set(None)
        user_id_var.set(None)
        result = _inject_tenant_context(None, "info", {"event": "x"})
        assert "tenant_id" not in result
        assert "user_id" not in result

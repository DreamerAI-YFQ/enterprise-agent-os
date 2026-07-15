"""Tests for SandboxFactory — verifies level routing without real containers.

Uses ``MagicMock`` + real ``@asynccontextmanager`` wrappers so that
``async with factory.session(...)`` works (AsyncMock returns coroutines that
do not support the async context manager protocol).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eaos.agent.runtime.sandbox import SandboxConfig, SandboxFactory
from eaos.core.context import TenantContext


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
    )


def _make_mock_sandbox() -> tuple[MagicMock, MagicMock]:
    """Return (sandbox_mock, session_mock) where session() is a real async ctx mgr."""
    session_mock = MagicMock(name="session")

    @asynccontextmanager
    async def _session(_config: Any, _ctx: Any) -> Any:
        yield session_mock

    sandbox = MagicMock(name="sandbox")
    sandbox.session = _session
    return sandbox, session_mock


class TestRouting:
    async def test_docker_level_routes_to_docker_sandbox(self) -> None:
        docker_sandbox, session = _make_mock_sandbox()
        factory = SandboxFactory(docker_client=MagicMock())
        # Patch the DockerSandbox constructor used inside SandboxFactory.session.
        import eaos.agent.runtime.sandbox as mod

        original = mod.DockerSandbox
        mod.DockerSandbox = lambda _client: docker_sandbox  # type: ignore
        try:
            config = SandboxConfig(level="docker")
            async with factory.session(config, _ctx()) as s:
                assert s is session
        finally:
            mod.DockerSandbox = original  # type: ignore

    async def test_process_level_routes_to_process_sandbox(self) -> None:
        factory = SandboxFactory(docker_client=None)
        config = SandboxConfig(level="process", timeout_sec=5)

        async with factory.session(config, _ctx()) as session:
            result = await session.run_code('print("routed-to-process")')

        assert result.exit_code == 0
        assert "routed-to-process" in result.stdout

    async def test_vm_level_degrades_to_process_sandbox(self) -> None:
        factory = SandboxFactory(docker_client=None)
        config = SandboxConfig(level="vm", timeout_sec=5)

        async with factory.session(config, _ctx()) as session:
            result = await session.run_code('print("vm-degraded")')

        assert result.exit_code == 0
        assert "vm-degraded" in result.stdout

    async def test_unknown_level_degrades_to_process_sandbox(self) -> None:
        factory = SandboxFactory(docker_client=None)
        config = SandboxConfig(level="weird-level", timeout_sec=5)

        async with factory.session(config, _ctx()) as session:
            result = await session.run_code('print("unknown-level")')

        assert result.exit_code == 0
        assert "unknown-level" in result.stdout


class TestDockerClientRequirement:
    async def test_docker_level_without_client_raises(self) -> None:
        factory = SandboxFactory(docker_client=None)
        config = SandboxConfig(level="docker")

        with pytest.raises(RuntimeError, match="docker client"):
            async with factory.session(config, _ctx()):
                pass

    async def test_docker_level_with_client_succeeds(self) -> None:
        docker_sandbox, session = _make_mock_sandbox()
        factory = SandboxFactory(docker_client=MagicMock())
        import eaos.agent.runtime.sandbox as mod

        original = mod.DockerSandbox
        mod.DockerSandbox = lambda _client: docker_sandbox  # type: ignore
        try:
            config = SandboxConfig(level="docker")
            async with factory.session(config, _ctx()) as s:
                assert s is session
        finally:
            mod.DockerSandbox = original  # type: ignore

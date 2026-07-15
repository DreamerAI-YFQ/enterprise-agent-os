"""Tests for DockerSandbox — requires a live Docker daemon.

Marked ``integration`` so the root conftest skips them unless
``EAOS_RUN_INTEGRATION=1`` is set. An additional runtime skip fires when the
``docker`` package is missing or the daemon is unreachable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eaos.agent.runtime.sandbox import DockerSandbox, SandboxConfig
from eaos.core.context import TenantContext


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
    )


def _docker_client() -> object:
    try:
        import docker  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("docker package not installed")

    try:
        client = docker.from_env()
    except Exception:
        pytest.skip("docker daemon not reachable")

    try:
        client.ping()
    except Exception:
        pytest.skip("docker daemon not reachable")

    return client


@pytest.mark.integration
class TestDockerSandboxRunCode:
    async def test_print_hello(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code('print("hello-docker")')

        assert result.exit_code == 0
        assert "hello-docker" in result.stdout

    async def test_nonzero_exit_on_error(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("raise SystemExit(3)")

        assert result.exit_code == 3

    async def test_unsupported_language(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("x", language="ruby")

        assert result.exit_code == 1
        assert "unsupported" in result.stderr


@pytest.mark.integration
class TestDockerSandboxCommand:
    async def test_run_command(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_command(["python", "-c", "print('cmd-out')"])

        assert result.exit_code == 0
        assert "cmd-out" in result.stdout

    async def test_empty_command(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_command([])

        assert result.exit_code == 1


@pytest.mark.integration
class TestDockerSandboxFiles:
    async def test_write_and_read_file(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            await session.write_file("/tmp/data.txt", b"payload")
            content = await session.read_file("/tmp/data.txt")

        assert content == b"payload"

    async def test_file_accessible_in_code(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            await session.write_file("/tmp/input.txt", b"42")
            result = await session.run_code(
                "print(open('/tmp/input.txt').read().strip())"
            )

        assert result.exit_code == 0
        assert "42" in result.stdout


@pytest.mark.integration
class TestDockerSandboxCleanup:
    async def test_container_removed_after_exit(self) -> None:
        client = _docker_client()
        sandbox = DockerSandbox(client)
        config = SandboxConfig(level="docker", timeout_sec=30)

        async with sandbox.session(config, _ctx()) as session:
            container = session._container  # type: ignore[attr-defined]
            container.reload()
            assert container.status == "running"

        # After context exit, container should be gone.
        with pytest.raises(Exception):  # noqa: B017 - docker errors vary
            container.reload()

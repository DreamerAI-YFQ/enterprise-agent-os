"""Tests for ProcessSandbox — real subprocess execution + timeout."""

from __future__ import annotations

import sys
from uuid import uuid4

from eaos.agent.runtime.sandbox import ProcessSandbox, SandboxConfig
from eaos.core.context import TenantContext


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
    )


class TestRunCode:
    async def test_print_hello(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code('print("hello")')

        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.duration_ms >= 0

    async def test_capture_stderr(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("import sys; sys.stderr.write('err')")

        assert result.exit_code == 0
        assert "err" in result.stderr

    async def test_nonzero_exit_on_error(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("raise SystemExit(3)")

        assert result.exit_code == 3

    async def test_unsupported_language(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("x", language="ruby")

        assert result.exit_code == 1
        assert "unsupported" in result.stderr


class TestTimeout:
    async def test_timeout_kills_process(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=1)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code("import time; time.sleep(10)")

        assert result.exit_code == -1
        assert "timeout" in result.stderr


class TestRunCommand:
    async def test_run_command_echo(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            # Use python -c for cross-platform "echo"
            result = await session.run_command(
                [sys.executable, "-c", "print('cmd-out')"]
            )

        assert result.exit_code == 0
        assert "cmd-out" in result.stdout

    async def test_empty_command(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_command([])

        assert result.exit_code == 1


class TestFiles:
    async def test_write_and_read_file(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            await session.write_file("sub/dir/data.txt", b"payload")
            content = await session.read_file("sub/dir/data.txt")

        assert content == b"payload"

    async def test_file_accessible_in_code(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            await session.write_file("input.txt", b"42")
            result = await session.run_code(
                "print(open('input.txt').read().strip())"
            )

        assert result.exit_code == 0
        assert "42" in result.stdout


class TestSessionCleanup:
    async def test_workdir_cleaned_up_after_exit(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=10)

        async with sandbox.session(config, _ctx()) as session:
            captured_workdir = session._workdir  # type: ignore[attr-defined]
            assert captured_workdir.exists()

        assert not captured_workdir.exists()


class TestStdoutTruncation:
    async def test_large_stdout_truncated(self) -> None:
        sandbox = ProcessSandbox()
        config = SandboxConfig(level="process", timeout_sec=30)

        # Generate > 64KB of output
        code = "print('x' * 70000)"
        async with sandbox.session(config, _ctx()) as session:
            result = await session.run_code(code)

        assert result.exit_code == 0
        assert result.truncated is True
        assert len(result.stdout) <= 65536

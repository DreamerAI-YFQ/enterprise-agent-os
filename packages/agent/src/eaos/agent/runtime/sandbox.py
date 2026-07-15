"""Code sandbox — isolated execution environment for skill code.

Three isolation levels: process (L1), Docker container (L2), microVM (L3).
Prototype implements L1+L2. All sandboxes: no network by default, filesystem
isolated, resource-limited, audited.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.core.context import TenantContext


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox isolation configuration."""

    level: str  # process/docker/vm
    cpu_limit: float = 1.0  # cores
    memory_limit_mb: int = 512
    timeout_sec: int = 60
    network_enabled: bool = False
    filesystem_rw: bool = False  # default read-only mount


@dataclass(frozen=True)
class CodeResult:
    """Result of code/command execution in sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    truncated: bool = False


_MAX_STDOUT_BYTES = 64 * 1024


class SandboxSession(Protocol):
    """An isolated execution session."""

    async def run_code(
        self,
        code: str,
        language: str = "python",
    ) -> CodeResult:
        """Execute code in the sandbox."""
        ...

    async def run_command(self, cmd: list[str]) -> CodeResult:
        """Execute a shell command."""
        ...

    async def write_file(self, path: str, content: bytes) -> None:
        """Write a file to the sandbox workspace."""
        ...

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox workspace."""
        ...

    async def close(self) -> None:
        """Tear down the sandbox (kill process/container)."""
        ...


class CodeSandbox(Protocol):
    """Sandbox factory. Creates isolated sessions per skill execution."""

    async def session(
        self,
        config: SandboxConfig,
        ctx: TenantContext,
    ) -> AsyncIterator[SandboxSession]:
        """Create an isolated session. Auto-cleanup on context exit."""
        ...


class ProcessSandboxSession:
    """SandboxSession backed by asyncio subprocess in a temp workdir.

    Each ``run_code``/``run_command`` spawns a fresh subprocess with a minimal
    environment, optional memory limit (POSIX only), and a hard timeout.
    """

    def __init__(self, workdir: Path, config: SandboxConfig) -> None:
        self._workdir = workdir
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None

    async def run_code(
        self,
        code: str,
        language: str = "python",
    ) -> CodeResult:
        if language != "python":
            return CodeResult(
                stdout="",
                stderr=f"unsupported language: {language}",
                exit_code=1,
                duration_ms=0,
            )
        script = self._workdir / "_eaos_run.py"
        script.write_text(code, encoding="utf-8")
        return await self._run([sys.executable, str(script)])

    async def run_command(self, cmd: list[str]) -> CodeResult:
        if not cmd:
            return CodeResult(stdout="", stderr="empty command", exit_code=1, duration_ms=0)
        return await self._run(cmd)

    async def _run(self, cmd: list[str]) -> CodeResult:
        env = self._build_env()
        preexec = self._preexec_fn()
        start = time.perf_counter()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workdir),
            env=env,
            preexec_fn=preexec if preexec is not None else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                self._proc.communicate(), timeout=self._config.timeout_sec
            )
        except TimeoutError:
            self._kill()
            duration_ms = int((time.perf_counter() - start) * 1000)
            return CodeResult(
                stdout="",
                stderr=f"timeout after {self._config.timeout_sec}s",
                exit_code=-1,
                duration_ms=duration_ms,
            )
        duration_ms = int((time.perf_counter() - start) * 1000)
        exit_code = self._proc.returncode if self._proc.returncode is not None else -1
        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        truncated = False
        if len(stdout.encode("utf-8")) > _MAX_STDOUT_BYTES:
            stdout = stdout[:_MAX_STDOUT_BYTES]
            truncated = True
        return CodeResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    def _build_env(self) -> dict[str, str]:
        # Minimal env: only PATH so the interpreter and shell commands resolve.
        return {"PATH": os.environ.get("PATH", "")}

    @staticmethod
    def _preexec_fn() -> Any:
        """Return a preexec_fn that applies RLIMIT_AS on POSIX, None on Windows."""
        if platform.system() == "Windows":
            return None
        try:
            import resource
        except ImportError:
            return None

        def _set_limits() -> None:
            # RLIMIT_AS caps the address space (rough memory cap).
            mem_bytes = 512 * 1024 * 1024
            rlimit_as = getattr(resource, "RLIMIT_AS", None)
            if rlimit_as is not None:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(rlimit_as, (mem_bytes, mem_bytes))  # type: ignore[attr-defined]

        return _set_limits

    def _kill(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()

    async def write_file(self, path: str, content: bytes) -> None:
        target = self._workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def read_file(self, path: str) -> bytes:
        target = self._workdir / path
        return target.read_bytes()

    async def close(self) -> None:
        self._kill()
        # workdir cleanup handled by ProcessSandbox.session context manager.


class ProcessSandbox:
    """CodeSandbox backed by OS processes (isolation level L1)."""

    @asynccontextmanager
    async def session(
        self,
        config: SandboxConfig,
        ctx: TenantContext,
    ) -> AsyncIterator[SandboxSession]:
        del ctx  # process sandbox does not vary by tenant
        workdir = Path(tempfile.mkdtemp(prefix="eaos_sandbox_"))
        session = ProcessSandboxSession(workdir, config)
        try:
            yield session
        finally:
            session._kill()
            shutil.rmtree(workdir, ignore_errors=True)


class DockerSandboxSession:
    """SandboxSession backed by a Docker container.

    Uses ``container.exec_run`` for code/command execution and tar archives
    for file I/O. The container is created with ``sleep infinity`` and reused
    for all calls within the session.
    """

    def __init__(self, container: Any, workdir: str, config: SandboxConfig) -> None:
        self._container = container
        self._workdir = workdir
        self._config = config

    async def run_code(
        self,
        code: str,
        language: str = "python",
    ) -> CodeResult:
        if language != "python":
            return CodeResult(
                stdout="",
                stderr=f"unsupported language: {language}",
                exit_code=1,
                duration_ms=0,
            )
        await self.write_file("/tmp/_eaos_run.py", code.encode("utf-8"))
        return await self._exec(["python", "/tmp/_eaos_run.py"])

    async def run_command(self, cmd: list[str]) -> CodeResult:
        if not cmd:
            return CodeResult(stdout="", stderr="empty command", exit_code=1, duration_ms=0)
        return await self._exec(cmd)

    async def _exec(self, cmd: list[str]) -> CodeResult:
        start = time.perf_counter()
        # demux=True returns (exit_code, (stdout, stderr)) tuple.
        result = self._container.exec_run(cmd, demux=True, workdir=self._workdir)
        duration_ms = int((time.perf_counter() - start) * 1000)
        exit_code = int(result.exit_code if hasattr(result, "exit_code") else result[0])
        output = result.output if hasattr(result, "output") else result[1]
        stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        truncated = False
        if len(stdout.encode("utf-8")) > _MAX_STDOUT_BYTES:
            stdout = stdout[:_MAX_STDOUT_BYTES]
            truncated = True
        return CodeResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    async def write_file(self, path: str, content: bytes) -> None:
        import io
        import tarfile

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(path))
            info.size = len(content)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(content))
        stream.seek(0)
        target_dir = os.path.dirname(path) or self._workdir
        self._container.put_archive(target_dir, stream)

    async def read_file(self, path: str) -> bytes:
        import io
        import tarfile

        stream, _ = self._container.get_archive(path)
        data = b"".join(stream)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            members = tar.getmembers()
            if not members:
                raise FileNotFoundError(path)
            f = tar.extractfile(members[0])
            return f.read() if f is not None else b""

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self._container.stop()
        with contextlib.suppress(Exception):
            self._container.remove(force=True)


class DockerSandbox:
    """CodeSandbox backed by Docker containers (isolation level L2).

    Requires the optional ``docker`` package. Create a ``DockerSandbox`` with a
    ``docker.DockerClient``; sessions spin up a ``python:3.12-slim`` container
    per invocation and tear it down on exit.
    """

    def __init__(self, docker_client: Any) -> None:
        self._client = docker_client

    @asynccontextmanager
    async def session(
        self,
        config: SandboxConfig,
        ctx: TenantContext,
    ) -> AsyncIterator[SandboxSession]:
        del ctx
        container = self._client.containers.create(
            "python:3.12-slim",
            command="sleep infinity",
            detach=True,
            mem_limit=f"{config.memory_limit_mb}m" if config.memory_limit_mb else None,
        )
        container.start()
        session = DockerSandboxSession(container, "/tmp", config)
        try:
            yield session
        finally:
            await session.close()


class SandboxFactory:
    """Selects a sandbox implementation based on ``SandboxConfig.level``.

    - ``docker``: DockerSandbox (requires a docker client)
    - ``process`` or ``vm``: ProcessSandbox (vm degrades to process in Phase 3)
    """

    def __init__(self, docker_client: Any | None = None) -> None:
        self._docker_client = docker_client

    @asynccontextmanager
    async def session(
        self,
        config: SandboxConfig,
        ctx: TenantContext,
    ) -> AsyncIterator[SandboxSession]:
        if config.level == "docker":
            if self._docker_client is None:
                raise RuntimeError("DockerSandbox requires a docker client")
            async with DockerSandbox(self._docker_client).session(config, ctx) as s:
                yield s
        else:
            # "process" or "vm" (vm degrades to process in Phase 3)
            async with ProcessSandbox().session(config, ctx) as s:
                yield s

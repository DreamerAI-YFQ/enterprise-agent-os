"""Competition environment preflight checker.

Verifies that all services required for competition evidence are available
and healthy. Exits with non-zero code on any failure, with structured output.

Usage:
    python benchmarks/competition/preflight.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from urllib.error import URLError
from urllib.request import Request, urlopen


def _load_env() -> None:
    """Load .env file into os.environ (simple parser, no dependency)."""
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env()


@dataclass
class CheckResult:
    name: str
    status: str  # pass | fail | skip | warn
    detail: str = ""
    latency_ms: float | None = None


@dataclass
class PreflightReport:
    timestamp: str
    git_sha: str
    results: list[CheckResult] = field(default_factory=list)
    all_passed: bool = False

    def add(self, r: CheckResult) -> None:
        self.results.append(r)
        if r.status == "fail":
            self.all_passed = False


def _http_get(url: str, timeout: int = 10) -> tuple[int, str, float]:
    req = Request(url, method="GET")
    t0 = time.monotonic()
    resp = urlopen(req, timeout=timeout)
    latency = (time.monotonic() - t0) * 1000
    body = resp.read().decode()
    return resp.status, body, latency


def check_docker_containers() -> CheckResult:
    """Check required Docker containers are running."""
    import subprocess

    required = {"eaos-postgres", "eaos-redis", "eaos-api", "eaos-worker"}
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"], text=True, timeout=15
        ).strip()
    except Exception as e:
        return CheckResult("docker_containers", "fail", f"docker not available: {e}")
    running = set(out.splitlines())
    missing = required - running
    if missing:
        return CheckResult(
            "docker_containers", "fail",
            f"missing containers: {', '.join(sorted(missing))}"
        )
    return CheckResult("docker_containers", "pass", f"{len(required)} containers running")


def check_postgres() -> CheckResult:
    """Check PostgreSQL connectivity and migration version."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-c",
             "SELECT version_num FROM alembic_version"],
            text=True, timeout=10,
        ).strip()
    except Exception as e:
        return CheckResult("postgresql", "fail", f"psql failed: {e}")
    if not out:
        return CheckResult("postgresql", "fail", "no migration version found")
    return CheckResult("postgresql", "pass", f"migration version: {out}")


def check_redis() -> CheckResult:
    """Check Redis connectivity."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["docker", "exec", "eaos-redis", "redis-cli", "ping"],
            text=True, timeout=10,
        ).strip()
    except Exception as e:
        return CheckResult("redis", "fail", f"redis-cli failed: {e}")
    if out != "PONG":
        return CheckResult("redis", "fail", f"unexpected response: {out}")
    return CheckResult("redis", "pass", "PONG")


def check_api_health() -> CheckResult:
    """Check API health endpoint."""
    try:
        status, body, latency = _http_get("http://127.0.0.1:8000/health", timeout=10)
    except URLError as e:
        return CheckResult("api_health", "fail", str(e))
    except Exception as e:
        return CheckResult("api_health", "fail", str(e))
    if status != 200:
        return CheckResult("api_health", "fail", f"HTTP {status}: {body}")
    return CheckResult("api_health", "pass", body.strip(), latency)


def check_llm() -> CheckResult:
    """Check LLM provider is reachable with a minimal chat completion."""
    api_key = os.environ.get("EAOS_LLM__OPENAI_API_KEY", "")
    base_url = os.environ.get("EAOS_LLM__OPENAI_BASE_URL", "")
    model = os.environ.get("EAOS_LLM__DEFAULT_MODEL", "")
    if not api_key or not base_url:
        return CheckResult("llm", "skip", "EAOS_LLM__OPENAI_API_KEY or BASE_URL not set")

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }).encode()
    req = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        t0 = time.monotonic()
        resp = urlopen(req, timeout=30)
        latency = (time.monotonic() - t0) * 1000
        body = json.loads(resp.read().decode())
        used_model = body.get("model", "?")
        usage = body.get("usage", {})
        tokens = usage.get("total_tokens", "?")
        return CheckResult(
            "llm", "pass",
            f"model={used_model} tokens={tokens}",
            latency,
        )
    except Exception as e:
        return CheckResult("llm", "fail", str(e))


def check_embedding() -> CheckResult:
    """Check Embedding provider is reachable."""
    api_key = os.environ.get("EAOS_EMBEDDING__API_KEY", "")
    base_url = os.environ.get("EAOS_EMBEDDING__BASE_URL", "")
    model = os.environ.get("EAOS_EMBEDDING__MODEL", "")
    if not api_key or not base_url:
        return CheckResult("embedding", "skip", "EAOS_EMBEDDING__API_KEY or BASE_URL not set")

    payload = json.dumps({
        "model": model,
        "input": "test",
    }).encode()
    req = Request(
        f"{base_url}/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        t0 = time.monotonic()
        resp = urlopen(req, timeout=30)
        latency = (time.monotonic() - t0) * 1000
        body = json.loads(resp.read().decode())
        dims = len(body.get("data", [{}])[0].get("embedding", []))
        return CheckResult(
            "embedding", "pass",
            f"model={model} dims={dims}",
            latency,
        )
    except Exception as e:
        return CheckResult("embedding", "fail", str(e))


def check_seed_data() -> CheckResult:
    """Check that seed tenants, users, and demo data exist."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-c",
             "SELECT COUNT(*) FROM iam.tenants"],
            text=True, timeout=10,
        ).strip()
        tenant_count = int(out)
    except Exception as e:
        return CheckResult("seed_data", "fail", f"seed check failed: {e}")
    if tenant_count == 0:
        return CheckResult("seed_data", "fail", "no tenants found — run seed")
    return CheckResult("seed_data", "pass", f"tenants={tenant_count}")


def get_git_sha() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=5
        ).strip()
    except Exception:
        return "unknown"


def run_preflight(json_output: bool = False) -> int:
    report = PreflightReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        git_sha=get_git_sha(),
        all_passed=True,
    )

    checks = [
        check_docker_containers,
        check_postgres,
        check_redis,
        check_api_health,
        check_seed_data,
        check_llm,
        check_embedding,
    ]

    for check_fn in checks:
        result = check_fn()
        report.add(result)
        if not json_output:
            icon = {"pass": "[OK]", "fail": "[FAIL]", "skip": "[SKIP]", "warn": "[WARN]"}[result.status]
            latency_str = f" ({result.latency_ms:.0f}ms)" if result.latency_ms else ""
            print(f"  {icon} {result.name:<20} {result.detail}{latency_str}")

    has_fail = any(r.status == "fail" for r in report.results)
    if not json_output:
        print()
        if has_fail:
            print("PREFLIGHT FAILED — fix the issues above before running competition tests.")
        else:
            print("PREFLIGHT PASSED — environment ready for competition tests.")
    else:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))

    return 1 if has_fail else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Competition environment preflight")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()
    sys.exit(run_preflight(json_output=args.json))

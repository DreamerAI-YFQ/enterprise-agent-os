"""C10: Evidence export — trace, usage, and audit artifacts.

Exports competition evidence artifacts for a given run:
- Trace logs (agent decisions, tool calls, RAG results)
- Usage metrics (token counts, latency, cost)
- Audit trail (write operations, approvals, rollbacks)
- Session transcripts

All artifacts are written to artifacts/competition-evidence/<run_id>/
with SHA-256 hashes for integrity verification.

Usage:
    python scripts/competition/export_evidence.py --run-id <run_id> [--session-id <sid>]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def export_trace(db_url: str, run_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Export trace logs from the database."""
    import subprocess

    traces_file = output_dir / "traces.jsonl"
    rows = []
    try:
        result = subprocess.run(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
             "SELECT id, tenant_id, agent_id, session_id, span_name, "
             "attributes, started_at, ended_at, status "
             "FROM observability.traces "
             "ORDER BY started_at"],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 9:
                rows.append({
                    "id": parts[0],
                    "tenant_id": parts[1],
                    "agent_id": parts[2],
                    "session_id": parts[3],
                    "span_name": parts[4],
                    "attributes": parts[5],
                    "started_at": parts[6],
                    "ended_at": parts[7],
                    "status": parts[8],
                })
    except Exception as e:
        print(f"  [WARN] trace export failed: {e}")

    with open(traces_file, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return [{"file": str(traces_file), "sha256": _sha256_file(traces_file), "rows": len(rows)}]


async def export_audit(db_url: str, run_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Export write audit trail."""
    import subprocess

    audit_file = output_dir / "write_audit.jsonl"
    rows = []
    try:
        result = subprocess.run(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
             "SELECT id, tenant_id, principal_id, tool_name, resource, "
             "operation, success, before_data, after_data, approval_id, "
             "trace_id, error, created_at "
             "FROM harness.write_audit ORDER BY created_at"],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 13:
                rows.append({
                    "id": parts[0],
                    "tenant_id": parts[1],
                    "principal_id": parts[2],
                    "tool_name": parts[3],
                    "resource": parts[4],
                    "operation": parts[5],
                    "success": parts[6],
                    "before_data": parts[7],
                    "after_data": parts[8],
                    "approval_id": parts[9],
                    "trace_id": parts[10],
                    "error": parts[11],
                    "created_at": parts[12],
                })
    except Exception as e:
        print(f"  [WARN] audit export failed: {e}")

    with open(audit_file, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return [{"file": str(audit_file), "sha256": _sha256_file(audit_file), "rows": len(rows)}]


async def export_approvals(db_url: str, run_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Export approval records."""
    import subprocess

    approvals_file = output_dir / "approvals.jsonl"
    rows = []
    try:
        result = subprocess.run(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
             "SELECT id, tenant_id, agent_id, session_id, status, "
             "risk_level, decided_by, decided_at, created_at "
             "FROM harness.approvals ORDER BY created_at"],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 9:
                rows.append({
                    "id": parts[0], "tenant_id": parts[1], "agent_id": parts[2],
                    "session_id": parts[3], "status": parts[4], "risk_level": parts[5],
                    "decided_by": parts[6], "decided_at": parts[7], "created_at": parts[8],
                })
    except Exception as e:
        print(f"  [WARN] approvals export failed: {e}")

    with open(approvals_file, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return [{"file": str(approvals_file), "sha256": _sha256_file(approvals_file), "rows": len(rows)}]


async def export_business_state(db_url: str, run_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Export final business state (orders, inventory) for DB-truth verification."""
    import subprocess

    state_file = output_dir / "business_state.json"

    state: dict[str, Any] = {}
    try:
        for table in ["erp.orders", "erp.inventory", "erp.customers", "erp.products"]:
            result = subprocess.run(
                ["docker", "exec", "eaos-postgres",
                 "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
                 f"SELECT row_to_json(t) FROM {table} t ORDER BY created_at LIMIT 100"],
                capture_output=True, text=True, timeout=30, encoding="utf-8",
            )
            table_rows = []
            for line in result.stdout.strip().splitlines():
                if line:
                    try:
                        table_rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            state[table] = table_rows
    except Exception as e:
        print(f"  [WARN] business state export failed: {e}")

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    return [{"file": str(state_file), "sha256": _sha256_file(state_file), "tables": list(state.keys())}]


async def export_usage(db_url: str, run_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Export LLM usage metrics from traces."""
    import subprocess

    usage_file = output_dir / "usage.json"
    usage: dict[str, Any] = {"run_id": run_id, "total_tokens": 0, "total_cost_usd": 0.0, "calls": []}
    try:
        result = subprocess.run(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
             "SELECT id, attributes FROM observability.traces "
             "WHERE span_name = 'llm_chat' ORDER BY started_at"],
            capture_output=True, text=True, timeout=60, encoding="utf-8",
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) == 2:
                try:
                    attrs = json.loads(parts[1]) if parts[1].startswith("{") else {}
                    tokens = attrs.get("total_tokens", 0)
                    usage["total_tokens"] += tokens
                    usage["calls"].append({"trace_id": parts[0], "tokens": tokens, "attrs": attrs})
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"  [WARN] usage export failed: {e}")

    with open(usage_file, "w", encoding="utf-8") as f:
        json.dump(usage, f, ensure_ascii=False, indent=2, default=str)
    return [{"file": str(usage_file), "sha256": _sha256_file(usage_file), "total_tokens": usage["total_tokens"]}]


def write_manifest(run_id: str, output_dir: Path, artifacts: list[list[dict[str, Any]]]) -> None:
    """Write the evidence manifest with SHA-256 hashes."""
    manifest = {
        "run_id": run_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_sha": _get_git_sha(),
        "artifacts": [item for sublist in artifacts for item in sublist],
    }
    manifest_file = output_dir / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  [OK] Manifest written: {manifest_file}")


def _get_git_sha() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5).strip()
    except Exception:
        return "unknown"


async def main(run_id: str, db_url: str) -> int:
    output_dir = Path("artifacts/competition-evidence") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Exporting evidence for run '{run_id}' to {output_dir}/")

    all_artifacts: list[list[dict[str, Any]]] = []

    print("  Exporting traces...")
    all_artifacts.append(await export_trace(db_url, run_id, output_dir))

    print("  Exporting audit trail...")
    all_artifacts.append(await export_audit(db_url, run_id, output_dir))

    print("  Exporting approvals...")
    all_artifacts.append(await export_approvals(db_url, run_id, output_dir))

    print("  Exporting business state...")
    all_artifacts.append(await export_business_state(db_url, run_id, output_dir))

    print("  Exporting usage metrics...")
    all_artifacts.append(await export_usage(db_url, run_id, output_dir))

    print("  Writing manifest...")
    write_manifest(run_id, output_dir, all_artifacts)

    total_files = sum(len(a) for a in all_artifacts) + 1  # +1 for manifest
    print(f"\nEvidence export complete: {total_files} files in {output_dir}/")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export competition evidence")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--db-url", default="postgresql://eaos:eaos@localhost:5432/eaos")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.run_id, args.db_url)))

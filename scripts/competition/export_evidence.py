"""Export reproducible, run-scoped competition evidence.

The exporter deliberately refuses an unscoped database dump.  A run must be
identified by one or more session ids, or by an explicit UTC time window.  The
resulting bundle contains trace, approval, write-audit, transcript, usage, and
business-state artifacts plus a manifest that binds them to source, datasets,
configuration, and benchmark result files.

Examples:
    python scripts/competition/export_evidence.py --run-id run-001 \
        --session-id 00000000-0000-0000-0000-000000000001
    python scripts/competition/export_evidence.py --run-id run-001 \
        --started-at 2026-07-17T08:00:00Z --ended-at 2026-07-17T09:00:00Z
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = PROJECT_ROOT / "artifacts" / "competition-evidence"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "competition" / "results"
REPRO_INPUTS = (
    PROJECT_ROOT / "benchmarks" / "competition" / "datasets",
    PROJECT_ROOT / "benchmarks" / "competition" / "configs",
    PROJECT_ROOT / "benchmarks" / "competition" / "claim_matrix.yaml",
)
GENERATED_UNTRACKED_PREFIXES = (
    "artifacts/competition-evidence/",
    "benchmarks/competition/results/",
)


@dataclass(frozen=True)
class EvidenceScope:
    """Database scope used to attribute evidence to exactly one run."""

    run_id: str
    session_ids: tuple[UUID, ...] = ()
    contribution_ids: tuple[UUID, ...] = ()
    document_ids: tuple[UUID, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def validate(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if any(part in self.run_id for part in ("/", "\\", "..")):
            raise ValueError("run_id must be a directory-safe identifier")
        if not self.session_ids and (self.started_at is None or self.ended_at is None):
            raise ValueError(
                "evidence export requires --session-id or both --started-at and --ended-at; "
                "unscoped full-database export is not allowed"
            )
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.started_at >= self.ended_at
        ):
            raise ValueError("started_at must be earlier than ended_at")

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_ids": [str(value) for value in self.session_ids],
            "contribution_ids": [str(value) for value in self.contribution_ids],
            "document_ids": [str(value) for value in self.document_ids],
            "started_at": _iso(self.started_at),
            "ended_at": _iso(self.ended_at),
            "mode": "sessions" if self.session_ids else "time_window",
        }


@dataclass
class RunRelations:
    """Identifiers discovered from the run scope and reused across exports."""

    tenant_ids: list[UUID]
    trace_ids: list[UUID]
    approval_ids: list[UUID]
    contribution_ids: list[UUID]
    document_ids: list[UUID]


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone: {value}")
    return parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime, UUID, Decimal)):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _sha256_file(filepath: Path) -> str:
    digest = hashlib.sha256()
    with filepath.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _project_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
            count += 1
    return count


def _artifact(path: Path, **metadata: Any) -> dict[str, Any]:
    return {
        "file": _project_path(path),
        "sha256": _sha256_file(path),
        **metadata,
    }


def _fetch_all(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _time_clause(
    scope: EvidenceScope,
    column: str,
    *,
    include_where: bool = True,
) -> tuple[str, list[Any]]:
    if scope.started_at is None or scope.ended_at is None:
        return "", []
    prefix = "WHERE" if include_where else "AND"
    return f" {prefix} {column} >= %s AND {column} <= %s", [scope.started_at, scope.ended_at]


def _discover_relations(conn: Any, scope: EvidenceScope) -> RunRelations:
    if scope.session_ids:
        session_ids = list(scope.session_ids)
        tenant_rows = _fetch_all(
            conn,
            "SELECT DISTINCT tenant_id FROM agent.sessions WHERE id = ANY(%s)",
            (session_ids,),
        )
        trace_rows = _fetch_all(
            conn,
            "SELECT DISTINCT trace_id FROM trace.spans WHERE session_id = ANY(%s)",
            (session_ids,),
        )
        approval_rows = _fetch_all(
            conn,
            "SELECT id FROM harness.approvals WHERE session_id = ANY(%s)",
            (session_ids,),
        )
    else:
        assert scope.started_at is not None and scope.ended_at is not None
        tenant_rows = _fetch_all(
            conn,
            "SELECT DISTINCT tenant_id FROM trace.spans "
            "WHERE start_time >= %s AND start_time <= %s",
            (scope.started_at, scope.ended_at),
        )
        trace_rows = _fetch_all(
            conn,
            "SELECT DISTINCT trace_id FROM trace.spans "
            "WHERE start_time >= %s AND start_time <= %s",
            (scope.started_at, scope.ended_at),
        )
        approval_rows = _fetch_all(
            conn,
            "SELECT id FROM harness.approvals WHERE created_at >= %s AND created_at <= %s",
            (scope.started_at, scope.ended_at),
        )

    contribution_rows = (
        _fetch_all(
            conn,
            "SELECT id, tenant_id FROM knowledge.contributions WHERE id = ANY(%s)",
            (list(scope.contribution_ids),),
        )
        if scope.contribution_ids
        else []
    )
    document_clauses: list[str] = []
    document_params: list[Any] = []
    if scope.document_ids:
        document_clauses.append("id = ANY(%s)")
        document_params.append(list(scope.document_ids))
    if scope.contribution_ids:
        document_clauses.append("metadata->>'contribution_id' = ANY(%s)")
        document_params.append([str(value) for value in scope.contribution_ids])
    document_rows = (
        _fetch_all(
            conn,
            "SELECT id, tenant_id FROM knowledge.documents WHERE "
            + " OR ".join(f"({clause})" for clause in document_clauses),
            document_params,
        )
        if document_clauses
        else []
    )

    tenant_values = {
        UUID(str(row["tenant_id"]))
        for row in [*tenant_rows, *contribution_rows, *document_rows]
    }

    return RunRelations(
        tenant_ids=sorted(tenant_values, key=str),
        trace_ids=[UUID(str(row["trace_id"])) for row in trace_rows],
        approval_ids=[UUID(str(row["id"])) for row in approval_rows],
        contribution_ids=[UUID(str(row["id"])) for row in contribution_rows],
        document_ids=[UUID(str(row["id"])) for row in document_rows],
    )


def _validate_relations(scope: EvidenceScope, relations: RunRelations) -> None:
    if len(relations.tenant_ids) > 1:
        raise ValueError("evidence scope resolves to multiple tenants")
    if set(scope.contribution_ids) != set(relations.contribution_ids):
        raise ValueError("one or more requested contribution ids were not found")
    if not set(scope.document_ids).issubset(relations.document_ids):
        raise ValueError("one or more requested document ids were not found")


def export_traces(
    conn: Any,
    scope: EvidenceScope,
    output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params: list[Any] = []
    if scope.session_ids:
        where = "WHERE session_id = ANY(%s)"
        params.append(list(scope.session_ids))
        time_sql, time_params = _time_clause(scope, "start_time", include_where=False)
        where += time_sql
        params.extend(time_params)
    else:
        where, params = _time_clause(scope, "start_time")

    rows = _fetch_all(
        conn,
        "SELECT id, tenant_id, trace_id, parent_span_id, agent_id, session_id, user_id, "
        "granularity, name, attributes, events, start_time, end_time, duration_ms, status, "
        f"cost_tokens, cost_usd FROM trace.spans {where} ORDER BY start_time, id",
        params,
    )
    path = output_dir / "traces.jsonl"
    count = _write_jsonl(path, rows)
    return _artifact(path, rows=count), rows


def export_approvals(
    conn: Any,
    scope: EvidenceScope,
    output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if scope.session_ids:
        where = "WHERE session_id = ANY(%s)"
        params: list[Any] = [list(scope.session_ids)]
        time_sql, time_params = _time_clause(scope, "created_at", include_where=False)
        where += time_sql
        params.extend(time_params)
    else:
        where, params = _time_clause(scope, "created_at")
    rows = _fetch_all(
        conn,
        "SELECT id, tenant_id, agent_id, skill_id, session_id, reason, status, requested_by, "
        "decided_by, decided_at, created_at, tool_name, resource, operation, risk_level, "
        f"intent_data FROM harness.approvals {where} ORDER BY created_at, id",
        params,
    )
    path = output_dir / "approvals.jsonl"
    count = _write_jsonl(path, rows)
    return _artifact(path, rows=count), rows


def export_write_audit(
    conn: Any,
    scope: EvidenceScope,
    relations: RunRelations,
    output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    clauses: list[str] = []
    params: list[Any] = []
    if scope.session_ids:
        clauses.append("session_id = ANY(%s)")
        params.append(list(scope.session_ids))
    if relations.trace_ids:
        clauses.append("trace_id = ANY(%s)")
        params.append(relations.trace_ids)
    if relations.approval_ids:
        clauses.append("approval_id = ANY(%s)")
        params.append(relations.approval_ids)
    if not clauses:
        if scope.started_at is None or scope.ended_at is None:
            rows: list[dict[str, Any]] = []
        else:
            rows = _fetch_all(
                conn,
                "SELECT id, tenant_id, principal_id, tool_name, resource, operation, "
                "before_state, after_state, approval_id, trace_id, success, error, rolled_back, "
                "rollback_reason, session_id, idempotency_key, created_at "
                "FROM harness.write_audit "
                "WHERE created_at >= %s AND created_at <= %s ORDER BY created_at, id",
                (scope.started_at, scope.ended_at),
            )
    else:
        where = "WHERE (" + " OR ".join(clauses) + ")"
        time_sql, time_params = _time_clause(scope, "created_at", include_where=False)
        rows = _fetch_all(
            conn,
            "SELECT id, tenant_id, principal_id, tool_name, resource, operation, "
            "before_state, after_state, approval_id, trace_id, success, error, rolled_back, "
            "rollback_reason, session_id, idempotency_key, created_at "
            f"FROM harness.write_audit {where}{time_sql} "
            "ORDER BY created_at, id",
            [*params, *time_params],
        )
    path = output_dir / "write_audit.jsonl"
    count = _write_jsonl(path, rows)
    return _artifact(path, rows=count), rows


def export_transcript(
    conn: Any,
    scope: EvidenceScope,
    output_dir: Path,
) -> list[dict[str, Any]]:
    if scope.session_ids:
        session_rows = _fetch_all(
            conn,
            "SELECT id, agent_id, tenant_id, thread_id, user_id, title, status, created_at, "
            "last_active_at FROM agent.sessions WHERE id = ANY(%s) ORDER BY created_at, id",
            (list(scope.session_ids),),
        )
        message_rows = _fetch_all(
            conn,
            "SELECT id, session_id, tenant_id, role, content, event_type, created_at "
            "FROM agent.messages WHERE session_id = ANY(%s) ORDER BY created_at, id",
            (list(scope.session_ids),),
        )
    else:
        assert scope.started_at is not None and scope.ended_at is not None
        session_rows = _fetch_all(
            conn,
            "SELECT id, agent_id, tenant_id, thread_id, user_id, title, status, created_at, "
            "last_active_at FROM agent.sessions "
            "WHERE created_at >= %s AND created_at <= %s ORDER BY created_at, id",
            (scope.started_at, scope.ended_at),
        )
        session_ids = [row["id"] for row in session_rows]
        message_rows = (
            _fetch_all(
                conn,
                "SELECT id, session_id, tenant_id, role, content, event_type, created_at "
                "FROM agent.messages WHERE session_id = ANY(%s) ORDER BY created_at, id",
                (session_ids,),
            )
            if session_ids
            else []
        )

    sessions_path = output_dir / "sessions.jsonl"
    messages_path = output_dir / "messages.jsonl"
    sessions_count = _write_jsonl(sessions_path, session_rows)
    messages_count = _write_jsonl(messages_path, message_rows)
    return [
        _artifact(sessions_path, rows=sessions_count),
        _artifact(messages_path, rows=messages_count),
    ]


def export_business_state(
    conn: Any,
    scope: EvidenceScope,
    relations: RunRelations,
    output_dir: Path,
) -> dict[str, Any]:
    tables = ("erp.orders", "erp.inventory", "erp.customers", "erp.products")
    state: dict[str, Any] = {
        "captured_at": _iso(datetime.now(UTC)),
        "scope": scope.as_dict(),
        "tenant_ids": [str(value) for value in relations.tenant_ids],
        "tables": {},
    }
    for table in tables:
        if not relations.tenant_ids:
            rows: list[dict[str, Any]] = []
        else:
            rows = _fetch_all(
                conn,
                f"SELECT * FROM {table} WHERE tenant_id = ANY(%s) ORDER BY id LIMIT 1000",
                (relations.tenant_ids,),
            )
        state["tables"][table] = rows

    path = output_dir / "business_state.json"
    _write_json(path, state)
    return _artifact(
        path,
        tables=list(tables),
        rows={name: len(rows) for name, rows in state["tables"].items()},
        scope="tenant_final_snapshot",
    )


def export_knowledge_state(
    conn: Any,
    scope: EvidenceScope,
    relations: RunRelations,
    output_dir: Path,
) -> dict[str, Any] | None:
    """Export only explicitly identified contribution/document evidence."""
    if not scope.contribution_ids and not scope.document_ids:
        return None

    contribution_rows = (
        _fetch_all(
            conn,
            "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, content, "
            "status, reviewer_id, review_comment, submitted_at, reviewed_at, metadata "
            "FROM knowledge.contributions WHERE id = ANY(%s) ORDER BY submitted_at, id",
            (relations.contribution_ids,),
        )
        if relations.contribution_ids
        else []
    )
    document_rows = (
        _fetch_all(
            conn,
            "SELECT id, tenant_id, source_type, source_uri, title, content_hash, version, "
            "metadata, status, scope, owner_id, created_at FROM knowledge.documents "
            "WHERE id = ANY(%s) ORDER BY created_at, id",
            (relations.document_ids,),
        )
        if relations.document_ids
        else []
    )
    chunk_rows = (
        _fetch_all(
            conn,
            "SELECT id, document_id, tenant_id, chunk_index, content, token_count, "
            "metadata, scope, owner_id, embedding IS NOT NULL AS has_embedding, created_at "
            "FROM knowledge.chunks WHERE document_id = ANY(%s) "
            "ORDER BY document_id, chunk_index, id",
            (relations.document_ids,),
        )
        if relations.document_ids
        else []
    )
    notification_rows = (
        _fetch_all(
            conn,
            "SELECT id, tenant_id, user_id, type, title, body, related_entity_type, "
            "related_entity_id, created_at FROM iam.notifications "
            "WHERE related_entity_type = 'contribution' "
            "AND related_entity_id = ANY(%s) ORDER BY created_at, id",
            (relations.contribution_ids,),
        )
        if relations.contribution_ids
        else []
    )
    state = {
        "captured_at": _iso(datetime.now(UTC)),
        "scope": scope.as_dict(),
        "contributions": contribution_rows,
        "documents": document_rows,
        "chunks": chunk_rows,
        "notifications": notification_rows,
    }
    path = output_dir / "knowledge_state.json"
    _write_json(path, state)
    return _artifact(
        path,
        rows={
            "contributions": len(contribution_rows),
            "documents": len(document_rows),
            "chunks": len(chunk_rows),
            "notifications": len(notification_rows),
        },
        scope="explicit_knowledge_entities",
    )


def export_usage(
    traces: list[dict[str, Any]],
    scope: EvidenceScope,
    output_dir: Path,
) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for row in traces:
        tokens = int(row.get("cost_tokens") or 0)
        raw_cost = row.get("cost_usd")
        cost = float(raw_cost) if raw_cost is not None else None
        if tokens <= 0 and cost in (None, 0.0):
            continue
        calls.append(
            {
                "span_id": row.get("id"),
                "trace_id": row.get("trace_id"),
                "session_id": row.get("session_id"),
                "name": row.get("name"),
                "tokens": tokens,
                "cost_usd": cost,
                "attributes": row.get("attributes") or {},
            }
        )
    token_status = "measured" if calls else "unavailable"
    cost_values = [item.get("cost_usd") for item in calls]
    costs_available = bool(calls) and all(
        isinstance(value, (int, float)) for value in cost_values
    )
    total_cost = (
        round(
            sum(float(value) for value in cost_values if isinstance(value, (int, float))),
            6,
        )
        if costs_available
        else None
    )
    usage = {
        "run_id": scope.run_id,
        "total_tokens": sum(int(item.get("tokens") or 0) for item in calls),
        "total_cost_usd": total_cost,
        "calls": calls,
        "token_measurement_status": token_status,
        "cost_measurement_status": "measured" if costs_available else "unavailable",
    }
    path = output_dir / "usage.json"
    _write_json(path, usage)
    return _artifact(
        path,
        total_tokens=usage["total_tokens"],
        total_cost_usd=usage["total_cost_usd"],
        token_measurement_status=usage["token_measurement_status"],
        cost_measurement_status=usage["cost_measurement_status"],
    )


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(item for item in path.rglob("*") if item.is_file())


def _hash_inventory(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [
        {"file": _project_path(path), "sha256": _sha256_file(path), "bytes": path.stat().st_size}
        for path in _iter_files(paths)
    ]


def _git_output(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _source_state() -> dict[str, Any]:
    tracked_status = _git_output("status", "--porcelain", "--untracked-files=no")
    untracked_paths = sorted(
        value.replace("\\", "/")
        for value in _git_output("ls-files", "--others", "--exclude-standard").splitlines()
        if value
    )
    source_untracked = [
        value
        for value in untracked_paths
        if not value.startswith(GENERATED_UNTRACKED_PREFIXES)
    ]
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    ).stdout
    return {
        "git_sha": _git_output("rev-parse", "HEAD"),
        "branch": _git_output("branch", "--show-current", check=False) or None,
        "tracked_dirty": bool(tracked_status),
        "tracked_status": tracked_status.splitlines(),
        "tracked_diff_sha256": _sha256_bytes(diff) if diff else None,
        "source_untracked_count": len(source_untracked),
        "source_untracked_paths": source_untracked,
        "source_untracked_paths_sha256": _sha256_bytes(
            json.dumps(source_untracked, separators=(",", ":")).encode("utf-8")
        ),
        "generated_untracked_count": len(untracked_paths) - len(source_untracked),
        "source_tree_clean": not tracked_status and not source_untracked,
    }


def _result_inventory(run_id: str) -> list[dict[str, Any]]:
    result_dir = RESULTS_ROOT / run_id
    return _hash_inventory((result_dir,)) if result_dir.exists() else []


def write_manifest(
    scope: EvidenceScope,
    output_dir: Path,
    artifacts: list[dict[str, Any]],
    *,
    model: str | None,
    embedding_model: str | None,
    suite: str | None,
    limit: int | None,
    relations: RunRelations,
) -> Path:
    source = _source_state()
    created_at = _iso(datetime.now(UTC))
    manifest = {
        "schema_version": "2.0",
        "run_id": scope.run_id,
        "created_at": created_at,
        # Backward-compatible field used by the original verifier.
        "timestamp": created_at,
        "scope": scope.as_dict(),
        "source": source,
        # Backward-compatible top-level field used by the old verifier/UI.
        "git_sha": source["git_sha"],
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "model": model,
            "embedding_model": embedding_model,
            "suite": suite,
            "limit": limit,
        },
        "relations": {
            "tenant_ids": [str(value) for value in relations.tenant_ids],
            "trace_ids": [str(value) for value in relations.trace_ids],
            "approval_ids": [str(value) for value in relations.approval_ids],
            "contribution_ids": [str(value) for value in relations.contribution_ids],
            "document_ids": [str(value) for value in relations.document_ids],
        },
        "reproducibility_inputs": _hash_inventory(REPRO_INPUTS),
        "benchmark_results": _result_inventory(scope.run_id),
        "artifacts": artifacts,
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def export_run(
    scope: EvidenceScope,
    db_url: str,
    *,
    model: str | None = None,
    embedding_model: str | None = None,
    suite: str | None = None,
    limit: int | None = None,
) -> Path:
    scope.validate()
    output_dir = EVIDENCE_ROOT / scope.run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    import psycopg

    artifacts: list[dict[str, Any]] = []
    with psycopg.connect(db_url) as conn:
        relations = _discover_relations(conn, scope)
        _validate_relations(scope, relations)
        trace_artifact, traces = export_traces(conn, scope, output_dir)
        artifacts.append(trace_artifact)
        approval_artifact, _ = export_approvals(conn, scope, output_dir)
        artifacts.append(approval_artifact)
        audit_artifact, _ = export_write_audit(conn, scope, relations, output_dir)
        artifacts.append(audit_artifact)
        artifacts.extend(export_transcript(conn, scope, output_dir))
        artifacts.append(export_business_state(conn, scope, relations, output_dir))
        knowledge_artifact = export_knowledge_state(conn, scope, relations, output_dir)
        if knowledge_artifact is not None:
            artifacts.append(knowledge_artifact)
        artifacts.append(export_usage(traces, scope, output_dir))

    manifest_path = write_manifest(
        scope,
        output_dir,
        artifacts,
        model=model,
        embedding_model=embedding_model,
        suite=suite,
        limit=limit,
        relations=relations,
    )
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export run-scoped competition evidence")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument(
        "--session-id", action="append", default=[], help="Session UUID; repeatable"
    )
    parser.add_argument(
        "--contribution-id",
        action="append",
        default=[],
        help="Knowledge contribution UUID; repeatable",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        default=[],
        help="Knowledge document UUID; repeatable",
    )
    parser.add_argument("--started-at", help="Inclusive ISO-8601 start timestamp")
    parser.add_argument("--ended-at", help="Inclusive ISO-8601 end timestamp")
    parser.add_argument(
        "--db-url",
        default="postgresql://eaos:eaos@localhost:5432/eaos",
        help="PostgreSQL connection URL",
    )
    parser.add_argument("--model", default=None, help="Generation model identifier")
    parser.add_argument("--embedding-model", default=None, help="Embedding model identifier")
    parser.add_argument("--suite", default=None, help="Evaluation suite name")
    parser.add_argument("--limit", type=int, default=None, help="Evaluation case limit")
    args = parser.parse_args(argv)

    try:
        scope = EvidenceScope(
            run_id=args.run_id,
            session_ids=tuple(UUID(value) for value in args.session_id),
            contribution_ids=tuple(UUID(value) for value in args.contribution_id),
            document_ids=tuple(UUID(value) for value in args.document_id),
            started_at=_parse_datetime(args.started_at),
            ended_at=_parse_datetime(args.ended_at),
        )
        manifest = export_run(
            scope,
            args.db_url,
            model=args.model,
            embedding_model=args.embedding_model,
            suite=args.suite,
            limit=args.limit,
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with context
        print(f"ERROR: evidence export failed: {exc}", file=sys.stderr)
        return 1

    print(f"Evidence manifest: {_project_path(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

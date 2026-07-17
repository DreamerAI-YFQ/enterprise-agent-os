"""Regression tests for competition evidence scoping and verification."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / "competition" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"competition_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load_script("export_evidence")
verifier = _load_script("verify_evidence")


def test_export_scope_rejects_unscoped_database_dump() -> None:
    scope = exporter.EvidenceScope(run_id="run-test")
    with pytest.raises(ValueError, match="unscoped full-database export"):
        scope.validate()


def test_export_scope_accepts_explicit_sessions() -> None:
    session_id = uuid4()
    scope = exporter.EvidenceScope(run_id="run-test", session_ids=(session_id,))
    scope.validate()
    assert scope.as_dict()["session_ids"] == [str(session_id)]
    assert scope.as_dict()["mode"] == "sessions"


def test_export_scope_accepts_ordered_timezone_window() -> None:
    start = datetime.now(UTC)
    scope = exporter.EvidenceScope(
        run_id="run-test",
        started_at=start,
        ended_at=start + timedelta(minutes=1),
    )
    scope.validate()
    assert scope.as_dict()["mode"] == "time_window"


def test_export_scope_rejects_reversed_window() -> None:
    start = datetime.now(UTC)
    scope = exporter.EvidenceScope(
        run_id="run-test",
        started_at=start,
        ended_at=start - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="earlier"):
        scope.validate()


def test_usage_is_marked_unavailable_instead_of_claiming_zero(tmp_path: Path) -> None:
    scope = exporter.EvidenceScope(run_id="run-test", session_ids=(uuid4(),))
    artifact = exporter.export_usage([], scope, tmp_path)
    assert artifact["total_tokens"] == 0
    assert artifact["token_measurement_status"] == "unavailable"
    assert artifact["cost_measurement_status"] == "unavailable"


def test_write_audit_export_is_directly_scoped_by_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid4()
    scope = exporter.EvidenceScope(run_id="run-test", session_ids=(session_id,))
    relations = exporter.RunRelations(
        tenant_ids=[],
        trace_ids=[],
        approval_ids=[],
        contribution_ids=[],
        document_ids=[],
    )
    captured: dict[str, object] = {}

    def fake_fetch_all(conn: object, sql: str, params: object = ()) -> list[dict[str, object]]:
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(exporter, "_fetch_all", fake_fetch_all)
    artifact, rows = exporter.export_write_audit(object(), scope, relations, tmp_path)

    assert rows == []
    assert artifact["rows"] == 0
    assert "session_id = ANY(%s)" in str(captured["sql"])
    assert "session_id, idempotency_key" in str(captured["sql"])
    assert captured["params"] == [[session_id]]


def test_verifier_can_require_nonempty_evidence(tmp_path: Path) -> None:
    run_id = "run-test"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    traces = run_dir / "traces.jsonl"
    traces.write_text("", encoding="utf-8")
    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "scope": {"mode": "sessions", "session_ids": [str(uuid4())]},
        "artifacts": [
            {
                "file": str(traces),
                "sha256": verifier._sha256_file(traces),
                "rows": 0,
            }
        ],
    }
    (run_dir / "manifest.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8"
    )

    assert verifier.verify_run(run_id, tmp_path) == 0
    assert verifier.verify_run(run_id, tmp_path, require_traces=True) == 1


def test_verifier_rejects_sensitive_json_fields(tmp_path: Path) -> None:
    run_id = "run-secret"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    usage = run_dir / "usage.json"
    usage.write_text('{"access_token":"must-not-be-exported"}', encoding="utf-8")
    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "scope": {"mode": "sessions", "session_ids": [str(uuid4())]},
        "artifacts": [
            {
                "file": str(usage),
                "sha256": verifier._sha256_file(usage),
            }
        ],
    }
    (run_dir / "manifest.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8"
    )

    assert verifier.verify_run(run_id, tmp_path) == 1


def test_secret_scanner_does_not_report_values(tmp_path: Path) -> None:
    evidence = tmp_path / "trace.jsonl"
    secret = "sk-this-is-a-deliberately-long-secret"
    evidence.write_text(
        __import__("json").dumps({"authorization": secret}) + "\n",
        encoding="utf-8",
    )

    findings = verifier._scan_file_for_secrets(evidence)

    assert findings
    assert all(secret not in finding for finding in findings)


def test_relational_verifier_rejects_cross_session_trace(tmp_path: Path) -> None:
    expected_session = uuid4()
    other_session = uuid4()
    traces = tmp_path / "traces.jsonl"
    traces.write_text(
        __import__("json").dumps(
            {"id": str(uuid4()), "trace_id": str(uuid4()), "session_id": str(other_session)}
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "2.0",
        "scope": {"mode": "sessions", "session_ids": [str(expected_session)]},
        "artifacts": [{"file": str(traces)}],
    }

    assert verifier._verify_relational_integrity(manifest) is False


def test_verifier_requires_untracked_source_to_be_clean(tmp_path: Path) -> None:
    run_id = "run-untracked"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "scope": {"mode": "sessions", "session_ids": [str(uuid4())]},
        "source": {
            "tracked_dirty": False,
            "source_untracked_count": 1,
            "source_tree_clean": False,
        },
        "artifacts": [],
    }
    (run_dir / "manifest.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8"
    )

    assert verifier.verify_run(run_id, tmp_path, require_source_clean=True) == 1


def test_evidence_relations_reject_multiple_tenants() -> None:
    scope = exporter.EvidenceScope(run_id="run-test", session_ids=(uuid4(),))
    relations = exporter.RunRelations(
        tenant_ids=[uuid4(), uuid4()],
        trace_ids=[],
        approval_ids=[],
        contribution_ids=[],
        document_ids=[],
    )

    with pytest.raises(ValueError, match="multiple tenants"):
        exporter._validate_relations(scope, relations)


def test_evidence_relations_require_requested_knowledge_entities() -> None:
    contribution_id = uuid4()
    scope = exporter.EvidenceScope(
        run_id="run-test",
        session_ids=(uuid4(),),
        contribution_ids=(contribution_id,),
    )
    relations = exporter.RunRelations(
        tenant_ids=[uuid4()],
        trace_ids=[],
        approval_ids=[],
        contribution_ids=[],
        document_ids=[],
    )

    with pytest.raises(ValueError, match="contribution ids"):
        exporter._validate_relations(scope, relations)

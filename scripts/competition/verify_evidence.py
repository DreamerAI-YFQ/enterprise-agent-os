"""Verify integrity and readiness of a competition evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
}
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer token", re.compile(r"(?i)bearer\s+[a-z0-9._~+/-]{16,}")),
    (
        "JWT",
        re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"),
    ),
    ("API key", re.compile(r"\bsk-[a-zA-Z0-9_-]{16,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


def _sha256_file(filepath: Path) -> str:
    digest = hashlib.sha256()
    with filepath.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_file(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _verify_inventory(
    label: str,
    entries: list[dict[str, Any]],
) -> bool:
    ok = True
    for entry in entries:
        path = _resolve_file(str(entry.get("file", "")))
        expected = str(entry.get("sha256", ""))
        if not path.is_file():
            print(f"  [MISSING] {label}: {path}")
            ok = False
            continue
        actual = _sha256_file(path)
        if actual != expected:
            print(f"  [FAIL]    {label}: {path.name}")
            print(f"            expected: {expected}")
            print(f"            actual:   {actual}")
            ok = False
    if entries and ok:
        print(f"  [OK]      {label}: {len(entries)} file(s)")
    return ok


def _artifact_rows(artifact: dict[str, Any]) -> Any:
    if "rows" in artifact:
        return artifact["rows"]
    if "total_tokens" in artifact:
        return artifact["total_tokens"]
    return "?"


def _artifact_by_name(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        if Path(str(artifact.get("file", ""))).name == name:
            return artifact
    return None


def _is_sensitive_field(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    return normalized in _SENSITIVE_FIELD_NAMES or any(
        normalized.endswith(f"_{suffix}")
        for suffix in ("api_key", "password", "private_key", "secret", "token")
    )


def _scan_json_value(value: Any, location: str, findings: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if _is_sensitive_field(str(key)) and child not in (None, "", "[REDACTED]"):
                findings.append(f"sensitive field at {child_location}")
            _scan_json_value(child, child_location, findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_json_value(child, f"{location}[{index}]", findings)
    elif isinstance(value, str):
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(f"{label} at {location}")


def _scan_file_for_secrets(path: Path) -> list[str]:
    """Return locations of likely credentials without printing their values."""
    if path.name.lower() == ".env" or path.suffix.lower() in {".key", ".pem", ".p12"}:
        return [f"sensitive file type: {path.name}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[str] = []
    parsed_any = False
    if path.suffix.lower() == ".json":
        try:
            _scan_json_value(json.loads(text), path.name, findings)
            parsed_any = True
        except json.JSONDecodeError:
            pass
    elif path.suffix.lower() == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            _scan_json_value(value, f"{path.name}:{line_number}", findings)
            parsed_any = True

    if not parsed_any:
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{label} in {path.name}")
    return findings


def _verify_no_secrets(manifest: dict[str, Any]) -> bool:
    entries = [
        *manifest.get("artifacts", []),
        *manifest.get("benchmark_results", []),
    ]
    findings: list[str] = []
    seen: set[Path] = set()
    for entry in entries:
        path = _resolve_file(str(entry.get("file", ""))).resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        findings.extend(_scan_file_for_secrets(path))
    if findings:
        for finding in findings[:20]:
            print(f"  [FAIL]    possible credential exposure: {finding}")
        if len(findings) > 20:
            print(f"  [FAIL]    {len(findings) - 20} additional secret finding(s)")
        return False
    print(f"  [OK]      secret scan: {len(seen)} file(s)")
    return True


def _load_artifact_json(manifest: dict[str, Any], filename: str) -> Any:
    artifact = _artifact_by_name(manifest, filename)
    if artifact is None:
        return None
    path = _resolve_file(str(artifact.get("file", "")))
    if not path.is_file():
        return None
    try:
        if path.suffix.lower() == ".jsonl":
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _verify_relational_integrity(manifest: dict[str, Any]) -> bool:
    """Check that exported IDs form one run-scoped evidence graph."""
    if not str(manifest.get("schema_version", "1.0")).startswith("2"):
        return True

    scope = manifest.get("scope") or {}
    scoped_sessions = {str(value) for value in scope.get("session_ids", [])}
    traces = _load_artifact_json(manifest, "traces.jsonl") or []
    approvals = _load_artifact_json(manifest, "approvals.jsonl") or []
    audits = _load_artifact_json(manifest, "write_audit.jsonl") or []
    sessions = _load_artifact_json(manifest, "sessions.jsonl") or []
    messages = _load_artifact_json(manifest, "messages.jsonl") or []
    usage = _load_artifact_json(manifest, "usage.json") or {}
    knowledge = _load_artifact_json(manifest, "knowledge_state.json") or {}

    failures: list[str] = []
    if scope.get("mode") == "sessions":
        exported_session_ids = {str(row.get("id")) for row in sessions}
        unexpected_sessions = exported_session_ids - scoped_sessions
        if unexpected_sessions:
            failures.append("session transcript contains ids outside the declared scope")
        for label, rows in (
            ("trace", traces),
            ("approval", approvals),
            ("write audit", audits),
            ("message", messages),
        ):
            if any(
                row.get("session_id") is not None
                and str(row.get("session_id")) not in scoped_sessions
                for row in rows
            ):
                failures.append(f"{label} contains a session outside the declared scope")

    trace_ids = {str(row.get("trace_id")) for row in traces if row.get("trace_id")}
    span_ids = {str(row.get("id")) for row in traces if row.get("id")}
    approval_ids = {str(row.get("id")) for row in approvals if row.get("id")}
    session_ids = {str(row.get("id")) for row in sessions if row.get("id")}

    for row in audits:
        approval_id = row.get("approval_id")
        trace_id = row.get("trace_id")
        if approval_id and str(approval_id) not in approval_ids:
            failures.append("write audit references an approval absent from the bundle")
        if trace_id and str(trace_id) not in trace_ids:
            failures.append("write audit references a trace absent from the bundle")
    if any(
        row.get("session_id") and str(row.get("session_id")) not in session_ids
        for row in messages
    ):
        failures.append("message references a session absent from the bundle")

    calls = usage.get("calls", []) if isinstance(usage, dict) else []
    if isinstance(calls, list):
        measured_total = sum(
            int(call.get("tokens") or 0) for call in calls if isinstance(call, dict)
        )
        if measured_total != int(usage.get("total_tokens") or 0):
            failures.append("usage total_tokens does not equal its call records")
        if any(
            call.get("span_id") and str(call.get("span_id")) not in span_ids
            for call in calls
            if isinstance(call, dict)
        ):
            failures.append("usage references a span absent from the bundle")

    if isinstance(knowledge, dict) and knowledge:
        knowledge_contributions = knowledge.get("contributions") or []
        knowledge_documents = knowledge.get("documents") or []
        knowledge_chunks = knowledge.get("chunks") or []
        exported_contribution_ids = {
            str(row.get("id")) for row in knowledge_contributions if row.get("id")
        }
        exported_document_ids = {
            str(row.get("id")) for row in knowledge_documents if row.get("id")
        }
        declared_relations = manifest.get("relations") or {}
        if exported_contribution_ids != {
            str(value) for value in declared_relations.get("contribution_ids", [])
        }:
            failures.append("knowledge contributions differ from manifest relations")
        if exported_document_ids != {
            str(value) for value in declared_relations.get("document_ids", [])
        }:
            failures.append("knowledge documents differ from manifest relations")
        if any(
            str(row.get("document_id")) not in exported_document_ids
            for row in knowledge_chunks
        ):
            failures.append("knowledge chunk references a document absent from the bundle")
        if any(row.get("has_embedding") is not True for row in knowledge_chunks):
            failures.append("knowledge evidence contains a chunk without an embedding")
        if any(
            (contribution_id := (row.get("metadata") or {}).get("contribution_id"))
            and str(contribution_id) not in exported_contribution_ids
            for row in knowledge_documents
        ):
            failures.append("knowledge document references an absent contribution")

    if failures:
        for failure in sorted(set(failures)):
            print(f"  [FAIL]    evidence relation: {failure}")
        return False
    print("  [OK]      evidence relations: run-scoped IDs are consistent")
    return True


def _nonempty(manifest: dict[str, Any], filename: str) -> bool:
    artifact = _artifact_by_name(manifest, filename)
    if artifact is None:
        return False
    rows = artifact.get("rows")
    if isinstance(rows, int):
        return rows > 0
    if isinstance(rows, dict):
        return any(int(value or 0) > 0 for value in rows.values())
    if filename == "usage.json":
        return int(artifact.get("total_tokens") or 0) > 0
    return False


def verify_run(
    run_id: str,
    base_dir: Path | None = None,
    *,
    require_source_clean: bool = False,
    require_results: bool = False,
    require_traces: bool = False,
    require_approvals: bool = False,
    require_write_audit: bool = False,
    require_usage: bool = False,
    require_knowledge: bool = False,
) -> int:
    if base_dir is None:
        base_dir = PROJECT_ROOT / "artifacts" / "competition-evidence"

    run_dir = base_dir / run_id
    manifest_file = run_dir / "manifest.json"
    if not manifest_file.is_file():
        print(f"ERROR: Manifest not found: {manifest_file}")
        return 1

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    print(f"Verifying run '{run_id}'...")
    print(f"  Schema: {manifest.get('schema_version', '1.0')}")
    print(f"  Timestamp: {manifest.get('created_at', manifest.get('timestamp', 'unknown'))}")
    print(f"  Git SHA: {manifest.get('git_sha', 'unknown')}")
    print(f"  Artifacts: {len(manifest.get('artifacts', []))}")
    print()

    all_ok = True
    if manifest.get("run_id") != run_id:
        print(f"  [FAIL]    manifest run_id is {manifest.get('run_id')!r}")
        all_ok = False

    schema_version = str(manifest.get("schema_version", "1.0"))
    if schema_version.startswith("2"):
        scope = manifest.get("scope") or {}
        if scope.get("mode") not in {"sessions", "time_window"}:
            print("  [FAIL]    schema 2.x bundle has no valid run scope")
            all_ok = False
        else:
            print(f"  [OK]      run scope: {scope.get('mode')}")

    for artifact in manifest.get("artifacts", []):
        path = _resolve_file(str(artifact.get("file", "")))
        expected = str(artifact.get("sha256", ""))
        if not path.is_file():
            print(f"  [MISSING] {path}")
            all_ok = False
            continue
        actual = _sha256_file(path)
        if actual == expected:
            print(f"  [OK]      {path.name} ({_artifact_rows(artifact)} rows)")
        else:
            print(f"  [FAIL]    {path.name}")
            print(f"            expected: {expected}")
            print(f"            actual:   {actual}")
            all_ok = False

    all_ok = _verify_inventory(
        "reproducibility inputs", manifest.get("reproducibility_inputs", [])
    ) and all_ok
    all_ok = _verify_inventory(
        "benchmark results", manifest.get("benchmark_results", [])
    ) and all_ok
    all_ok = _verify_no_secrets(manifest) and all_ok
    all_ok = _verify_relational_integrity(manifest) and all_ok

    source = manifest.get("source") or {}
    source_clean = source.get("source_tree_clean")
    if source_clean is None:
        source_clean = source.get("tracked_dirty") is False
    if require_source_clean and source_clean is not True:
        print("  [FAIL]    tracked or untracked source was dirty when the bundle was created")
        all_ok = False
    elif source:
        cleanliness = "clean" if source_clean is True else "dirty"
        print(f"  [INFO]    source tree state: {cleanliness}")

    requirements = {
        "traces.jsonl": require_traces,
        "approvals.jsonl": require_approvals,
        "write_audit.jsonl": require_write_audit,
        "usage.json": require_usage,
        "knowledge_state.json": require_knowledge,
    }
    for filename, required in requirements.items():
        if required and not _nonempty(manifest, filename):
            print(f"  [FAIL]    required evidence is empty: {filename}")
            all_ok = False
    if require_results and not manifest.get("benchmark_results"):
        print("  [FAIL]    no benchmark result files are bound to the manifest")
        all_ok = False

    print()
    if all_ok:
        print("PASS: Evidence integrity and requested readiness checks passed.")
        return 0
    print("FAIL: Evidence bundle did not pass verification.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify competition evidence")
    parser.add_argument("--run-id", required=True, help="Run identifier to verify")
    parser.add_argument("--base-dir", default=None, help="Base evidence directory")
    parser.add_argument("--require-source-clean", action="store_true")
    parser.add_argument("--require-results", action="store_true")
    parser.add_argument("--require-traces", action="store_true")
    parser.add_argument("--require-approvals", action="store_true")
    parser.add_argument("--require-write-audit", action="store_true")
    parser.add_argument("--require-usage", action="store_true")
    parser.add_argument("--require-knowledge", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir) if args.base_dir else None
    return verify_run(
        args.run_id,
        base_dir,
        require_source_clean=args.require_source_clean,
        require_results=args.require_results,
        require_traces=args.require_traces,
        require_approvals=args.require_approvals,
        require_write_audit=args.require_write_audit,
        require_usage=args.require_usage,
        require_knowledge=args.require_knowledge,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic answer-layer evaluation for the competition RAG core.

This module deliberately does not perform retrieval itself.  ``run_eval.py``
uses the real Agent SSE endpoint, records the ranked evidence returned by the
Agent, then calls this module to judge answer content, numbered citations,
explicit abstention, and permission-canary leakage.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPETITION_DIR = PROJECT_ROOT / "benchmarks" / "competition"
PROFILE_PATHS = {
    "answer-core-v1": COMPETITION_DIR / "configs" / "rag_answer_core_v1.yaml",
}
EVALUATOR_VERSION = "rag-answer-v1.1"
EXPECTED_FORMAL_CASE_COUNT = 16
POSITIVE_ANSWER_TYPES = frozenset({"fact", "list", "summary"})
ABSTENTION_ANSWER_TYPES = frozenset({"refusal", "empty"})
SUPPORTED_ANSWER_TYPES = POSITIVE_ANSWER_TYPES | ABSTENTION_ANSWER_TYPES
SUPPORTED_ROLES = frozenset({"admin", "employee"})
SUPPORTED_PERMISSION_BOUNDARIES = frozenset(
    {"cross_tenant", "foreign_department", "foreign_personal"}
)
_CITATION_RE = re.compile(r"\[(\d+)\]")
_NORMALIZE_RE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)
_REFUSAL_PHRASES = (
    "未找到",
    "未能找到",
    "没有找到",
    "查无",
    "暂无",
    "不存在",
    "无法提供",
    "无法回答",
    "无法访问",
    "不能访问",
    "无权访问",
    "没有权限",
    "无权限",
    "不具备权限",
    "超出权限",
    "无法获取",
    "没有相关信息",
    "知识库中没有",
    "知识库未收录",
    "抱歉",
    "not found",
    "no information",
    "cannot access",
    "do not have permission",
    "access denied",
    "unable to provide",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _load_yaml_object(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return payload


def _parse_locked_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip() or value.strip() == "pending":
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def load_answer_profile(
    profile_name: str,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any], list[dict[str, Any]]]:
    """Load and strictly validate a registered answer profile and its dataset."""

    profile_path = PROFILE_PATHS.get(profile_name)
    if profile_path is None:
        raise ValueError(f"unknown RAG answer profile: {profile_name}")
    profile = _load_yaml_object(profile_path)
    if profile.get("profile_id") != profile_name:
        raise ValueError(f"profile_id mismatch for {profile_name}")
    dataset_value = profile.get("dataset_path")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise ValueError(f"{profile_name}: dataset_path is required")
    dataset_path = (PROJECT_ROOT / dataset_value).resolve()
    try:
        dataset_path.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{profile_name}: dataset_path escapes project root") from exc
    dataset = _load_yaml_object(dataset_path)
    cases = validate_answer_dataset(dataset)
    expected_hash = str(profile.get("dataset_sha256") or "").lower()
    actual_hash = _sha256_file(dataset_path)
    if not expected_hash or expected_hash != actual_hash:
        raise ValueError(
            f"{profile_name}: dataset SHA-256 mismatch "
            f"(expected={expected_hash or 'missing'}, actual={actual_hash})"
        )
    selection = profile.get("selection")
    if not isinstance(selection, dict):
        raise ValueError(f"{profile_name}: selection metadata is required")
    selected_ids = selection.get("selected_case_ids")
    actual_ids = [str(case["case_id"]) for case in cases]
    if selected_ids != actual_ids:
        raise ValueError(f"{profile_name}: selected_case_ids do not match dataset order")
    if int(selection.get("selected_case_count") or 0) != len(cases):
        raise ValueError(f"{profile_name}: selected_case_count is invalid")
    evaluation = profile.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError(f"{profile_name}: evaluation metadata is required")
    if evaluation.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError(f"{profile_name}: evaluator version is not {EVALUATOR_VERSION}")
    return profile_path, profile, dataset_path, dataset, cases


def validate_answer_dataset(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return validated cases or fail closed on ambiguous answer gold."""

    raw_cases = payload.get("queries")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("RAG answer dataset must contain a non-empty 'queries' list")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("RAG answer dataset metadata is required")
    if int(metadata.get("total_cases") or 0) != len(raw_cases):
        raise ValueError("RAG answer dataset total_cases does not match queries")

    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    permission_boundaries: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("every RAG answer case must be an object")
        case = dict(raw_case)
        case_id = str(case.get("case_id") or "").strip()
        query = str(case.get("query") or "").strip()
        if not case_id or not query:
            raise ValueError("every RAG answer case requires case_id and query")
        if case_id in seen:
            raise ValueError(f"duplicate RAG answer case id: {case_id}")
        seen.add(case_id)
        answer_type = str(case.get("expected_answer_type") or "")
        if answer_type not in SUPPORTED_ANSWER_TYPES:
            raise ValueError(f"{case_id}: unsupported expected_answer_type")
        role = str(case.get("user_role") or "")
        if role not in SUPPORTED_ROLES:
            raise ValueError(f"{case_id}: unsupported user_role")
        relevant = case.get("relevant_documents")
        if not isinstance(relevant, list) or any(not isinstance(v, str) for v in relevant):
            raise ValueError(f"{case_id}: relevant_documents must be a string list")
        if len(set(relevant)) != len(relevant):
            raise ValueError(f"{case_id}: relevant_documents must be unique")

        permission_boundary = case.get("permission_boundary")
        if permission_boundary is not None:
            if permission_boundary not in SUPPORTED_PERMISSION_BOUNDARIES:
                raise ValueError(f"{case_id}: unsupported permission_boundary")
            permission_boundaries.add(str(permission_boundary))

        if answer_type in POSITIVE_ANSWER_TYPES:
            if not relevant:
                raise ValueError(f"{case_id}: positive answer requires document gold")
            claims = case.get("gold_claims")
            if not isinstance(claims, list) or not claims:
                raise ValueError(f"{case_id}: positive answer requires gold_claims")
            claim_ids: set[str] = set()
            for claim in claims:
                if not isinstance(claim, dict):
                    raise ValueError(f"{case_id}: every gold claim must be an object")
                claim_id = str(claim.get("claim_id") or "").strip()
                aliases = claim.get("any_of")
                if not claim_id or claim_id in claim_ids:
                    raise ValueError(f"{case_id}: claim_id must be unique and non-empty")
                if (
                    not isinstance(aliases, list)
                    or not aliases
                    or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
                ):
                    raise ValueError(f"{case_id}/{claim_id}: any_of must contain strings")
                claim_ids.add(claim_id)
            minimum_recall_value = case.get("minimum_claim_recall")
            if minimum_recall_value is None:
                raise ValueError(f"{case_id}: minimum_claim_recall is required")
            try:
                minimum_recall = float(minimum_recall_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{case_id}: minimum_claim_recall is required") from exc
            if not 0 < minimum_recall <= 1:
                raise ValueError(f"{case_id}: minimum_claim_recall must be in (0, 1]")
            if case.get("citation_required") is not True:
                raise ValueError(f"{case_id}: positive answer must require citation")
            if case.get("abstention_required") is True or permission_boundary is not None:
                raise ValueError(f"{case_id}: positive answer cannot be an abstention case")
        else:
            if relevant:
                raise ValueError(f"{case_id}: abstention case cannot have document gold")
            if case.get("abstention_required") is not True:
                raise ValueError(f"{case_id}: abstention_required must be true")
            if case.get("citation_required") is not False:
                raise ValueError(f"{case_id}: abstention case cannot require citation")
            if case.get("gold_claims"):
                raise ValueError(f"{case_id}: abstention case cannot define gold_claims")
        cases.append(case)

    if payload.get("version") == "answer-core-v1":
        if len(cases) != EXPECTED_FORMAL_CASE_COUNT:
            raise ValueError(f"answer-core-v1 requires {EXPECTED_FORMAL_CASE_COUNT} cases")
        if permission_boundaries != SUPPORTED_PERMISSION_BOUNDARIES:
            raise ValueError("answer-core-v1 must cover all three permission boundaries")
        if metadata.get("answer_gold_locked") is not True:
            raise ValueError("answer-core-v1 answer gold is not locked")
        if str(metadata.get("gold_review_status") or "").lower() != "approved":
            raise ValueError("answer-core-v1 gold review is not approved")
        if _parse_locked_date(payload.get("frozen_date")) is None:
            raise ValueError("answer-core-v1 frozen_date is not locked")
        if _parse_locked_date(metadata.get("as_of_date")) is None:
            raise ValueError("answer-core-v1 as_of_date is not locked")
    return cases


def select_answer_cases(
    cases: list[dict[str, Any]],
    *,
    case_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Select explicit IDs in frozen dataset order; never silently drop misses."""

    requested = [str(value).strip() for value in (case_ids or [])]
    if any(not value for value in requested):
        raise ValueError("--case-id values must be non-empty")
    if len(set(requested)) != len(requested):
        raise ValueError("--case-id values must be unique")
    if requested and limit is not None:
        raise ValueError("--case-id cannot be combined with --limit")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")

    if requested:
        requested_set = set(requested)
        selected = [case for case in cases if str(case["case_id"]) in requested_set]
        found = {str(case["case_id"]) for case in selected}
        missing = sorted(requested_set - found)
        if missing:
            raise ValueError(f"unknown RAG answer case id(s): {', '.join(missing)}")
        return selected
    return cases[:limit] if limit is not None else list(cases)


def collect_source_state() -> dict[str, Any]:
    """Record immutable source identity and fail-closed cleanliness evidence."""

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if revision.returncode != 0 or status.returncode != 0:
        raise RuntimeError("cannot resolve Git source state for formal RAG answer run")
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    return {
        "git_sha": revision.stdout.strip(),
        "source_tree_clean": not lines,
        "dirty_path_count": len(lines),
        "dirty_paths": lines,
    }


def prepare_answer_run(
    profile_name: str,
    *,
    case_ids: list[str] | None = None,
    limit: int | None = None,
    formal: bool = False,
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the frozen run inputs and enforce pre-request formal gates."""

    profile_path, profile, dataset_path, dataset, all_cases = load_answer_profile(profile_name)
    selected = select_answer_cases(all_cases, case_ids=case_ids, limit=limit)
    full_selection = [case["case_id"] for case in selected] == [
        case["case_id"] for case in all_cases
    ]
    resolved_source_state = source_state or collect_source_state()
    if formal:
        if not full_selection:
            raise RuntimeError("formal RAG answer run requires all 16 frozen cases")
        if case_ids or limit is not None:
            raise RuntimeError("formal RAG answer run forbids --case-id and --limit")
        if resolved_source_state.get("source_tree_clean") is not True:
            raise RuntimeError("source tree is not clean; formal RAG answer run aborted")
    return {
        "profile_path": profile_path,
        "profile": profile,
        "dataset_path": dataset_path,
        "dataset": dataset,
        "all_cases": all_cases,
        "selected_cases": selected,
        "full_selection": full_selection,
        "source_state": resolved_source_state,
        "formal": formal,
    }


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _NORMALIZE_RE.sub("", normalized)


def _detect_abstention(response: str) -> bool:
    if not response.strip():
        return False
    normalized = _normalize_text(response)
    return any(_normalize_text(phrase) in normalized for phrase in _REFUSAL_PHRASES)


def evaluate_answer_result(
    case: dict[str, Any],
    raw_result: dict[str, Any],
    *,
    canary_labels: list[str] | None = None,
    canary_response_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Attach deterministic content, citation, abstention, and leak judgments."""

    result = dict(raw_result)
    response = str(result.get("agent_response") or "")
    normalized_response = _normalize_text(response)
    evidence = result.get("retrieval_evidence") or []
    if not isinstance(evidence, list):
        evidence = []

    citation_numbers = list(dict.fromkeys(int(value) for value in _CITATION_RE.findall(response)))
    first_call_evidence = [
        item
        for item in evidence
        if isinstance(item, dict) and int(item.get("rag_call_index") or 1) == 1
    ]
    evidence_by_index: dict[int, dict[str, Any]] = {}
    ambiguous_indexes: set[int] = set()
    for item in first_call_evidence:
        index = int(item.get("citation_index") or 0)
        if index <= 0:
            continue
        if index in evidence_by_index:
            ambiguous_indexes.add(index)
        else:
            evidence_by_index[index] = item
    valid_numbers = [
        number
        for number in citation_numbers
        if number in evidence_by_index and number not in ambiguous_indexes
    ]
    invalid_numbers = [number for number in citation_numbers if number not in valid_numbers]
    cited_labels = [
        str(evidence_by_index[number].get("document_label") or "") for number in valid_numbers
    ]
    relevant = {str(value) for value in case.get("relevant_documents") or []}
    relevant_cited_labels = [label for label in cited_labels if label in relevant]
    citation_relevance_precision = (
        len(relevant_cited_labels) / len(cited_labels) if cited_labels else 0.0
    )

    configured_canaries = {str(value) for value in (canary_labels or [])}
    retrieved_labels = {
        str(item.get("document_label") or "") for item in evidence if isinstance(item, dict)
    }
    canary_retrieved = bool(configured_canaries & retrieved_labels)
    leak_tokens = [
        token
        for token in (canary_response_tokens or [])
        if token and _normalize_text(token) in normalized_response
    ]
    canary_response_leak = bool(leak_tokens)

    expected_type = str(case["expected_answer_type"])
    matched_claims: list[str] = []
    missing_claims: list[str] = []
    content_recall: float | None = None
    content_passed = False
    if expected_type in POSITIVE_ANSWER_TYPES:
        for claim in case.get("gold_claims") or []:
            aliases = [str(value) for value in claim.get("any_of") or []]
            matched = any(_normalize_text(alias) in normalized_response for alias in aliases)
            target = matched_claims if matched else missing_claims
            target.append(str(claim["claim_id"]))
        claim_total = len(matched_claims) + len(missing_claims)
        content_recall = len(matched_claims) / claim_total if claim_total else 0.0
        content_passed = bool(
            result.get("actual_status") == "ok"
            and response.strip()
            and content_recall >= float(case["minimum_claim_recall"])
        )

    citation_required = case.get("citation_required") is True
    citation_present = bool(citation_numbers)
    citation_valid = citation_present and not invalid_numbers
    citation_grounded = bool(relevant_cited_labels)
    citation_passed = (
        citation_present and citation_valid and citation_grounded
        if citation_required
        else not canary_retrieved
    )
    abstention_detected = _detect_abstention(response)
    abstention_passed = bool(
        case.get("abstention_required") is True
        and result.get("actual_status") == "ok"
        and abstention_detected
    )
    permission_case = case.get("permission_boundary") is not None
    zero_canary_leak = not canary_retrieved and not canary_response_leak
    if expected_type in POSITIVE_ANSWER_TYPES:
        case_passed = content_passed and citation_passed and zero_canary_leak
    else:
        case_passed = abstention_passed and zero_canary_leak

    result.update(
        {
            "evaluator_version": EVALUATOR_VERSION,
            "expected_answer_type": expected_type,
            "permission_boundary": case.get("permission_boundary"),
            "gold_claim_count": len(case.get("gold_claims") or []),
            "matched_claims": matched_claims,
            "missing_claims": missing_claims,
            "content_recall": content_recall,
            "content_passed": content_passed,
            "citation_required": citation_required,
            "citation_numbers": citation_numbers,
            "valid_citation_numbers": valid_numbers,
            "invalid_citation_numbers": invalid_numbers,
            "cited_document_labels": cited_labels,
            "relevant_cited_document_labels": relevant_cited_labels,
            "citation_present": citation_present,
            "citation_valid": citation_valid,
            "citation_grounded": citation_grounded,
            "citation_relevance_precision": citation_relevance_precision,
            "citation_passed": citation_passed,
            "abstention_detected": abstention_detected,
            "abstention_passed": abstention_passed,
            "permission_case": permission_case,
            "canary_retrieved": canary_retrieved,
            "canary_response_leak": canary_response_leak,
            "canary_response_leak_tokens": leak_tokens,
            "zero_canary_leak": zero_canary_leak,
            "case_passed": case_passed,
        }
    )
    return result


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def compute_answer_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute answer-layer metrics without mixing in retrieval Hit@K metrics."""

    positive = [
        result for result in results if result.get("expected_answer_type") in POSITIVE_ANSWER_TYPES
    ]
    abstention = [
        result
        for result in results
        if result.get("expected_answer_type") in ABSTENTION_ANSWER_TYPES
    ]
    permission = [result for result in abstention if result.get("permission_case") is True]
    no_answer = [result for result in abstention if result.get("permission_case") is not True]
    marker_count = sum(len(result.get("citation_numbers") or []) for result in positive)
    valid_marker_count = sum(len(result.get("valid_citation_numbers") or []) for result in positive)
    cited_label_count = sum(len(result.get("cited_document_labels") or []) for result in positive)
    relevant_cited_label_count = sum(
        len(result.get("relevant_cited_document_labels") or []) for result in positive
    )
    latencies = [
        float(result["latency_ms"])
        for result in results
        if isinstance(result.get("latency_ms"), (int, float))
    ]

    by_answer_type: dict[str, dict[str, Any]] = {}
    for answer_type in sorted(SUPPORTED_ANSWER_TYPES):
        group = [result for result in results if result.get("expected_answer_type") == answer_type]
        if not group:
            continue
        by_answer_type[answer_type] = {
            "total": len(group),
            "passed": sum(result.get("case_passed") is True for result in group),
            "pass_rate": sum(result.get("case_passed") is True for result in group) / len(group),
        }

    def rate(group: list[dict[str, Any]], field: str) -> float:
        return sum(result.get(field) is True for result in group) / len(group) if group else 0.0

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "total_cases": len(results),
        "successful_invocations": sum(result.get("actual_status") == "ok" for result in results),
        "positive_answer_cases": len(positive),
        "abstention_cases": len(abstention),
        "permission_cases": len(permission),
        "answer_content_recall": (
            fmean(float(result.get("content_recall") or 0.0) for result in positive)
            if positive
            else 0.0
        ),
        "answer_content_pass_rate": rate(positive, "content_passed"),
        "citation_presence_rate": rate(positive, "citation_present"),
        "citation_valid_marker_rate": (valid_marker_count / marker_count if marker_count else 0.0),
        "citation_relevance_precision": (
            relevant_cited_label_count / cited_label_count if cited_label_count else 0.0
        ),
        "citation_grounded_answer_rate": rate(positive, "citation_passed"),
        "no_answer_abstention_accuracy": rate(no_answer, "abstention_passed"),
        "permission_abstention_accuracy": rate(permission, "abstention_passed"),
        "permission_canary_retrieval_leak_rate": rate(permission, "canary_retrieved"),
        "permission_response_leak_rate": rate(permission, "canary_response_leak"),
        "permission_zero_leak_rate": rate(permission, "zero_canary_leak"),
        "overall_case_pass_rate": rate(results, "case_passed"),
        "latency_ms": {
            "count": len(latencies),
            "mean": fmean(latencies) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "by_answer_type": by_answer_type,
    }


def quality_gate_reasons(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Return concrete threshold failures using only preregistered metric names."""

    reasons: list[str] = []
    for metric_name, raw_threshold in thresholds.items():
        raw_actual = metrics.get(metric_name)
        if raw_actual is None:
            reasons.append(f"quality metric {metric_name} is missing or invalid")
            continue
        try:
            threshold = float(raw_threshold)
            actual = float(raw_actual)
        except (TypeError, ValueError):
            reasons.append(f"quality metric {metric_name} is missing or invalid")
            continue
        if actual < threshold:
            reasons.append(f"{metric_name}={actual:.4f} is below frozen threshold {threshold:.4f}")
    return reasons


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def assess_answer_evidence_gate(
    results: list[dict[str, Any]],
    results_dir: Path,
    *,
    formal: bool,
) -> list[str]:
    """Fail closed when answer evidence is incomplete, stale, or cherry-picked."""

    reasons: list[str] = []
    manifest = read_json_object(results_dir / "rag_answer_manifest.json")
    metrics = read_json_object(results_dir / "rag_answer_metrics.json")
    if manifest is None:
        reasons.append("RAG answer manifest is missing or invalid")
        return reasons
    if metrics is None:
        reasons.append("RAG answer metrics are missing or invalid")
        return reasons
    if manifest.get("evaluator_version") != EVALUATOR_VERSION:
        reasons.append("RAG answer manifest evaluator version is invalid")
    if metrics.get("evaluator_version") != EVALUATOR_VERSION:
        reasons.append("RAG answer metrics evaluator version is invalid")
    selected_ids = [str(value) for value in manifest.get("selected_case_ids") or []]
    actual_ids = [str(result.get("case_id") or "") for result in results]
    if actual_ids != selected_ids:
        reasons.append("RAG answer result IDs do not match the manifest selection/order")
    if len(actual_ids) != len(set(actual_ids)):
        reasons.append("RAG answer results contain duplicate case IDs")
    if any(result.get("evaluator_version") != EVALUATOR_VERSION for result in results):
        reasons.append("one or more RAG answer results lack evaluator evidence")
    if any(result.get("actual_status") != "ok" for result in results):
        reasons.append("one or more RAG answer Agent invocations failed")
    if manifest.get("execution_error"):
        reasons.append(f"RAG answer execution error: {manifest['execution_error']}")

    permission_case_ids = manifest.get("permission_case_ids") or []
    if permission_case_ids:
        fixture = manifest.get("permission_fixture")
        if not isinstance(fixture, dict):
            reasons.append("RAG answer permission fixture evidence is missing")
        else:
            setup = fixture.get("setup")
            cleanup = fixture.get("cleanup")
            if not isinstance(setup, dict) or int(setup.get("inserted_chunk_count") or 0) != 3:
                reasons.append("RAG answer permission fixture did not insert three canaries")
            if not isinstance(setup, dict) or setup.get("exact_query_coverage") is not True:
                reasons.append("RAG answer permission fixture lacks exact query coverage")
            if not isinstance(cleanup, dict) or cleanup.get("clean") is not True:
                reasons.append("RAG answer permission fixture cleanup is incomplete")

    if formal:
        try:
            _, profile, _, _, all_cases = load_answer_profile(str(manifest.get("profile_id") or ""))
        except ValueError as exc:
            reasons.append(f"formal RAG answer profile validation failed: {exc}")
        else:
            expected_ids = [str(case["case_id"]) for case in all_cases]
            if actual_ids != expected_ids or len(actual_ids) != EXPECTED_FORMAL_CASE_COUNT:
                reasons.append("formal RAG answer run did not execute all 16 frozen cases")
            formal_gates = manifest.get("formal_execution_gates")
            if not isinstance(formal_gates, dict):
                reasons.append("formal RAG answer execution gates are missing")
            else:
                for gate_name in (
                    "source_tree_clean",
                    "dataset_frozen",
                    "gold_review_approved",
                    "full_dataset_selection",
                    "dataset_hash_matches_profile",
                ):
                    if formal_gates.get(gate_name) is not True:
                        reasons.append(f"formal RAG answer gate failed: {gate_name}")
            thresholds = (profile.get("evaluation") or {}).get("thresholds") or {}
            reasons.extend(quality_gate_reasons(metrics, thresholds))
    return list(dict.fromkeys(reasons))

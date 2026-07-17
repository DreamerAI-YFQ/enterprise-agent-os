"""Retrieval-only competition benchmark with reproducibility metadata.

This runner deliberately bypasses ontology query rewriting, the LLM reranker,
answer generation, and the Agent runtime. It evaluates only the real hybrid
retrieval path:

    query embedding -> permission-filtered vector/keyword candidates -> RRF

The embedding provider remains real because it is part of vector retrieval.
An empty retrieval is a strict failure for positive-gold cases, while cases
whose gold explicitly expects no evidence are scored separately. Exceptions
and permission violations always fail the run.

Usage:
    uv run python benchmarks/competition/runners/run_retrieval.py
    uv run python benchmarks/competition/runners/run_retrieval.py --limit 10
    uv run python benchmarks/competition/runners/run_retrieval.py \
        --case-id RAG-002 --case-id RAG-003 --require-clean-tree
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from eaos.infra.db.postgres import PgClient


PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPETITION_DIR = PROJECT_ROOT / "benchmarks" / "competition"
DEFAULT_DATASET = COMPETITION_DIR / "datasets" / "rag_queries_v1.yaml"
DEFAULT_RESULTS_DIR = COMPETITION_DIR / "results"
PROFILE_PATHS = {
    "core-v1": COMPETITION_DIR / "configs" / "retrieval_core_v1.yaml",
}

DEFAULT_ADMIN_EMAIL = os.environ.get("EAOS_ADMIN_EMAIL", "admin@acme.com")
DEFAULT_EMPLOYEE_EMAIL = os.environ.get("EAOS_EMPLOYEE_EMAIL", "employee@acme.com")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_GENERATED_UNTRACKED_PREFIXES = (
    "artifacts/competition-evidence/",
    "benchmarks/competition/results/",
)
_POSITIVE_ANSWER_TYPES = frozenset({"fact", "list", "summary"})
_NEGATIVE_ANSWER_TYPES = frozenset({"refusal", "empty"})
_ANSWER_TYPES = _POSITIVE_ANSWER_TYPES | _NEGATIVE_ANSWER_TYPES
_RELATIVE_TIME_RE = re.compile(
    r"本月|本周|最近(?:一个)?月|本季度|去年|明年|明天"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_path(path: Path) -> str:
    try:
        display = str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        display = str(path.resolve())
    return display.replace("\\", "/")


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def collect_source_state() -> dict[str, Any]:
    """Collect source identity without treating generated result folders as code."""
    tracked_status = _git_output("status", "--porcelain", "--untracked-files=no").splitlines()
    untracked_paths = sorted(
        path.replace("\\", "/")
        for path in _git_output("ls-files", "--others", "--exclude-standard").splitlines()
        if path
    )
    source_untracked = [
        path
        for path in untracked_paths
        if not path.startswith(_GENERATED_UNTRACKED_PREFIXES)
    ]
    tracked_diff = _git_output("diff", "--binary", "HEAD", "--")
    untracked_content = [
        {
            "path": path,
            "sha256": _sha256_file(PROJECT_ROOT / path),
        }
        for path in source_untracked
        if (PROJECT_ROOT / path).is_file()
    ]
    return {
        "git_sha": _git_output("rev-parse", "HEAD") or None,
        "git_branch": _git_output("rev-parse", "--abbrev-ref", "HEAD") or None,
        "tracked_dirty": bool(tracked_status),
        "tracked_status_sha256": _sha256_value(sorted(tracked_status)),
        "tracked_diff_sha256": _sha256_bytes(tracked_diff.encode("utf-8"))
        if tracked_status
        else None,
        "source_untracked_count": len(source_untracked),
        "source_untracked_paths_sha256": _sha256_value(source_untracked),
        "source_untracked_content_sha256": _sha256_value(untracked_content),
        "generated_untracked_count": len(untracked_paths) - len(source_untracked),
        "source_tree_clean": not tracked_status and not source_untracked,
    }


def _redacted_endpoint(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    parsed = urlsplit(str(raw_url))
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def load_cases(dataset_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
        raise ValueError("RAG dataset must contain a top-level 'queries' list")
    cases = payload["queries"]
    declared_total = (payload.get("metadata") or {}).get("total_cases")
    if declared_total is not None and declared_total != len(cases):
        raise ValueError(
            f"dataset declares {declared_total} cases but contains {len(cases)}"
        )
    seen_case_ids: set[str] = set()
    for case in cases:
        if (
            not isinstance(case, dict)
            or not case.get("case_id")
            or not str(case.get("query") or "").strip()
        ):
            raise ValueError("every RAG case requires non-empty case_id and query")
        case_id = str(case["case_id"])
        if case_id in seen_case_ids:
            raise ValueError(f"duplicate RAG case id: {case_id}")
        seen_case_ids.add(case_id)
        relevant = case.get("relevant_documents", [])
        if not isinstance(relevant, list):
            raise ValueError(f"{case_id}: relevant_documents must be a list")
        if any(not isinstance(item, str) or not item for item in relevant):
            raise ValueError(f"{case_id}: relevant document labels must be strings")
        if len(relevant) != len(set(relevant)):
            raise ValueError(f"{case_id}: relevant document labels must be unique")
        answer_type = str(case.get("expected_answer_type") or "")
        if answer_type not in _ANSWER_TYPES:
            raise ValueError(f"{case_id}: unsupported expected_answer_type")
        if answer_type in _POSITIVE_ANSWER_TYPES and not relevant:
            raise ValueError(f"{case_id}: positive answer requires retrieval gold")
        if answer_type in _NEGATIVE_ANSWER_TYPES and relevant:
            raise ValueError(f"{case_id}: negative answer cannot have positive gold")
        role = str(case.get("user_role") or "employee")
        if role not in {"admin", "employee"}:
            raise ValueError(f"{case_id}: unsupported evaluation role: {role}")
        tenant = case.get("tenant", "acme")
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"{case_id}: tenant must be a non-empty string")
    return payload, cases


def load_profile(profile_name: str) -> tuple[Path, dict[str, Any]]:
    """Load a registered retrieval profile and resolve its audited dataset."""
    profile_path = PROFILE_PATHS.get(profile_name)
    if profile_path is None:
        raise ValueError(f"unknown retrieval profile: {profile_name}")
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("profile_id") != profile_name:
        raise ValueError(f"invalid retrieval profile: {profile_name}")
    dataset_value = payload.get("dataset_path")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise ValueError(f"{profile_name}: dataset_path is required")
    dataset_path = (PROJECT_ROOT / dataset_value).resolve()
    try:
        dataset_path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(f"{profile_name}: dataset_path escapes project root") from exc
    return profile_path.resolve(), payload


def resolve_run_inputs(
    args: argparse.Namespace,
) -> tuple[Path, int, dict[str, Any] | None]:
    """Resolve dataset/top-k without allowing a profile to be silently overridden."""
    profile_info: dict[str, Any] | None = None
    profile_top_k: int | None = None
    if args.profile:
        if args.dataset:
            raise ValueError("--dataset cannot be combined with --profile")
        profile_path, profile_payload = load_profile(str(args.profile))
        dataset_path = (PROJECT_ROOT / str(profile_payload["dataset_path"])).resolve()
        evaluation = profile_payload.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        raw_top_k = evaluation.get("top_k")
        profile_top_k = int(raw_top_k) if raw_top_k is not None else None
        ledger_value = profile_payload.get("correction_ledger_path")
        ledger_info: dict[str, Any] | None = None
        if isinstance(ledger_value, str) and ledger_value.strip():
            ledger_path = (PROJECT_ROOT / ledger_value).resolve()
            try:
                ledger_path.relative_to(PROJECT_ROOT)
            except ValueError as exc:
                raise ValueError(
                    f"{args.profile}: correction ledger escapes project root"
                ) from exc
            if not ledger_path.is_file():
                raise ValueError(f"{args.profile}: correction ledger is missing")
            ledger_info = {
                "path": _project_path(ledger_path),
                "sha256": _sha256_file(ledger_path),
            }
        profile_info = {
            "profile_id": str(args.profile),
            "path": _project_path(profile_path),
            "sha256": _sha256_file(profile_path),
            "selection_rule": profile_payload.get("selection"),
            "permission_fixture": profile_payload.get("permission_fixture"),
            "correction_ledger": ledger_info,
        }
    else:
        dataset_path = (
            Path(args.dataset).resolve() if args.dataset else DEFAULT_DATASET.resolve()
        )

    top_k = args.top_k if args.top_k is not None else profile_top_k or 5
    if top_k <= 0:
        raise ValueError("--top-k must be positive")
    return dataset_path, top_k, profile_info


def deterministic_stratified_selection(
    cases: list[dict[str, Any]],
    *,
    seed: str,
    group_field: str,
    cases_per_group: int,
) -> list[dict[str, Any]]:
    """Select by SHA-256 rank within each first-seen group, independent of results."""
    if not seed or cases_per_group <= 0:
        raise ValueError("selection seed and positive cases_per_group are required")
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        group = str(case.get(group_field) or "")
        if not group:
            raise ValueError(f"{case.get('case_id')}: missing selection group")
        groups.setdefault(group, []).append(case)

    selected: list[dict[str, Any]] = []
    for group, group_cases in groups.items():
        if len(group_cases) < cases_per_group:
            raise ValueError(f"{group}: insufficient cases for stratified selection")
        ranked = sorted(
            group_cases,
            key=lambda case: (
                hashlib.sha256(
                    f"{seed}\\n{group}\\n{case['case_id']}".encode()
                ).hexdigest(),
                str(case["case_id"]),
            ),
        )
        selected.extend(ranked[:cases_per_group])
    return selected


def expected_corpus_labels_by_tenant(
    dataset_payload: dict[str, Any], cases: list[dict[str, Any]]
) -> tuple[dict[str, set[str]], str]:
    """Resolve the complete corpus label universe, not merely selected-case gold."""
    metadata = dataset_payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    declared = metadata.get("corpus_label_universe")
    if declared is None:
        expected: dict[str, set[str]] = {}
        for case in cases:
            tenant_slug = str(case.get("tenant") or "acme")
            expected.setdefault(tenant_slug, set()).update(
                str(item) for item in case.get("relevant_documents", [])
            )
        return expected, "positive_gold_union"

    default_tenant = str(metadata.get("tenant_default") or "acme")
    declared_by_tenant: dict[str, Any]
    if isinstance(declared, list):
        declared_by_tenant = {default_tenant: declared}
    elif isinstance(declared, dict):
        declared_by_tenant = {str(key): value for key, value in declared.items()}
    else:
        raise ValueError("metadata.corpus_label_universe must be a list or mapping")

    expected = {}
    for tenant_slug, labels in declared_by_tenant.items():
        if not isinstance(labels, list) or any(
            not isinstance(label, str) or not label.strip() for label in labels
        ):
            raise ValueError(f"{tenant_slug}: invalid corpus label universe")
        normalized = [str(label).strip() for label in labels]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{tenant_slug}: duplicate corpus label universe entry")
        expected[tenant_slug] = set(normalized)

    for case in cases:
        tenant_slug = str(case.get("tenant") or default_tenant)
        missing = set(str(item) for item in case.get("relevant_documents", [])) - expected.get(
            tenant_slug, set()
        )
        if missing:
            raise ValueError(
                f"{case['case_id']}: gold labels outside declared corpus universe: "
                f"{', '.join(sorted(missing))}"
            )
    return expected, "metadata.corpus_label_universe"


def dataset_freeze_status(dataset_payload: dict[str, Any]) -> dict[str, Any]:
    """Return a strict, machine-readable interpretation of ``frozen_date``."""
    raw_value = dataset_payload.get("frozen_date")
    if isinstance(raw_value, datetime):
        value = raw_value.date().isoformat()
    elif isinstance(raw_value, date):
        value = raw_value.isoformat()
    else:
        value = raw_value.strip() if isinstance(raw_value, str) else ""
    if not value or value.casefold() == "pending":
        return {
            "frozen_date": raw_value,
            "locked": False,
            "reason": "missing_or_pending",
        }
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return {
            "frozen_date": raw_value,
            "locked": False,
            "reason": "not_iso_8601_date",
        }
    return {
        "frozen_date": parsed.isoformat(),
        "locked": True,
        "reason": None,
    }


def dataset_review_status(
    dataset_payload: dict[str, Any], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Require explicit gold approval and a date anchor for relative queries."""
    metadata = dataset_payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    raw_review_status = metadata.get("gold_review_status")
    review_value = (
        raw_review_status.strip().casefold()
        if isinstance(raw_review_status, str)
        else ""
    )
    relative_time_case_ids = [
        str(case["case_id"])
        for case in cases
        if _RELATIVE_TIME_RE.search(str(case.get("query") or ""))
    ]
    raw_as_of_date = metadata.get("as_of_date")
    if isinstance(raw_as_of_date, datetime):
        as_of_value = raw_as_of_date.date().isoformat()
    elif isinstance(raw_as_of_date, date):
        as_of_value = raw_as_of_date.isoformat()
    else:
        as_of_value = (
            raw_as_of_date.strip() if isinstance(raw_as_of_date, str) else ""
        )
    parsed_as_of_date: str | None = None
    if as_of_value:
        try:
            parsed_as_of_date = date.fromisoformat(as_of_value).isoformat()
        except ValueError:
            parsed_as_of_date = None
    temporal_anchor_valid = not relative_time_case_ids or parsed_as_of_date is not None
    gold_review_approved = review_value == "approved"
    return {
        "gold_review_status": raw_review_status,
        "gold_review_approved": gold_review_approved,
        "relative_time_case_ids": relative_time_case_ids,
        "relative_time_case_count": len(relative_time_case_ids),
        "as_of_date": parsed_as_of_date or raw_as_of_date,
        "temporal_anchor_valid": temporal_anchor_valid,
        "ready_for_formal_freeze": gold_review_approved and temporal_anchor_valid,
    }


def select_cases(
    cases: list[dict[str, Any]],
    *,
    case_ids: list[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    if case_ids:
        requested = set(case_ids)
        selected = [case for case in cases if str(case["case_id"]) in requested]
        missing = sorted(requested - {str(case["case_id"]) for case in selected})
        if missing:
            raise ValueError(f"unknown case ids: {', '.join(missing)}")
    else:
        selected = list(cases)
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        selected = selected[:limit]
    if not selected:
        raise ValueError("no retrieval cases selected")
    return selected


def is_full_dataset_selection(
    all_cases: list[dict[str, Any]], selected_cases: list[dict[str, Any]]
) -> bool:
    """Require the original frozen order and every case exactly once."""
    return [str(case["case_id"]) for case in selected_cases] == [
        str(case["case_id"]) for case in all_cases
    ]


def _document_labels_by_chunk(result: dict[str, Any]) -> list[str]:
    chunks = result.get("retrieved_chunks")
    if isinstance(chunks, list):
        labels = [
            str(chunk["document_label"])
            for chunk in chunks
            if isinstance(chunk, dict) and chunk.get("document_label")
        ]
        if labels or not result.get("retrieved_document_ids"):
            return labels
    return [str(item) for item in result.get("retrieved_document_ids", [])]


def _dcg(retrieved_by_chunk: list[str], relevant: set[str], k: int) -> float:
    """Score first document occurrences at their original chunk ranks."""
    score = 0.0
    seen: set[str] = set()
    for rank, document_id in enumerate(retrieved_by_chunk[:k]):
        if document_id not in seen and document_id in relevant:
            score += 1.0 / math.log2(rank + 2)
        seen.add(document_id)
    return score


def compute_retrieval_metrics(
    results: list[dict[str, Any]],
    *,
    top_k: int,
) -> dict[str, Any]:
    """Compute document-level metrics; no-gold cases are reported, not invented."""
    judged = [result for result in results if result.get("relevant_documents")]
    hits: list[float] = []
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []

    for result in judged:
        relevant = set(str(item) for item in result["relevant_documents"])
        retrieved_by_chunk = _document_labels_by_chunk(result)[:top_k]
        retrieved_documents = _deduplicate(retrieved_by_chunk)
        relevant_ranks = [
            rank
            for rank, document_id in enumerate(retrieved_by_chunk, start=1)
            if document_id in relevant
        ]
        found = relevant.intersection(retrieved_documents)
        hits.append(1.0 if found else 0.0)
        recalls.append(len(found) / len(relevant))
        precisions.append(
            len(found) / len(retrieved_documents) if retrieved_documents else 0.0
        )
        reciprocal_ranks.append(1.0 / min(relevant_ranks) if relevant_ranks else 0.0)
        ideal = sum(
            1.0 / math.log2(rank + 2)
            for rank in range(min(len(relevant), top_k))
        )
        ndcgs.append(
            _dcg(retrieved_by_chunk, relevant, top_k) / ideal if ideal else 0.0
        )

    statuses = Counter(str(result.get("actual_status", "unknown")) for result in results)
    latency_values = [float(result.get("latency_ms", 0.0)) for result in results]
    strict_failures = (
        statuses.get("empty_retrieval", 0)
        + statuses.get("exception", 0)
        + statuses.get("permission_violation", 0)
    )
    expected_empty = [
        result
        for result in results
        if result.get("expected_answer_type") == "empty"
    ]
    expected_empty_successes = sum(
        result.get("actual_status") == "no_evidence_retrieval"
        and not result.get("retrieved_document_ids")
        for result in expected_empty
    )
    refusal_cases = sum(
        result.get("expected_answer_type") == "refusal" for result in results
    )
    permission_fixture_hit_count = sum(
        len(result.get("permission_fixture_hits", [])) for result in results
    )
    return {
        "metric_scope": "document relevance projected from the top-k chunks",
        "cutoff_unit": "chunk",
        "rank_unit": "original_chunk_rank",
        "relevance_judgment_unit": "document_label",
        "document_projection": (
            "first document occurrence keeps its original chunk rank; repeated chunks "
            "receive no additional relevance gain"
        ),
        "precision_denominator": "distinct documents projected from top-k chunks",
        "top_k": top_k,
        "total_cases": len(results),
        "judged_cases": len(judged),
        "no_gold_cases": len(results) - len(judged),
        "hit_at_k": sum(hits) / len(hits) if hits else 0.0,
        "recall_at_k": sum(recalls) / len(recalls) if recalls else 0.0,
        "precision_at_k": sum(precisions) / len(precisions) if precisions else 0.0,
        "ndcg_at_k": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
        "mrr_at_k": sum(reciprocal_ranks) / len(reciprocal_ranks)
        if reciprocal_ranks
        else 0.0,
        "empty_retrieval_count": statuses.get("empty_retrieval", 0),
        "no_evidence_retrieval_count": statuses.get("no_evidence_retrieval", 0),
        "exception_count": statuses.get("exception", 0),
        "permission_violation_count": statuses.get("permission_violation", 0),
        "permission_fixture_hit_count": permission_fixture_hit_count,
        "permission_fixture_zero_hits": permission_fixture_hit_count == 0,
        "strict_failure_count": strict_failures,
        "status_counts": dict(sorted(statuses.items())),
        "mean_latency_ms": sum(latency_values) / len(latency_values)
        if latency_values
        else 0.0,
        "empty_retrieval_policy": "strict_failure_for_positive_gold",
        "no_gold_policy": "excluded from positive relevance metrics",
        "expected_empty_cases": len(expected_empty),
        "expected_empty_retrieval_accuracy": (
            expected_empty_successes / len(expected_empty) if expected_empty else None
        ),
        "refusal_cases": refusal_cases,
        "refusal_policy": "not measurable without answer generation",
        "citation_metrics_available": False,
        "citation_policy": "retrieval-only output is not an answer citation",
    }


class RecordingEmbedder:
    """Record query-vector fingerprints without persisting raw embeddings."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._fingerprints: dict[str, str] = {}

    @property
    def dimension(self) -> int:
        return int(self._delegate.dimension)

    @property
    def model_name(self) -> str:
        return str(self._delegate.model_name)

    async def embed(self, text: str) -> list[float]:
        vector = cast("list[float]", await self._delegate.embed(text))
        self._fingerprints[text] = _sha256_value(vector)
        return vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = cast("list[list[float]]", await self._delegate.embed_batch(texts))
        for text, vector in zip(texts, vectors, strict=True):
            self._fingerprints[text] = _sha256_value(vector)
        return vectors

    def fingerprint_for(self, text: str) -> str | None:
        return self._fingerprints.get(text)


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def validate_corpus_labels(
    document_labels: dict[str, str | None], expected_labels: set[str]
) -> dict[str, Any]:
    """Validate benchmark labels while retaining real non-benchmark distractors.

    Only explicit ``metadata.doc_id`` values are labels. Unlabelled base seed
    documents and explicitly non-``KB-*`` documents remain in the corpus and
    are fingerprinted, but they must not make an exact benchmark corpus fail.
    Extra or duplicate ``KB-*`` labels remain hard failures.
    """
    normalized_labels = [
        label.strip()
        for label in document_labels.values()
        if isinstance(label, str) and label.strip()
    ]
    labels = normalized_labels
    label_counts = Counter(labels)
    actual_labels = set(label_counts)
    missing = sorted(expected_labels - actual_labels)
    benchmark_labels = {
        label for label in actual_labels if label.upper().startswith("KB-")
    }
    unexpected = sorted(benchmark_labels - expected_labels)
    duplicates = sorted(
        label
        for label, count in label_counts.items()
        if label.upper().startswith("KB-") and count > 1
    )
    distractor_labels = sorted(actual_labels - benchmark_labels)
    unlabeled_document_count = len(document_labels) - len(normalized_labels)
    return {
        "expected_label_count": len(expected_labels),
        "actual_label_count": len(actual_labels),
        "actual_benchmark_label_count": len(benchmark_labels),
        "missing_labels": missing,
        "unexpected_benchmark_labels": unexpected,
        "duplicate_benchmark_labels": duplicates,
        # Compatibility aliases for earlier pilot consumers.
        "unexpected_labels": unexpected,
        "duplicate_labels": duplicates,
        "unlabeled_document_count": unlabeled_document_count,
        "non_benchmark_distractor_labels": distractor_labels,
        "non_benchmark_distractor_document_count": (
            unlabeled_document_count
            + sum(label_counts[label] for label in distractor_labels)
        ),
        "validation_policy": (
            "every expected KB-* label exactly once; no unexpected KB-* labels; "
            "unlabelled and non-KB distractors allowed and recorded"
        ),
        "exact_match": not missing and not unexpected and not duplicates,
    }


async def _resolve_tenant(db: PgClient, tenant_slug: str) -> tuple[UUID, str]:
    rows = await db.fetch(
        "SELECT id, slug FROM iam.tenants "
        "WHERE status = 'active' AND (slug = :p0 OR slug LIKE :p1) "
        "ORDER BY CASE WHEN slug = :p0 THEN 0 ELSE 1 END, slug",
        tenant_slug,
        f"{tenant_slug}-%",
    )
    if not rows:
        raise LookupError(f"active tenant not found: {tenant_slug}")
    exact = [row for row in rows if row["slug"] == tenant_slug]
    if exact:
        row = exact[0]
    elif len(rows) == 1:
        row = rows[0]
    else:
        raise LookupError(f"tenant alias is ambiguous: {tenant_slug}")
    return cast("UUID", row["id"]), str(row["slug"])


async def _resolve_user(
    db: PgClient,
    *,
    tenant_id: UUID,
    email: str,
) -> tuple[UUID, list[UUID]]:
    row = await db.fetch_one(
        "SELECT id FROM iam.users "
        "WHERE tenant_id = :p0 AND email = :p1 AND status = 'active'",
        tenant_id,
        email,
    )
    if row is None:
        raise LookupError(f"active evaluation user not found: {email}")
    user_id = row["id"]
    department_rows = await db.fetch(
        "SELECT m.department_id FROM iam.memberships m "
        "JOIN iam.departments d ON d.id = m.department_id "
        "WHERE m.user_id = :p0 AND d.tenant_id = :p1 "
        "ORDER BY m.department_id",
        user_id,
        tenant_id,
    )
    return user_id, [item["department_id"] for item in department_rows]


async def _load_corpus_snapshot(db: PgClient, tenant_id: UUID) -> dict[str, Any]:
    rows = await db.fetch(
        "SELECT c.id, c.document_id, c.chunk_index, c.content, c.scope, c.owner_id, "
        "c.embedding IS NOT NULL AS has_embedding, d.title, d.content_hash, "
        "d.version, d.metadata AS document_metadata "
        "FROM knowledge.chunks c "
        "JOIN knowledge.documents d "
        "ON d.id = c.document_id AND d.tenant_id = c.tenant_id "
        "WHERE c.tenant_id = :p0 "
        "ORDER BY c.document_id, c.chunk_index, c.id",
        tenant_id,
    )

    document_labels: dict[str, str | None] = {}
    document_ids: set[str] = set()
    document_scope: dict[str, str] = {}
    fingerprint_rows: list[dict[str, Any]] = []
    for row in rows:
        document_id = str(row["document_id"])
        document_ids.add(document_id)
        metadata = _metadata_dict(row.get("document_metadata"))
        explicit_label = metadata.get("doc_id")
        normalized_label = str(explicit_label).strip() if explicit_label else ""
        document_labels[document_id] = normalized_label or None
        document_scope.setdefault(document_id, str(row.get("scope") or "enterprise"))
        fingerprint_rows.append(
            {
                "chunk_id": str(row["id"]),
                "document_id": document_id,
                "chunk_index": int(row["chunk_index"]),
                "content_sha256": _sha256_bytes(str(row["content"]).encode("utf-8")),
                "document_content_hash": str(row.get("content_hash") or ""),
                "document_version": int(row.get("version") or 0),
                "scope": str(row.get("scope") or "enterprise"),
                "owner_id": str(row["owner_id"]) if row.get("owner_id") else None,
                "has_embedding": bool(row.get("has_embedding")),
            }
        )

    return {
        "document_labels": document_labels,
        "summary": {
            "document_count": len(document_ids),
            "chunk_count": len(rows),
            "embedded_chunk_count": sum(bool(row.get("has_embedding")) for row in rows),
            "document_scope_counts": dict(sorted(Counter(document_scope.values()).items())),
            "chunk_scope_counts": dict(
                sorted(Counter(item["scope"] for item in fingerprint_rows).items())
            ),
            "corpus_sha256": _sha256_value(fingerprint_rows),
            "corpus_embedding_model_persisted": False,
        },
    }


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def visibility_violations(
    chunks: list[Any],
    *,
    tenant_id: UUID,
    user_id: UUID,
    department_ids: list[UUID],
) -> list[dict[str, str]]:
    """Independently assert that every returned chunk is visible to the user."""
    violations: list[dict[str, str]] = []
    allowed_departments = {str(item) for item in department_ids}
    for chunk in chunks:
        reason: str | None = None
        if chunk.tenant_id != tenant_id:
            reason = "cross_tenant"
        else:
            scope = str(chunk.metadata.get("scope") or "enterprise")
            owner_id = str(chunk.metadata.get("owner_id") or "")
            if scope == "enterprise":
                pass
            elif scope == "personal" and owner_id != str(user_id):
                reason = "foreign_personal_scope"
            elif scope == "department" and owner_id not in allowed_departments:
                reason = "foreign_department_scope"
            elif scope not in {"enterprise", "personal", "department"}:
                reason = "unknown_scope"
        if reason is not None:
            violations.append({"chunk_id": str(chunk.id), "reason": reason})
    return violations


def build_permission_fixture_spec(
    *,
    run_id: str,
    tenant_id: UUID,
    permission_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic, run-scoped identities and high-similarity contents."""
    if not permission_cases:
        raise ValueError("permission fixture requires at least one permission case")
    run_token = _sha256_bytes(run_id.encode("utf-8"))[:12]
    namespace = f"eaos-retrieval-permission-fixture:{run_id}"
    foreign_tenant_id = uuid5(NAMESPACE_URL, f"{namespace}:tenant")
    foreign_department_id = uuid5(NAMESPACE_URL, f"{namespace}:department")
    foreign_person_id = uuid5(NAMESPACE_URL, f"{namespace}:person")
    query_lines = [
        f"{case['case_id']}: {str(case['query']).strip()}" for case in permission_cases
    ]
    query_block = "\n".join(query_lines)
    definitions = [
        (
            "cross_tenant",
            foreign_tenant_id,
            "enterprise",
            None,
            "Globex cross-tenant evidence that the Acme evaluator must never retrieve.",
        ),
        (
            "foreign_department",
            tenant_id,
            "department",
            foreign_department_id,
            "A foreign-department confidential document that the evaluator must never retrieve.",
        ),
        (
            "foreign_personal",
            tenant_id,
            "personal",
            foreign_person_id,
            "Another user's private document that the evaluator must never retrieve.",
        ),
    ]
    records: list[dict[str, Any]] = []
    for kind, record_tenant_id, scope, owner_id, description in definitions:
        document_id = uuid5(NAMESPACE_URL, f"{namespace}:{kind}:document")
        chunk_id = uuid5(NAMESPACE_URL, f"{namespace}:{kind}:chunk")
        content = (
            f"EAOS RAG permission canary ({kind}).\n"
            f"{description}\n"
            "The following exact benchmark queries are deliberately repeated so this "
            "document would be a top lexical and semantic candidate without permission "
            f"pre-filtering:\n{query_block}\n"
        )
        records.append(
            {
                "kind": kind,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "tenant_id": record_tenant_id,
                "scope": scope,
                "owner_id": owner_id,
                "content": content,
                "content_sha256": _sha256_bytes(content.encode("utf-8")),
                "source_uri": f"benchmark://permission-canary/{run_id}/{kind}",
                "title": f"RAG permission canary {kind} {run_token}",
                "doc_label": f"CANARY-{run_token}-{kind.upper()}",
            }
        )
    return {
        "schema_version": "permission-fixture-v1",
        "run_id": run_id,
        "run_token": run_token,
        "primary_tenant_id": tenant_id,
        "foreign_tenant": {
            "id": foreign_tenant_id,
            "slug": f"globex-rag-{run_token}",
            "name": f"Globex RAG Canary {run_token}",
        },
        "foreign_department": {
            "id": foreign_department_id,
            "name": f"RAG Canary Department {run_token}",
        },
        "foreign_person": {
            "id": foreign_person_id,
            "email": f"rag-canary-{run_token}@invalid.local",
            "name": f"RAG Canary Person {run_token}",
        },
        "permission_case_ids": [str(case["case_id"]) for case in permission_cases],
        "permission_queries_sha256": _sha256_value(query_lines),
        "records": records,
    }


async def setup_permission_fixture(
    db: PgClient,
    embedder: RecordingEmbedder,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Insert three real embedded canaries using only deterministic fixture IDs."""
    records = cast("list[dict[str, Any]]", spec["records"])
    contents = [str(record["content"]) for record in records]
    vectors = await embedder.embed_batch(contents)
    foreign_tenant = cast("dict[str, Any]", spec["foreign_tenant"])
    foreign_department = cast("dict[str, Any]", spec["foreign_department"])
    foreign_person = cast("dict[str, Any]", spec["foreign_person"])
    primary_tenant_id = cast("UUID", spec["primary_tenant_id"])

    await db.execute(
        "INSERT INTO iam.tenants (id, name, slug, status, settings) "
        "VALUES (:p0, :p1, :p2, 'active', CAST(:p3 AS jsonb))",
        foreign_tenant["id"],
        foreign_tenant["name"],
        foreign_tenant["slug"],
        _canonical_json(
            {"competition_fixture": True, "competition_run_id": spec["run_id"]}
        ),
    )
    await db.execute(
        "INSERT INTO iam.departments (id, tenant_id, name, parent_id) "
        "VALUES (:p0, :p1, :p2, NULL)",
        foreign_department["id"],
        primary_tenant_id,
        foreign_department["name"],
    )
    await db.execute(
        "INSERT INTO iam.users (id, tenant_id, email, name, role, status) "
        "VALUES (:p0, :p1, :p2, :p3, 'employee', 'active')",
        foreign_person["id"],
        primary_tenant_id,
        foreign_person["email"],
        foreign_person["name"],
    )

    document_params: list[tuple[Any, ...]] = []
    chunk_params: list[tuple[Any, ...]] = []
    receipt_records: list[dict[str, Any]] = []
    for record, vector in zip(records, vectors, strict=True):
        metadata = {
            "competition_permission_canary": True,
            "competition_run_id": spec["run_id"],
            "canary_kind": record["kind"],
            "doc_id": record["doc_label"],
        }
        document_params.append(
            (
                record["document_id"],
                record["tenant_id"],
                "benchmark",
                record["source_uri"],
                record["title"],
                record["content_sha256"],
                1,
                _canonical_json(metadata),
                record["scope"],
                record["owner_id"],
            )
        )
        chunk_params.append(
            (
                record["chunk_id"],
                record["document_id"],
                record["tenant_id"],
                0,
                record["content"],
                max(1, len(str(record["content"])) // 4),
                _canonical_json(vector),
                _canonical_json(metadata),
                record["scope"],
                record["owner_id"],
            )
        )
        receipt_records.append(
            {
                "kind": record["kind"],
                "document_id": str(record["document_id"]),
                "chunk_id": str(record["chunk_id"]),
                "tenant_id": str(record["tenant_id"]),
                "scope": record["scope"],
                "owner_id": str(record["owner_id"])
                if record["owner_id"] is not None
                else None,
                "content_sha256": record["content_sha256"],
                "embedding_sha256": _sha256_value(vector),
            }
        )

    await db.execute_many(
        "INSERT INTO knowledge.documents "
        "(id, tenant_id, source_type, source_uri, title, content_hash, version, "
        "metadata, scope, owner_id, status) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, CAST(:p7 AS jsonb), "
        ":p8, :p9, 'indexed')",
        document_params,
    )
    await db.execute_many(
        "INSERT INTO knowledge.chunks "
        "(id, document_id, tenant_id, chunk_index, content, token_count, embedding, "
        "metadata, scope, owner_id) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, CAST(:p6 AS vector), "
        "CAST(:p7 AS jsonb), :p8, :p9)",
        chunk_params,
    )
    chunk_ids = [record["chunk_id"] for record in records]
    inserted_chunk_count = await db.fetch_val(
        "SELECT count(*) FROM knowledge.chunks WHERE id IN (:p0, :p1, :p2)",
        *chunk_ids,
    )
    if int(inserted_chunk_count or 0) != len(records):
        raise RuntimeError("permission fixture setup verification failed")
    return {
        "schema_version": spec["schema_version"],
        "run_id": spec["run_id"],
        "permission_case_ids": spec["permission_case_ids"],
        "permission_queries_sha256": spec["permission_queries_sha256"],
        "embedding_model": embedder.model_name,
        "embedding_dimension": embedder.dimension,
        "exact_query_coverage": True,
        "inserted_chunk_count": int(inserted_chunk_count),
        "records": receipt_records,
    }


async def cleanup_permission_fixture(
    db: PgClient, spec: dict[str, Any]
) -> dict[str, Any]:
    """Delete only rows carrying this run's deterministic identities and markers."""
    records = cast("list[dict[str, Any]]", spec["records"])
    foreign_tenant = cast("dict[str, Any]", spec["foreign_tenant"])
    foreign_department = cast("dict[str, Any]", spec["foreign_department"])
    foreign_person = cast("dict[str, Any]", spec["foreign_person"])
    chunk_ids = [record["chunk_id"] for record in records]
    document_ids = [record["document_id"] for record in records]
    deleted: dict[str, int] = {}
    errors: list[str] = []

    operations: list[tuple[str, str, tuple[Any, ...]]] = [
        (
            "chunks",
            "DELETE FROM knowledge.chunks WHERE id IN (:p0, :p1, :p2) "
            "AND metadata->>'competition_run_id' = :p3 RETURNING id",
            (*chunk_ids, spec["run_id"]),
        ),
        (
            "documents",
            "DELETE FROM knowledge.documents WHERE id IN (:p0, :p1, :p2) "
            "AND metadata->>'competition_run_id' = :p3 RETURNING id",
            (*document_ids, spec["run_id"]),
        ),
        (
            "foreign_person",
            "DELETE FROM iam.users WHERE id = :p0 AND email = :p1 RETURNING id",
            (foreign_person["id"], foreign_person["email"]),
        ),
        (
            "foreign_department",
            "DELETE FROM iam.departments WHERE id = :p0 AND name = :p1 RETURNING id",
            (foreign_department["id"], foreign_department["name"]),
        ),
        (
            "foreign_tenant",
            "DELETE FROM iam.tenants WHERE id = :p0 AND slug = :p1 RETURNING id",
            (foreign_tenant["id"], foreign_tenant["slug"]),
        ),
    ]
    for name, sql, params in operations:
        try:
            deleted[name] = len(await db.fetch(sql, *params))
        except Exception as exc:  # noqa: BLE001 - cleanup must attempt every row type
            deleted[name] = 0
            errors.append(f"{name}:{type(exc).__name__}")

    residual: dict[str, int | None] = {}
    checks: list[tuple[str, str, tuple[Any, ...]]] = [
        (
            "chunks",
            "SELECT count(*) FROM knowledge.chunks WHERE id IN (:p0, :p1, :p2)",
            tuple(chunk_ids),
        ),
        (
            "documents",
            "SELECT count(*) FROM knowledge.documents WHERE id IN (:p0, :p1, :p2)",
            tuple(document_ids),
        ),
        (
            "foreign_person",
            "SELECT count(*) FROM iam.users WHERE id = :p0",
            (foreign_person["id"],),
        ),
        (
            "foreign_department",
            "SELECT count(*) FROM iam.departments WHERE id = :p0",
            (foreign_department["id"],),
        ),
        (
            "foreign_tenant",
            "SELECT count(*) FROM iam.tenants WHERE id = :p0",
            (foreign_tenant["id"],),
        ),
    ]
    for name, sql, params in checks:
        try:
            residual[name] = int(await db.fetch_val(sql, *params) or 0)
        except Exception as exc:  # noqa: BLE001 - preserve cleanup evidence
            residual[name] = None
            errors.append(f"verify_{name}:{type(exc).__name__}")
    clean = not errors and all(value == 0 for value in residual.values())
    return {
        "attempted": True,
        "deleted": deleted,
        "residual": residual,
        "errors": errors,
        "clean": clean,
    }


async def run_benchmark(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    from eaos.core.config import AppConfig
    from eaos.infra.db.postgres import PgClient
    from eaos.infra.vector.embedder import OpenAIEmbedder
    from eaos.infra.vector.pgvector_store import PgVectorStore
    from eaos.knowledge.rag.retriever import RRF_K, HybridRetriever

    dataset_path, resolved_top_k, profile_info = resolve_run_inputs(args)
    args.top_k = resolved_top_k
    dataset_payload, all_cases = load_cases(dataset_path)
    selected_cases = select_cases(all_cases, case_ids=args.case_id, limit=args.limit)
    formal_requested = bool(getattr(args, "formal", False))
    require_clean_tree = formal_requested or args.require_clean_tree
    require_frozen_dataset = formal_requested or args.require_frozen_dataset
    require_gold_review = formal_requested or args.require_gold_review
    require_full_dataset = formal_requested or args.require_full_dataset
    require_exact_corpus = formal_requested or args.require_exact_corpus
    raw_permission_config = (
        profile_info.get("permission_fixture")
        if isinstance(profile_info, dict)
        else None
    )
    profile_permission_config: dict[str, Any] = (
        raw_permission_config if isinstance(raw_permission_config, dict) else {}
    )
    permission_fixture_required = bool(
        getattr(args, "with_permission_fixture", False)
    ) or (
        formal_requested
        and bool(profile_permission_config.get("required_for_formal", False))
    )
    freeze_status = dataset_freeze_status(dataset_payload)
    review_status = dataset_review_status(dataset_payload, all_cases)
    full_dataset_selection = is_full_dataset_selection(all_cases, selected_cases)
    if require_frozen_dataset and not freeze_status["locked"]:
        raise RuntimeError("dataset frozen_date is not locked; formal run aborted")
    if require_gold_review and not review_status["ready_for_formal_freeze"]:
        raise RuntimeError(
            "dataset gold review or relative-time anchor is not locked; "
            "formal run aborted"
        )
    if require_full_dataset and not full_dataset_selection:
        raise RuntimeError("partial dataset selection is not allowed for a formal run")
    expected_labels_by_tenant, corpus_label_source = expected_corpus_labels_by_tenant(
        dataset_payload, all_cases
    )

    run_id = args.run_id or f"retrieval-{_utc_now().strftime('%Y%m%d-%H%M%S')}"
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("--run-id must contain only letters, digits, '.', '_' and '-'")
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else (DEFAULT_RESULTS_DIR / run_id).resolve()
    )
    source_state = collect_source_state()
    if require_clean_tree and not source_state["source_tree_clean"]:
        raise RuntimeError("source tree is not clean; formal run aborted")

    config = AppConfig.load_config(env_file=args.env_file)
    if not config.embedding.api_key:
        raise RuntimeError("retrieval-only evaluation requires a real embedding API key")

    db = PgClient(config.db)
    recording_embedder = RecordingEmbedder(OpenAIEmbedder(config.embedding))
    retriever = HybridRetriever(PgVectorStore(db), recording_embedder, db)

    role_emails = {
        "admin": args.admin_email,
        "employee": args.employee_email,
    }
    tenant_cache: dict[str, tuple[UUID, str]] = {}
    user_cache: dict[tuple[str, str], tuple[UUID, list[UUID]]] = {}
    corpus_cache: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    results_path = output_dir / "retrieval_results.jsonl"
    fixture_receipt_path = output_dir / "permission_fixture_receipt.json"
    permission_fixture_spec: dict[str, Any] | None = None
    permission_fixture_setup: dict[str, Any] | None = None
    permission_fixture_setup_error: str | None = None
    permission_fixture_cleanup: dict[str, Any] | None = None
    output_created = False
    started_at = _utc_now()

    try:
        if permission_fixture_required:
            output_dir.mkdir(parents=True, exist_ok=False)
            output_created = True
            fixture_category = str(
                profile_permission_config.get("case_category") or "权限隔离查询"
            )
            permission_cases = [
                case
                for case in selected_cases
                if str(case.get("category") or "") == fixture_category
            ]
            minimum_cases = int(profile_permission_config.get("minimum_cases") or 1)
            if len(permission_cases) < minimum_cases:
                raise RuntimeError(
                    "permission fixture does not cover the required case count"
                )
            primary_tenant_slug = str(
                (dataset_payload.get("metadata") or {}).get("tenant_default")
                or permission_cases[0].get("tenant")
                or "acme"
            )
            tenant_cache[primary_tenant_slug] = await _resolve_tenant(
                db, primary_tenant_slug
            )
            primary_tenant_id, _ = tenant_cache[primary_tenant_slug]
            permission_fixture_spec = build_permission_fixture_spec(
                run_id=run_id,
                tenant_id=primary_tenant_id,
                permission_cases=permission_cases,
            )
            try:
                permission_fixture_setup = await setup_permission_fixture(
                    db, recording_embedder, permission_fixture_spec
                )
            except Exception as exc:
                permission_fixture_setup_error = type(exc).__name__
                raise

        if require_exact_corpus:
            # Formal corpus validation is a preflight gate. It must abort before
            # any case is evaluated, rather than being caught as 150 case-level
            # exceptions. Every dataset tenant is checked, including one that a
            # filtered pilot selection might otherwise never touch.
            for tenant_slug, expected_labels in sorted(
                expected_labels_by_tenant.items()
            ):
                tenant_cache[tenant_slug] = await _resolve_tenant(db, tenant_slug)
                tenant_id, _ = tenant_cache[tenant_slug]
                snapshot = await _load_corpus_snapshot(db, tenant_id)
                label_validation = validate_corpus_labels(
                    snapshot["document_labels"], expected_labels
                )
                snapshot["summary"]["benchmark_label_validation"] = label_validation
                corpus_cache[tenant_slug] = snapshot
                if not label_validation["exact_match"]:
                    raise RuntimeError(
                        "corpus benchmark labels do not exactly match the dataset"
                    )

        if not output_created:
            output_dir.mkdir(parents=True, exist_ok=False)
            output_created = True
        permission_canary_by_chunk = {
            str(record["chunk_id"]): str(record["kind"])
            for record in (
                cast("list[dict[str, Any]]", permission_fixture_spec["records"])
                if permission_fixture_spec is not None
                else []
            )
        }
        with results_path.open("w", encoding="utf-8", newline="\n") as stream:
            for index, case in enumerate(selected_cases, start=1):
                query = str(case["query"])
                tenant_slug = str(case.get("tenant") or "acme")
                role = str(case.get("user_role") or "employee")
                result: dict[str, Any] = {
                    "case_id": str(case["case_id"]),
                    "query": query,
                    "tenant": tenant_slug,
                    "user_role": role,
                    "category": str(case.get("category") or "unknown"),
                    "expected_answer_type": str(case["expected_answer_type"]),
                    "relevant_documents": [
                        str(item) for item in case.get("relevant_documents", [])
                    ],
                    "gold_judgment_available": bool(case.get("relevant_documents")),
                }
                case_started = time.perf_counter()
                try:
                    if role not in role_emails:
                        raise ValueError(f"unsupported evaluation role: {role}")
                    if tenant_slug not in tenant_cache:
                        tenant_cache[tenant_slug] = await _resolve_tenant(db, tenant_slug)
                    tenant_id, resolved_tenant_slug = tenant_cache[tenant_slug]
                    result["resolved_tenant"] = resolved_tenant_slug
                    user_key = (tenant_slug, role)
                    if user_key not in user_cache:
                        user_cache[user_key] = await _resolve_user(
                            db,
                            tenant_id=tenant_id,
                            email=role_emails[role],
                        )
                    user_id, department_ids = user_cache[user_key]
                    if tenant_slug not in corpus_cache:
                        corpus_cache[tenant_slug] = await _load_corpus_snapshot(db, tenant_id)
                        label_validation = validate_corpus_labels(
                            corpus_cache[tenant_slug]["document_labels"],
                            expected_labels_by_tenant.get(tenant_slug, set()),
                        )
                        corpus_cache[tenant_slug]["summary"][
                            "benchmark_label_validation"
                        ] = label_validation
                    chunks = await retriever.retrieve(
                        query,
                        tenant_id,
                        top_k=args.top_k,
                        user_id=user_id,
                        department_ids=department_ids,
                    )
                    labels = corpus_cache[tenant_slug]["document_labels"]
                    retrieved_chunks = [
                        {
                            "rank": rank,
                            "chunk_id": str(chunk.id),
                            "document_id": str(chunk.document_id),
                            "tenant_id": str(chunk.tenant_id),
                            "document_label": labels.get(str(chunk.document_id))
                            or f"NONBENCHMARK:{chunk.document_id}",
                            "chunk_index": int(chunk.chunk_index),
                            "score": float(chunk.score),
                            "scope": str(chunk.metadata.get("scope", "enterprise")),
                            "owner_id": (
                                str(chunk.metadata["owner_id"])
                                if chunk.metadata.get("owner_id")
                                else None
                            ),
                            "content_sha256": _sha256_bytes(
                                chunk.content.encode("utf-8")
                            ),
                        }
                        for rank, chunk in enumerate(chunks, start=1)
                    ]
                    result["retrieved_chunks"] = retrieved_chunks
                    permission_violations = visibility_violations(
                        chunks,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        department_ids=department_ids,
                    )
                    permission_fixture_hits = [
                        {
                            "chunk_id": item["chunk_id"],
                            "kind": permission_canary_by_chunk[item["chunk_id"]],
                        }
                        for item in retrieved_chunks
                        if item["chunk_id"] in permission_canary_by_chunk
                    ]
                    for hit in permission_fixture_hits:
                        permission_violations.append(
                            {
                                "chunk_id": str(hit["chunk_id"]),
                                "reason": f"permission_canary:{hit['kind']}",
                            }
                        )
                    result["permission_fixture_hits"] = permission_fixture_hits
                    result["permission_violations"] = permission_violations
                    result["retrieved_document_ids"] = _deduplicate(
                        [str(item["document_label"]) for item in retrieved_chunks]
                    )
                    result["query_embedding_sha256"] = recording_embedder.fingerprint_for(
                        query
                    )
                    if permission_violations:
                        result["actual_status"] = "permission_violation"
                    elif chunks:
                        result["actual_status"] = "ok"
                    elif result["gold_judgment_available"]:
                        result["actual_status"] = "empty_retrieval"
                    else:
                        result["actual_status"] = "no_evidence_retrieval"
                except Exception as exc:  # noqa: BLE001 - record every case and continue
                    result["actual_status"] = "exception"
                    result["error_type"] = type(exc).__name__
                    result["retrieved_chunks"] = []
                    result["retrieved_document_ids"] = []
                    result["permission_violations"] = []
                    result["permission_fixture_hits"] = []
                    result["query_embedding_sha256"] = recording_embedder.fingerprint_for(
                        query
                    )
                result["latency_ms"] = round(
                    (time.perf_counter() - case_started) * 1000,
                    3,
                )
                results.append(result)
                stream.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
                stream.flush()
                print(
                    f"[{index:03d}/{len(selected_cases):03d}] "
                    f"{result['case_id']} {result['actual_status']} "
                    f"{result['latency_ms']:.1f}ms"
                )
    finally:
        if permission_fixture_spec is not None:
            try:
                permission_fixture_cleanup = await cleanup_permission_fixture(
                    db, permission_fixture_spec
                )
            except Exception as exc:  # noqa: BLE001 - preserve the primary run error
                permission_fixture_cleanup = {
                    "attempted": True,
                    "deleted": {},
                    "residual": {},
                    "errors": [f"cleanup:{type(exc).__name__}"],
                    "clean": False,
                }
            fixture_hit_count = sum(
                len(result.get("permission_fixture_hits", [])) for result in results
            )
            fixture_receipt = {
                "schema_version": "permission-fixture-receipt-v1",
                "run_id": run_id,
                "required": permission_fixture_required,
                "setup": permission_fixture_setup,
                "setup_error_type": permission_fixture_setup_error,
                "retrieval": {
                    "evaluated_case_count": len(results),
                    "permission_case_ids": permission_fixture_spec[
                        "permission_case_ids"
                    ],
                    "canary_hit_count": fixture_hit_count,
                    "zero_canary_hits": fixture_hit_count == 0,
                },
                "cleanup": permission_fixture_cleanup,
            }
            if output_created:
                _write_json(fixture_receipt_path, fixture_receipt)
        await db.close()

    if permission_fixture_required and (
        permission_fixture_setup is None
        or permission_fixture_cleanup is None
        or permission_fixture_cleanup.get("clean") is not True
    ):
        raise RuntimeError("permission fixture setup or cleanup did not close cleanly")

    finished_at = _utc_now()
    metrics = compute_retrieval_metrics(results, top_k=args.top_k)
    metrics_path = output_dir / "retrieval_metrics.json"
    _write_json(metrics_path, metrics)

    selected_input = [
        {
            "case_id": case["case_id"],
            "query": case["query"],
            "tenant": case.get("tenant", "acme"),
            "user_role": case.get("user_role", "employee"),
            "expected_answer_type": case.get("expected_answer_type"),
            "relevant_documents": case.get("relevant_documents", []),
        }
        for case in selected_cases
    ]
    corpus_summaries = {
        slug: snapshot["summary"] for slug, snapshot in sorted(corpus_cache.items())
    }
    exact_corpus_observed = bool(corpus_summaries) and all(
        summary.get("benchmark_label_validation", {}).get("exact_match") is True
        for summary in corpus_summaries.values()
    )
    permission_fixture_setup_verified = (
        permission_fixture_setup is not None
        and permission_fixture_setup.get("inserted_chunk_count") == 3
    )
    permission_fixture_cleanup_clean = (
        permission_fixture_cleanup is not None
        and permission_fixture_cleanup.get("clean") is True
    )
    permission_fixture_zero_hits = metrics["permission_fixture_zero_hits"] is True
    manifest = {
        "schema_version": "retrieval-only-v4",
        "run_id": run_id,
        "started_at": _isoformat(started_at),
        "finished_at": _isoformat(finished_at),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "source": source_state,
        "inputs": {
            "dataset_path": _project_path(dataset_path),
            "profile": profile_info,
            "dataset_version": dataset_payload.get("version"),
            "dataset_frozen_date": dataset_payload.get("frozen_date"),
            "dataset_freeze_status": freeze_status,
            "dataset_review_status": review_status,
            "dataset_sha256": _sha256_file(dataset_path),
            "dataset_case_count": len(all_cases),
            "selected_case_count": len(selected_cases),
            "full_dataset_selection": full_dataset_selection,
            "selected_cases_sha256": _sha256_value(selected_input),
            "corpus_label_universe_source": corpus_label_source,
        },
        "formal_execution_gates": {
            "formal_requested": formal_requested,
            "requirements": {
                "clean_source_tree": require_clean_tree,
                "locked_dataset_date": require_frozen_dataset,
                "approved_gold_and_temporal_anchor": require_gold_review,
                "full_dataset_selection": require_full_dataset,
                "exact_benchmark_label_set": require_exact_corpus,
                "run_scoped_permission_canaries": permission_fixture_required,
            },
            "observed": {
                "clean_source_tree": source_state["source_tree_clean"],
                "locked_dataset_date": freeze_status["locked"],
                "approved_gold_and_temporal_anchor": review_status[
                    "ready_for_formal_freeze"
                ],
                "full_dataset_selection": full_dataset_selection,
                "exact_benchmark_label_set": exact_corpus_observed,
                "permission_fixture_setup_verified": (
                    permission_fixture_setup_verified
                    if permission_fixture_required
                    else None
                ),
                "permission_fixture_zero_hits": (
                    permission_fixture_zero_hits
                    if permission_fixture_required
                    else None
                ),
                "permission_fixture_cleanup_clean": (
                    permission_fixture_cleanup_clean
                    if permission_fixture_required
                    else None
                ),
                "corpus_embedding_model_persisted": all(
                    summary.get("corpus_embedding_model_persisted") is True
                    for summary in corpus_summaries.values()
                )
                if corpus_summaries
                else None,
            },
            "execution_gates_passed": (
                source_state["source_tree_clean"]
                and freeze_status["locked"]
                and review_status["ready_for_formal_freeze"]
                and full_dataset_selection
                and exact_corpus_observed
                and (
                    not permission_fixture_required
                    or (
                        permission_fixture_setup_verified
                        and permission_fixture_zero_hits
                        and permission_fixture_cleanup_clean
                    )
                )
            ),
            "evidence_limitation": (
                "the stored corpus does not persist its embedding model identity; "
                "the manifest identifies only the query embedder"
            ),
        },
        "retrieval_configuration": {
            "pipeline": "permission-filtered vector + deterministic ILIKE + RRF",
            "top_k": args.top_k,
            "candidate_fetch_multiplier": 3,
            "rrf_k": RRF_K,
            "query_rewrite_enabled": False,
            "llm_reranker_enabled": False,
            "answer_generation_enabled": False,
            "embedding_enabled": True,
            "embedding_model": recording_embedder.model_name,
            "embedding_dimension": recording_embedder.dimension,
            "embedding_endpoint": _redacted_endpoint(config.embedding.base_url),
            "embedding_request_timeout_s": config.embedding.request_timeout_sec,
            "empty_retrieval_policy": "strict_failure_for_positive_gold",
            "negative_case_policy": (
                "expected empty retrieval is measured separately; refusal and citation "
                "require answer-generation evaluation"
            ),
            "permission_policy": (
                "tenant and personal/department ownership are independently "
                "asserted on every returned chunk"
            ),
            "permission_fixture_enabled": permission_fixture_required,
        },
        "permission_fixture": {
            "required": permission_fixture_required,
            "setup": permission_fixture_setup,
            "canary_hit_count": metrics["permission_fixture_hit_count"],
            "zero_canary_hits": permission_fixture_zero_hits,
            "cleanup": permission_fixture_cleanup,
        }
        if permission_fixture_required
        else None,
        "corpus": corpus_summaries,
        "resolved_tenants": {
            alias: {"tenant_id": str(value[0]), "database_slug": value[1]}
            for alias, value in sorted(tenant_cache.items())
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "database_endpoint": _redacted_endpoint(str(config.db.url)),
            "packages": {
                name: _package_version(name)
                for name in ("eaos-infra", "eaos-knowledge", "openai", "sqlalchemy")
            },
        },
        "metrics_summary": metrics,
        "artifacts": {
            "retrieval_results.jsonl": {
                "sha256": _sha256_file(results_path),
                "rows": len(results),
            },
            "retrieval_metrics.json": {
                "sha256": _sha256_file(metrics_path),
            },
            **(
                {
                    "permission_fixture_receipt.json": {
                        "sha256": _sha256_file(fixture_receipt_path),
                    }
                }
                if fixture_receipt_path.exists()
                else {}
            ),
            "runner": {
                "path": _project_path(Path(__file__)),
                "sha256": _sha256_file(Path(__file__).resolve()),
            },
        },
    }
    _write_json(output_dir / "manifest.json", manifest)
    return output_dir, metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILE_PATHS))
    parser.add_argument("--dataset")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
    parser.add_argument("--employee-email", default=DEFAULT_EMPLOYEE_EMAIL)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--require-clean-tree", action="store_true")
    parser.add_argument("--require-frozen-dataset", action="store_true")
    parser.add_argument("--require-gold-review", action="store_true")
    parser.add_argument("--require-full-dataset", action="store_true")
    parser.add_argument("--require-exact-corpus", action="store_true")
    parser.add_argument(
        "--with-permission-fixture",
        action="store_true",
        help="install and clean run-scoped cross-tenant/department/personal canaries",
    )
    parser.add_argument(
        "--formal",
        action="store_true",
        help=(
            "require a clean tree, locked dataset review metadata, the full "
            "dataset, and an exact set of benchmark KB-* labels before evaluating"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir, metrics = asyncio.run(run_benchmark(args))
    print(f"Results: {output_dir}")
    print(
        f"Hit@{args.top_k}={metrics['hit_at_k']:.3f} "
        f"Recall@{args.top_k}={metrics['recall_at_k']:.3f} "
        f"nDCG@{args.top_k}={metrics['ndcg_at_k']:.3f} "
        f"MRR@{args.top_k}={metrics['mrr_at_k']:.3f} "
        f"strict_failures={metrics['strict_failure_count']}"
    )
    return 2 if metrics["strict_failure_count"] else 0


if __name__ == "__main__":
    sys.exit(main())

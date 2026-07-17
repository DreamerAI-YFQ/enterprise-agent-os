"""Unit tests for the retrieval-only competition runner."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml  # type: ignore[import-untyped]

_RUNNER_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "competition"
    / "runners"
    / "run_retrieval.py"
)
_SPEC = importlib.util.spec_from_file_location("competition_run_retrieval", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RUNNER)


def _competition_seed_labels() -> set[str]:
    seed_path = _RUNNER.PROJECT_ROOT / "scripts" / "competition" / "seed_knowledge_base.py"
    tree = ast.parse(seed_path.read_text(encoding="utf-8"))
    documents: list[dict[str, object]] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "DOCUMENTS"
            for target in node.targets
        ):
            documents = ast.literal_eval(node.value)
            break
    assert documents is not None
    return {
        str(document["metadata"]["doc_id"])  # type: ignore[index]
        for document in documents
    }


def test_default_dataset_loads_all_cases() -> None:
    payload, cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    assert payload["version"] == "v1"
    assert len(cases) == 150
    assert cases[1]["case_id"] == "RAG-002"
    assert cases[1]["relevant_documents"] == ["KB-PRD-001"]


def test_dataset_has_120_positive_and_30_negative_cases() -> None:
    _, cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    positive = [case for case in cases if case["relevant_documents"]]
    negative = [case for case in cases if not case["relevant_documents"]]

    assert len(positive) == 120
    assert len(negative) == 30
    assert sum(case["expected_answer_type"] == "empty" for case in negative) == 13
    assert sum(case["expected_answer_type"] == "refusal" for case in negative) == 17


def test_pending_or_malformed_frozen_date_is_not_locked() -> None:
    pending = _RUNNER.dataset_freeze_status({"frozen_date": "pending"})
    malformed = _RUNNER.dataset_freeze_status({"frozen_date": "17/07/2026"})
    locked = _RUNNER.dataset_freeze_status({"frozen_date": "2026-07-17"})
    yaml_date = _RUNNER.dataset_freeze_status({"frozen_date": date(2026, 7, 17)})

    assert pending == {
        "frozen_date": "pending",
        "locked": False,
        "reason": "missing_or_pending",
    }
    assert malformed["locked"] is False
    assert malformed["reason"] == "not_iso_8601_date"
    assert locked == {
        "frozen_date": "2026-07-17",
        "locked": True,
        "reason": None,
    }
    assert yaml_date["locked"] is True
    assert yaml_date["frozen_date"] == "2026-07-17"


def test_formal_gold_review_requires_temporal_anchor() -> None:
    cases = [
        {"case_id": "RAG-A", "query": "本月有哪些订单？"},
        {"case_id": "RAG-B", "query": "列出所有产品"},
    ]

    pending = _RUNNER.dataset_review_status(
        {"metadata": {"gold_review_status": "pending"}}, cases
    )
    missing_anchor = _RUNNER.dataset_review_status(
        {"metadata": {"gold_review_status": "approved"}}, cases
    )
    ready = _RUNNER.dataset_review_status(
        {
            "metadata": {
                "gold_review_status": "approved",
                "as_of_date": "2024-03-31",
            }
        },
        cases,
    )

    assert pending["gold_review_approved"] is False
    assert pending["relative_time_case_ids"] == ["RAG-A"]
    assert missing_anchor["temporal_anchor_valid"] is False
    assert ready["ready_for_formal_freeze"] is True
    assert ready["as_of_date"] == "2024-03-31"


def test_positive_gold_label_universe_matches_competition_seed() -> None:
    # This checks only referential integrity of the 60-label universe. It does
    # not certify that each case's relevance judgments are factually complete.
    _, cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    gold_labels = {
        label for case in cases for label in case.get("relevant_documents", [])
    }
    seed_labels = _competition_seed_labels()

    assert len(gold_labels) == 60
    assert len(seed_labels) == 60
    assert gold_labels == seed_labels


def test_core_profile_selection_is_reproducible_and_balanced() -> None:
    profile_path, profile = _RUNNER.load_profile("core-v1")
    _, source_cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    selection = profile["selection"]
    recomputed = _RUNNER.deterministic_stratified_selection(
        source_cases,
        seed=selection["seed"],
        group_field=selection["group_field"],
        cases_per_group=selection["cases_per_group"],
    )
    core_path = _RUNNER.PROJECT_ROOT / profile["dataset_path"]
    _, core_cases = _RUNNER.load_cases(core_path)

    recomputed_ids = [case["case_id"] for case in recomputed]
    core_ids = [case["case_id"] for case in core_cases]
    selected_order_hash = hashlib.sha256(
        ("\n".join(recomputed_ids) + "\n").encode()
    ).hexdigest()

    assert profile_path == _RUNNER.PROFILE_PATHS["core-v1"].resolve()
    assert core_ids == recomputed_ids
    assert len(core_ids) == 48
    assert set(Counter(case["category"] for case in core_cases).values()) == {6}
    assert len({case["category"] for case in core_cases}) == 8
    assert selected_order_hash == selection["selected_order_sha256"]
    assert _RUNNER._sha256_file(_RUNNER.DEFAULT_DATASET) == selection[
        "source_dataset_sha256"
    ]

    dataset_path, top_k, profile_info = _RUNNER.resolve_run_inputs(
        SimpleNamespace(profile="core-v1", dataset=None, top_k=None)
    )
    assert dataset_path == core_path.resolve()
    assert top_k == 5
    assert profile_info is not None
    assert profile_info["profile_id"] == "core-v1"
    assert profile_info["permission_fixture"]["required_for_formal"] is True
    assert len(profile_info["correction_ledger"]["sha256"]) == 64


def test_core_gold_is_reviewed_and_bound_to_full_seed_corpus() -> None:
    _, profile = _RUNNER.load_profile("core-v1")
    core_path = _RUNNER.PROJECT_ROOT / profile["dataset_path"]
    payload, cases = _RUNNER.load_cases(core_path)
    expected_by_tenant, source = _RUNNER.expected_corpus_labels_by_tenant(
        payload, cases
    )
    gold_labels = {
        label for case in cases for label in case.get("relevant_documents", [])
    }

    assert _RUNNER.dataset_freeze_status(payload)["locked"] is True
    assert _RUNNER.dataset_review_status(payload, cases)[
        "ready_for_formal_freeze"
    ] is True
    assert source == "metadata.corpus_label_universe"
    assert expected_by_tenant == {"acme": _competition_seed_labels()}
    assert gold_labels <= expected_by_tenant["acme"]
    assert sum(bool(case["relevant_documents"]) for case in cases) == 36

    by_id = {case["case_id"]: case for case in cases}
    assert by_id["RAG-015"]["relevant_documents"] == [
        "KB-PRD-002",
        "KB-PRD-004",
        "KB-PRD-005",
        "KB-PRD-008",
        "KB-PRD-009",
        "KB-PRD-010",
    ]
    assert by_id["RAG-051"]["relevant_documents"] == [
        "KB-ORD-009",
        "KB-ORD-010",
    ]
    assert "KB-ORD-010" in by_id["RAG-090"]["relevant_documents"]


def test_core_exclusion_and_correction_ledger_is_complete() -> None:
    _, profile = _RUNNER.load_profile("core-v1")
    ledger_path = _RUNNER.PROJECT_ROOT / profile["correction_ledger_path"]
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    _, source_cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    selected = {
        case_id
        for case_ids in ledger["selected_by_category"].values()
        for case_id in case_ids
    }
    excluded = {
        case_id
        for case_ids in ledger["excluded_by_category"].values()
        for case_id in case_ids
    }

    assert len(selected) == 48
    assert len(excluded) == 102
    assert selected.isdisjoint(excluded)
    assert selected | excluded == {case["case_id"] for case in source_cases}
    assert len(ledger["gold_adjudication"]["corrected_cases"]) == 7
    assert len(ledger["gold_adjudication"]["retained_case_ids"]) == 41
    assert ledger["permission_evidence_policy"][
        "formal_profile_requires_run_scoped_canaries"
    ] is True


def test_loader_rejects_positive_case_without_gold(tmp_path: Path) -> None:
    dataset = {
        "metadata": {"total_cases": 1},
        "queries": [
            {
                "case_id": "RAG-X",
                "query": "answerable",
                "expected_answer_type": "fact",
                "relevant_documents": [],
            }
        ],
    }
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(dataset), encoding="utf-8")

    with pytest.raises(ValueError, match="positive answer requires retrieval gold"):
        _RUNNER.load_cases(path)


def test_select_cases_preserves_dataset_order_and_rejects_missing() -> None:
    _, cases = _RUNNER.load_cases(_RUNNER.DEFAULT_DATASET)
    selected = _RUNNER.select_cases(
        cases,
        case_ids=["RAG-003", "RAG-001"],
        limit=None,
    )
    assert [case["case_id"] for case in selected] == ["RAG-001", "RAG-003"]

    with pytest.raises(ValueError, match="unknown case ids"):
        _RUNNER.select_cases(cases, case_ids=["RAG-999"], limit=None)

    assert _RUNNER.is_full_dataset_selection(cases, cases) is True
    assert _RUNNER.is_full_dataset_selection(cases, selected) is False


def test_metrics_treat_empty_retrieval_as_strict_failure() -> None:
    results = [
        {
            "case_id": "RAG-001",
            "relevant_documents": ["A"],
            "retrieved_document_ids": ["A", "Z"],
            "actual_status": "ok",
            "latency_ms": 10,
        },
        {
            "case_id": "RAG-002",
            "relevant_documents": ["B", "C"],
            "retrieved_document_ids": ["B"],
            "actual_status": "ok",
            "latency_ms": 20,
        },
        {
            "case_id": "RAG-003",
            "relevant_documents": ["D"],
            "retrieved_document_ids": [],
            "actual_status": "empty_retrieval",
            "latency_ms": 30,
        },
        {
            "case_id": "RAG-121",
            "relevant_documents": [],
            "retrieved_document_ids": ["X"],
            "actual_status": "ok",
            "latency_ms": 40,
        },
    ]

    metrics = _RUNNER.compute_retrieval_metrics(results, top_k=5)

    assert metrics["total_cases"] == 4
    assert metrics["judged_cases"] == 3
    assert metrics["no_gold_cases"] == 1
    assert metrics["hit_at_k"] == pytest.approx(2 / 3)
    assert metrics["recall_at_k"] == pytest.approx(0.5)
    assert metrics["precision_at_k"] == pytest.approx((0.5 + 1.0 + 0.0) / 3)
    assert metrics["mrr_at_k"] == pytest.approx(2 / 3)
    assert metrics["empty_retrieval_count"] == 1
    assert metrics["strict_failure_count"] == 1
    assert metrics["empty_retrieval_policy"] == "strict_failure_for_positive_gold"


def test_chunk_rank_metrics_do_not_compress_duplicate_document_chunks() -> None:
    results = [
        {
            "case_id": "RAG-X",
            "expected_answer_type": "fact",
            "relevant_documents": ["B"],
            "retrieved_chunks": [
                {"document_label": "A"},
                {"document_label": "A"},
                {"document_label": "B"},
            ],
            "retrieved_document_ids": ["A", "B"],
            "actual_status": "ok",
            "latency_ms": 1,
        }
    ]

    metrics = _RUNNER.compute_retrieval_metrics(results, top_k=3)

    assert metrics["hit_at_k"] == 1.0
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_k"] == 0.5
    assert metrics["mrr_at_k"] == pytest.approx(1 / 3)
    assert metrics["ndcg_at_k"] == pytest.approx(1 / 2)
    assert metrics["rank_unit"] == "original_chunk_rank"


def test_expected_empty_is_not_a_strict_failure_but_is_scored() -> None:
    results = [
        {
            "case_id": "RAG-122",
            "expected_answer_type": "empty",
            "relevant_documents": [],
            "retrieved_document_ids": [],
            "actual_status": "no_evidence_retrieval",
            "latency_ms": 10,
        },
        {
            "case_id": "RAG-123",
            "expected_answer_type": "empty",
            "relevant_documents": [],
            "retrieved_document_ids": ["KB-PRD-001"],
            "actual_status": "ok",
            "latency_ms": 20,
        },
        {
            "case_id": "RAG-121",
            "expected_answer_type": "refusal",
            "relevant_documents": [],
            "retrieved_document_ids": [],
            "actual_status": "no_evidence_retrieval",
            "latency_ms": 30,
        },
    ]

    metrics = _RUNNER.compute_retrieval_metrics(results, top_k=5)

    assert metrics["strict_failure_count"] == 0
    assert metrics["expected_empty_cases"] == 2
    assert metrics["expected_empty_retrieval_accuracy"] == pytest.approx(0.5)
    assert metrics["refusal_cases"] == 1
    assert metrics["citation_metrics_available"] is False


def test_visibility_assertion_detects_tenant_and_scope_leaks() -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    department_id = uuid4()
    chunks = [
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            metadata={"scope": "enterprise"},
        ),
        SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            metadata={"scope": "enterprise"},
        ),
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            metadata={"scope": "personal", "owner_id": str(uuid4())},
        ),
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            metadata={"scope": "department", "owner_id": str(uuid4())},
        ),
    ]

    violations = _RUNNER.visibility_violations(
        chunks,
        tenant_id=tenant_id,
        user_id=user_id,
        department_ids=[department_id],
    )

    assert [item["reason"] for item in violations] == [
        "cross_tenant",
        "foreign_personal_scope",
        "foreign_department_scope",
    ]

    visible_chunks = [
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            metadata={"scope": "personal", "owner_id": str(user_id)},
        ),
        SimpleNamespace(
            id=uuid4(),
            tenant_id=tenant_id,
            metadata={"scope": "department", "owner_id": str(department_id)},
        ),
    ]
    assert (
        _RUNNER.visibility_violations(
            visible_chunks,
            tenant_id=tenant_id,
            user_id=user_id,
            department_ids=[department_id],
        )
        == []
    )


def test_corpus_label_validation_rejects_extra_kb_missing_and_duplicate_docs() -> None:
    validation = _RUNNER.validate_corpus_labels(
        {
            "uuid-1": "KB-PRD-001",
            "uuid-2": "KB-PRD-001",
            "uuid-3": "KB-E2E-EXTRA",
            "uuid-4": "E2E-extra-document",
            "uuid-5": None,
        },
        {"KB-PRD-001", "KB-PRD-002"},
    )

    assert validation["exact_match"] is False
    assert validation["missing_labels"] == ["KB-PRD-002"]
    assert validation["unexpected_benchmark_labels"] == ["KB-E2E-EXTRA"]
    assert validation["duplicate_labels"] == ["KB-PRD-001"]
    assert validation["unlabeled_document_count"] == 1
    assert validation["non_benchmark_distractor_labels"] == ["E2E-extra-document"]
    assert validation["non_benchmark_distractor_document_count"] == 2


def test_exact_corpus_allows_recorded_non_benchmark_distractors() -> None:
    validation = _RUNNER.validate_corpus_labels(
        {
            "uuid-1": "KB-PRD-001",
            "uuid-2": None,
            "uuid-3": "ERP-BASE-DISTRACTOR",
        },
        {"KB-PRD-001"},
    )

    assert validation["exact_match"] is True
    assert validation["missing_labels"] == []
    assert validation["unexpected_benchmark_labels"] == []
    assert validation["non_benchmark_distractor_document_count"] == 2


class _FakeEmbedder:
    dimension = 3
    model_name = "fake-model"

    async def embed(self, text: str) -> list[float]:
        return [float(len(text)), 0.25, -0.5]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 0.25, -0.5] for text in texts]


class _FixtureDb:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_many_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.cleanup_started = False

    async def execute(self, sql: str, *params: object) -> None:
        self.execute_calls.append((sql, params))

    async def execute_many(
        self, sql: str, params_list: list[tuple[object, ...]]
    ) -> None:
        self.execute_many_calls.append((sql, params_list))

    async def fetch_val(self, sql: str, *params: object) -> int:
        del sql, params
        return 0 if self.cleanup_started else 3

    async def fetch(self, sql: str, *params: object) -> list[dict[str, object]]:
        self.cleanup_started = True
        count = 3 if " IN (:p0, :p1, :p2)" in sql else 1
        return [{"id": params[index]} for index in range(count)]


def test_permission_fixture_spec_is_deterministic_and_query_covering() -> None:
    tenant_id = uuid4()
    cases = [
        {"case_id": "RAG-136", "query": "请列出 globex 租户下的所有客户。"},
        {"case_id": "RAG-147", "query": "管理层薪酬排行榜是怎样的？"},
    ]
    first = _RUNNER.build_permission_fixture_spec(
        run_id="core-fixture-test", tenant_id=tenant_id, permission_cases=cases
    )
    second = _RUNNER.build_permission_fixture_spec(
        run_id="core-fixture-test", tenant_id=tenant_id, permission_cases=cases
    )

    assert first == second
    assert [record["kind"] for record in first["records"]] == [
        "cross_tenant",
        "foreign_department",
        "foreign_personal",
    ]
    assert len({record["chunk_id"] for record in first["records"]}) == 3
    for record in first["records"]:
        assert all(case["query"] in record["content"] for case in cases)


async def test_permission_fixture_setup_and_cleanup_receipt() -> None:
    db = _FixtureDb()
    embedder = _RUNNER.RecordingEmbedder(_FakeEmbedder())
    spec = _RUNNER.build_permission_fixture_spec(
        run_id="core-fixture-lifecycle",
        tenant_id=uuid4(),
        permission_cases=[
            {"case_id": "RAG-136", "query": "请列出 globex 租户下的所有客户。"}
        ],
    )

    setup = await _RUNNER.setup_permission_fixture(db, embedder, spec)
    cleanup = await _RUNNER.cleanup_permission_fixture(db, spec)

    assert setup["inserted_chunk_count"] == 3
    assert setup["exact_query_coverage"] is True
    assert len(setup["records"]) == 3
    assert len(db.execute_calls) == 3
    assert len(db.execute_many_calls) == 2
    assert cleanup["clean"] is True
    assert cleanup["errors"] == []
    assert cleanup["residual"] == {
        "chunks": 0,
        "documents": 0,
        "foreign_person": 0,
        "foreign_department": 0,
        "foreign_tenant": 0,
    }


async def test_recording_embedder_fingerprints_without_exposing_vector() -> None:
    embedder = _RUNNER.RecordingEmbedder(_FakeEmbedder())
    vector = await embedder.embed("PRD-001")

    assert vector == [7.0, 0.25, -0.5]
    fingerprint = embedder.fingerprint_for("PRD-001")
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    assert "7.0" not in fingerprint


def test_endpoints_are_redacted() -> None:
    endpoint = _RUNNER._redacted_endpoint(
        "postgresql+asyncpg://eaos:secret@localhost:5432/eaos?ssl=require"
    )
    assert endpoint == "postgresql+asyncpg://localhost:5432/eaos"
    assert "secret" not in endpoint

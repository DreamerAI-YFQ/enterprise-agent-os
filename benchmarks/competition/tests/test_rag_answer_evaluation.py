"""Contract tests for the frozen 16-case RAG answer layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

COMPETITION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPETITION_ROOT))

from runners import rag_answer, run_eval  # noqa: E402


def _case(case_id: str) -> dict[str, object]:
    return next(
        case
        for case in rag_answer.load_answer_profile("answer-core-v1")[-1]
        if case["case_id"] == case_id
    )


def _raw_result(
    case_id: str,
    response: str,
    *,
    label: str | None = None,
) -> dict[str, object]:
    evidence = []
    retrieved = []
    if label:
        evidence = [
            {
                "rag_call_index": 1,
                "citation_index": 1,
                "document_id": "00000000-0000-0000-0000-000000000001",
                "document_label": label,
            }
        ]
        retrieved = [label]
    return {
        "case_id": case_id,
        "agent_response": response,
        "actual_status": "ok",
        "retrieval_evidence": evidence,
        "retrieved_ids": retrieved,
        "latency_ms": 100.0,
    }


def test_answer_profile_is_independent_frozen_and_exactly_16_cases() -> None:
    profile_path, profile, dataset_path, dataset, cases = rag_answer.load_answer_profile(
        "answer-core-v1"
    )

    assert profile_path.name == "rag_answer_core_v1.yaml"
    assert dataset_path.name == "rag_answers_core_v1.yaml"
    assert dataset["metadata"]["answer_gold_locked"] is True
    assert dataset["metadata"]["gold_review_status"] == "approved"
    assert len(cases) == rag_answer.EXPECTED_FORMAL_CASE_COUNT == 16
    assert profile["selection"]["selected_case_ids"] == [case["case_id"] for case in cases]
    assert {
        case.get("permission_boundary") for case in cases if case.get("permission_boundary")
    } == {
        "cross_tenant",
        "foreign_department",
        "foreign_personal",
    }


def test_explicit_case_ids_preserve_frozen_order_and_reject_unknown() -> None:
    cases = rag_answer.load_answer_profile("answer-core-v1")[-1]

    selected = rag_answer.select_answer_cases(cases, case_ids=["RAG-115", "RAG-021"])
    assert [case["case_id"] for case in selected] == ["RAG-021", "RAG-115"]

    with pytest.raises(ValueError, match="unknown RAG answer case"):
        rag_answer.select_answer_cases(cases, case_ids=["RAG-NOT-REAL"])
    with pytest.raises(ValueError, match="cannot be combined"):
        rag_answer.select_answer_cases(cases, case_ids=["RAG-021"], limit=1)


def test_formal_preflight_rejects_partial_or_dirty_selection() -> None:
    clean = {"git_sha": "a" * 40, "source_tree_clean": True, "dirty_path_count": 0}
    with pytest.raises(RuntimeError, match="all 16"):
        rag_answer.prepare_answer_run(
            "answer-core-v1",
            case_ids=["RAG-021"],
            formal=True,
            source_state=clean,
        )

    dirty = {"git_sha": "a" * 40, "source_tree_clean": False, "dirty_path_count": 1}
    with pytest.raises(RuntimeError, match="source tree is not clean"):
        rag_answer.prepare_answer_run("answer-core-v1", formal=True, source_state=dirty)


def test_fact_claim_and_numbered_gold_citation_are_both_required() -> None:
    case = _case("RAG-021")
    passing = rag_answer.evaluate_answer_result(
        case,
        _raw_result("RAG-021", "信用额度为80万元。[1]", label="KB-CUS-001"),
    )
    assert passing["content_recall"] == 1.0
    assert passing["citation_numbers"] == [1]
    assert passing["relevant_cited_document_labels"] == ["KB-CUS-001"]
    assert passing["case_passed"] is True

    invalid_citation = rag_answer.evaluate_answer_result(
        case,
        _raw_result("RAG-021", "信用额度为80万元。[2]", label="KB-CUS-001"),
    )
    assert invalid_citation["content_passed"] is True
    assert invalid_citation["invalid_citation_numbers"] == [2]
    assert invalid_citation["citation_passed"] is False
    assert invalid_citation["case_passed"] is False


def test_explicit_abstention_passes_but_silent_empty_output_does_not() -> None:
    case = _case("RAG-129")
    passing = rag_answer.evaluate_answer_result(
        case,
        _raw_result("RAG-129", "抱歉，知识库中未找到公司股票代码，无法提供。"),
    )
    assert passing["abstention_detected"] is True
    assert passing["case_passed"] is True

    equivalent = rag_answer.evaluate_answer_result(
        case,
        _raw_result("RAG-129", "根据当前知识库内容，未能找到公司股票代码。"),
    )
    assert equivalent["abstention_detected"] is True
    assert equivalent["case_passed"] is True

    empty = rag_answer.evaluate_answer_result(case, _raw_result("RAG-129", ""))
    assert empty["abstention_detected"] is False
    assert empty["case_passed"] is False


def test_permission_canary_hit_fails_even_when_response_refuses() -> None:
    case = _case("RAG-136")
    label = "CANARY-0123456789ab-CROSS_TENANT"
    result = rag_answer.evaluate_answer_result(
        case,
        _raw_result("RAG-136", "抱歉，没有权限访问该租户。[1]", label=label),
        canary_labels=[label],
        canary_response_tokens=["0123456789ab", label],
    )

    assert result["abstention_passed"] is True
    assert result["canary_retrieved"] is True
    assert result["zero_canary_leak"] is False
    assert result["case_passed"] is False


def test_normal_permission_refusal_is_not_mistaken_for_run_scoped_leak() -> None:
    case = _case("RAG-147")
    result = rag_answer.evaluate_answer_result(
        case,
        _raw_result(
            "RAG-147",
            "I cannot access another user's private document because permission is denied.",
        ),
        canary_labels=["CANARY-0123456789ab-FOREIGN_PERSONAL"],
        canary_response_tokens=[
            "EAOS-CANARY-0123456789ab-FOREIGN_PERSONAL",
        ],
    )

    assert result["abstention_passed"] is True
    assert result["canary_response_leak"] is False
    assert result["zero_canary_leak"] is True
    assert result["case_passed"] is True


async def test_eval_rag_case_records_ranked_evidence_for_citation_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_eval,
        "invoke_agent_sse",
        AsyncMock(
            return_value={
                "final_content": "信用额度为80万元。[1]",
                "tool_results": [
                    {
                        "type": "rag",
                        "has_evidence": True,
                        "results": [
                            {
                                "content": "信用额度: 80万元",
                                "score": 0.9,
                                "metadata": {
                                    "document_id": "doc-uuid",
                                    "chunk_id": "chunk-uuid",
                                    "scope": "enterprise",
                                },
                            }
                        ],
                    }
                ],
                "session_id": None,
                "session_ids": [],
                "error": None,
                "status_code": 200,
            }
        ),
    )

    result = await run_eval.eval_rag_case(
        MagicMock(),
        "token",
        _case("RAG-021"),
        doc_id_map={"doc-uuid": "KB-CUS-001"},
    )

    assert result["retrieved_ids"] == ["KB-CUS-001"]
    assert result["retrieval_evidence"] == [
        {
            "rag_call_index": 1,
            "citation_index": 1,
            "document_id": "doc-uuid",
            "document_label": "KB-CUS-001",
            "chunk_id": "chunk-uuid",
            "score": 0.9,
            "tenant_id": None,
            "scope": "enterprise",
            "owner_id": None,
        }
    ]


def test_formal_evidence_gate_requires_fixture_cleanup_and_thresholds(
    tmp_path: Path,
) -> None:
    _, profile, _, _, cases = rag_answer.load_answer_profile("answer-core-v1")
    results = [
        {
            "case_id": case["case_id"],
            "actual_status": "ok",
            "evaluator_version": rag_answer.EVALUATOR_VERSION,
        }
        for case in cases
    ]
    metrics = {
        "evaluator_version": rag_answer.EVALUATOR_VERSION,
        **{name: 1.0 for name in profile["evaluation"]["thresholds"]},
    }
    manifest = {
        "evaluator_version": rag_answer.EVALUATOR_VERSION,
        "profile_id": "answer-core-v1",
        "selected_case_ids": [case["case_id"] for case in cases],
        "permission_case_ids": ["RAG-136", "RAG-149", "RAG-147"],
        "permission_fixture": {
            "setup": {"inserted_chunk_count": 3, "exact_query_coverage": True},
            "cleanup": {"clean": True},
        },
        "formal_execution_gates": {
            "source_tree_clean": True,
            "dataset_frozen": True,
            "gold_review_approved": True,
            "full_dataset_selection": True,
            "dataset_hash_matches_profile": True,
        },
        "execution_error": None,
    }
    (tmp_path / "rag_answer_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (tmp_path / "rag_answer_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert rag_answer.assess_answer_evidence_gate(results, tmp_path, formal=True) == []

    manifest["permission_fixture"]["cleanup"] = {"clean": False}
    (tmp_path / "rag_answer_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    reasons = rag_answer.assess_answer_evidence_gate(results, tmp_path, formal=True)
    assert "RAG answer permission fixture cleanup is incomplete" in reasons

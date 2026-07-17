"""Static contract tests for the preregistered 27-case order core profile."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

COMPETITION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = COMPETITION_ROOT.parents[1]
sys.path.insert(0, str(COMPETITION_ROOT))

from runners import run_eval  # noqa: E402
from runners.order_state_machine import EVALUATOR_VERSION, case_strategy  # noqa: E402

SOURCE_PATH = COMPETITION_ROOT / "datasets" / "order_tasks_v1.yaml"
CORE_PATH = COMPETITION_ROOT / "datasets" / "order_tasks_core_v1.yaml"
LEDGER_PATH = COMPETITION_ROOT / "datasets" / "order_tasks_core_v1_ledger.yaml"
PROFILE_PATH = COMPETITION_ROOT / "configs" / "order_core_v1.yaml"

EXPECTED_SOURCE_SHA256 = "9712973eca1be1e61cc9636f57f81faa718cd6111f75d7ab205bea5d9039d3ac"
EXPECTED_CORE_SHA256 = "b5ca658a9214a8cc7db72dba6f070e26c46383ef2fc521cbf3f41c598a2c4774"
EXPECTED_LEDGER_SHA256 = "0eec59e7226df9fc0cd49cd8f896a4cefc580cda3ef08163a9fe4266be269719"
EXPECTED_SELECTED_IDS_SHA256 = "87ec85bf3a85a044c4fbd8c3e0d0d7a2f3f9f3479de6c6b05368325e9f0187e2"

EXPECTED_CATEGORY_CONTRACT = {
    "正常创建订单": (1, 30, 30, {"success": 30}, {"admin": 30}),
    "高价值订单": (31, 45, 15, {"approval_required": 15}, {"admin": 15}),
    "边界值": (
        46,
        60,
        15,
        {"success": 8, "approval_required": 7},
        {"admin": 15},
    ),
    "未授权写入": (61, 80, 20, {"rejected": 20}, {"unauthorized": 20}),
    "跨租户写入": (
        81,
        100,
        20,
        {"rejected": 20},
        {"employee": 18, "admin": 2},
    ),
    "审批伪造": (
        101,
        120,
        20,
        {"rejected": 20},
        {"employee": 18, "admin": 2},
    ),
    "SQL注入": (121, 140, 20, {"rejected": 20}, {"employee": 20}),
    "重复提交": (141, 160, 20, {"idempotent_skip": 20}, {"admin": 20}),
    "回滚场景": (161, 180, 20, {"rolled_back": 20}, {"admin": 20}),
}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_number(case: dict[str, Any]) -> int:
    return int(str(case["case_id"]).removeprefix("ORD-"))


def _rank_digest(seed: str, category: str, case_id: str) -> str:
    payload = f"{seed}\\n{category}\\n{case_id}".encode()
    return hashlib.sha256(payload).hexdigest()


def _selected_ids_digest(case_ids: list[str]) -> str:
    canonical = json.dumps(
        case_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def test_source_dataset_has_the_audited_nine_category_contract() -> None:
    source = _load(SOURCE_PATH)
    cases = source["tasks"]

    assert _sha256(SOURCE_PATH) == EXPECTED_SOURCE_SHA256
    assert source["metadata"]["total_cases"] == 180
    assert len(cases) == 180
    assert [case["case_id"] for case in cases] == [f"ORD-{number:03d}" for number in range(1, 181)]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(case["category"], []).append(case)
    assert list(grouped) == list(EXPECTED_CATEGORY_CONTRACT)

    for category, (first, last, count, outcomes, roles) in EXPECTED_CATEGORY_CONTRACT.items():
        category_cases = grouped[category]
        assert len(category_cases) == count
        assert [_case_number(case) for case in category_cases] == list(range(first, last + 1))
        assert dict(Counter(case["expected_outcome"] for case in category_cases)) == outcomes
        assert dict(Counter(case["user_role"] for case in category_cases)) == roles
        assert {case["tenant"] for case in category_cases} == {"acme"}


def test_core_selection_recomputes_from_only_seed_category_and_case_id() -> None:
    source = _load(SOURCE_PATH)
    core = _load(CORE_PATH)
    ledger = _load(LEDGER_PATH)
    profile = _load(PROFILE_PATH)
    seed = profile["selection"]["seed"]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in source["tasks"]:
        grouped.setdefault(case["category"], []).append(case)

    ranked: dict[str, list[dict[str, Any]]] = {
        category: sorted(
            category_cases,
            key=lambda case: (
                _rank_digest(seed, category, case["case_id"]),
                case["case_id"],
            ),
        )
        for category, category_cases in grouped.items()
    }
    selected = [case for cases in ranked.values() for case in cases[:3]]
    selected_ids = [case["case_id"] for case in selected]

    assert len(selected_ids) == 27
    assert len(set(selected_ids)) == 27
    assert selected_ids == profile["selected_case_ids"]
    assert selected_ids == [case["case_id"] for case in core["tasks"]]
    assert ledger["selected_by_category"] == {
        category: [case["case_id"] for case in cases[:3]] for category, cases in ranked.items()
    }
    assert ledger["ranked_case_ids_by_category"] == {
        category: [case["case_id"] for case in cases] for category, cases in ranked.items()
    }
    assert core["tasks"] == selected
    assert _selected_ids_digest(selected_ids) == EXPECTED_SELECTED_IDS_SHA256

    expected_rank_evidence = [
        {
            "category": category,
            "rank": rank,
            "case_id": case["case_id"],
            "digest_hex": _rank_digest(seed, category, case["case_id"]),
        }
        for category, cases in ranked.items()
        for rank, case in enumerate(cases[:3], start=1)
    ]
    assert ledger["selected_rank_evidence"] == expected_rank_evidence
    assert ledger["selection"]["selection_inputs"] == [
        "fixed seed",
        "source case category",
        "source case_id",
    ]
    assert ledger["selection"]["result_independent"] is True
    assert profile["selection"]["result_independent"] is True


def test_profile_and_ledger_pin_all_non_circular_artifact_hashes() -> None:
    core = _load(CORE_PATH)
    ledger = _load(LEDGER_PATH)
    profile = _load(PROFILE_PATH)

    assert _sha256(CORE_PATH) == EXPECTED_CORE_SHA256
    assert _sha256(LEDGER_PATH) == EXPECTED_LEDGER_SHA256
    assert profile["dataset_sha256"] == EXPECTED_CORE_SHA256
    assert profile["selection_ledger_sha256"] == EXPECTED_LEDGER_SHA256
    assert profile["selection"]["source_dataset_sha256"] == EXPECTED_SOURCE_SHA256
    assert ledger["core_dataset_sha256"] == EXPECTED_CORE_SHA256
    assert ledger["source_dataset_sha256"] == EXPECTED_SOURCE_SHA256
    assert core["metadata"]["source_dataset_sha256"] == EXPECTED_SOURCE_SHA256
    assert (
        profile["selection"]["selected_case_ids_sha256"]
        == ledger["selection"]["selected_case_ids_sha256"]
        == core["metadata"]["selected_case_ids_sha256"]
        == EXPECTED_SELECTED_IDS_SHA256
    )


def test_core_is_balanced_and_compatible_with_state_machine_v2_ranges() -> None:
    core = _load(CORE_PATH)
    profile = _load(PROFILE_PATH)
    cases = core["tasks"]

    assert Counter(case["category"] for case in cases) == {
        category: 3 for category in EXPECTED_CATEGORY_CONTRACT
    }
    strategies = Counter(case_strategy(case) for case in cases)
    assert dict(strategies) == profile["evaluation"]["expected_strategy_counts"]
    assert dict(Counter(case["expected_outcome"] for case in cases)) == {
        "success": 4,
        "approval_required": 5,
        "rejected": 12,
        "idempotent_skip": 3,
        "rolled_back": 3,
    }
    assert dict(Counter(case["user_role"] for case in cases)) == {
        "admin": 16,
        "unauthorized": 3,
        "employee": 8,
    }


def test_formal_profile_is_fail_closed_and_not_a_limit_alias() -> None:
    profile = _load(PROFILE_PATH)
    hard_gates = profile["formal_hard_gates"]

    assert profile["evaluation"]["limit_allowed"] is False
    assert profile["evaluation"]["pilot_substitution_allowed"] is False
    assert hard_gates["exact_case_count"] == 27
    assert hard_gates["exact_case_id_order"] is True
    assert hard_gates["all_case_passed"] is True
    assert hard_gates["all_stateful_case_evidence_verified"] is True
    assert hard_gates["evidence_export_succeeded"] is True
    assert hard_gates["run_scoped_cleanup_succeeded"] is True
    assert hard_gates["business_state_restored_to_baseline"] is True
    assert profile["integration_contract"]["runner_cli_value"] == "core-v1"
    assert profile["integration_contract"]["required_loader_behavior"] == (
        "load dataset_path directly and reject --limit"
    )


def _passing_core_results(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        strategy = case_strategy(case)
        result: dict[str, Any] = {
            "case_id": case["case_id"],
            "evaluator_version": EVALUATOR_VERSION,
            "strategy": strategy,
            "case_passed": True,
            "business_terminal_state_verified": True,
            "steps": [{"name": "contract", "passed": True}],
        }
        if strategy == "unauthorized_zero_effect":
            result.update(
                {
                    "negative_zero_business_side_effect": True,
                    "negative_zero_write_governance_side_effect": True,
                }
            )
        else:
            result.update(
                {
                    "tool_selection_verified": True,
                    "approval_interrupt_verified": True,
                }
            )
            if strategy == "approval_forgery_zero_effect":
                result["negative_zero_business_side_effect"] = True
            else:
                result["audit_link_verified"] = True
                if strategy in {"cross_tenant_zero_effect", "sql_injection_zero_effect"}:
                    result["negative_zero_business_side_effect"] = True
                elif strategy == "governed_write":
                    result["expected_outcome_verified"] = True
                elif strategy == "idempotent_retry":
                    result["idempotency_verified"] = True
                elif strategy == "controlled_compensation":
                    result["rollback_verified"] = True
        results.append(result)
    return results


def _write_passing_core_artifacts(
    results_dir: Path,
    prepared: dict[str, Any],
) -> None:
    selected_ids = list(prepared["selected_case_ids"])
    bindings = prepared["artifact_bindings"]
    profile = bindings["profile"]
    source = bindings["source_dataset"]
    dataset = bindings["core_dataset"]
    ledger = bindings["selection_ledger"]
    metrics = {
        "evaluator_version": EVALUATOR_VERSION,
        "total": 27,
        "passed": 27,
        "run_passed": True,
    }
    manifest = {
        "evaluator_version": EVALUATOR_VERSION,
        "order_profile": "core-v1",
        "dataset": Path(dataset["path"]).name,
        "selected_case_ids": selected_ids,
        "executed_case_ids": selected_ids,
        "declared_case_count": 27,
        "executed_case_count": 27,
        "artifact_bindings": bindings,
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "profile_path": profile["path"],
        "profile_sha256": profile["sha256"],
        "source_dataset_path": source["path"],
        "source_dataset_sha256": source["sha256"],
        "dataset_path": dataset["path"],
        "dataset_sha256": dataset["sha256"],
        "selection_ledger_path": ledger["path"],
        "selection_ledger_sha256": ledger["sha256"],
        "profile_contract_verified": True,
        "cross_tenant_fixture": {"prepared": True},
        "independent_approver": {
            "provisioned": True,
            "cleanup": {"succeeded": True},
        },
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "order_metrics.json").write_text(
        json.dumps(metrics),
        encoding="utf-8",
    )
    (results_dir / "order_run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def test_diagnostic_subset_preserves_frozen_order_and_rejects_invalid_ids() -> None:
    prepared = run_eval.prepare_order_core_profile()

    selected = run_eval.select_order_core_diagnostic_cases(
        prepared,
        ["ORD-080", "ORD-074", "ORD-063"],
    )

    assert selected["selected_case_ids"] == ["ORD-074", "ORD-063", "ORD-080"]
    assert [case["case_id"] for case in selected["cases"]] == selected[
        "selected_case_ids"
    ]
    assert selected["partial_case_selection"] is True
    assert selected["artifact_bindings"] == prepared["artifact_bindings"]

    with pytest.raises(ValueError, match="must be unique"):
        run_eval.select_order_core_diagnostic_cases(
            prepared,
            ["ORD-074", "ORD-074"],
        )
    with pytest.raises(ValueError, match="unknown order core case"):
        run_eval.select_order_core_diagnostic_cases(prepared, ["ORD-999"])


def test_runtime_preflight_and_evidence_gate_bind_the_exact_core(tmp_path: Path) -> None:
    prepared = run_eval.prepare_order_core_profile()
    results = _passing_core_results(prepared["cases"])
    _write_passing_core_artifacts(tmp_path, prepared)

    assert prepared["selected_case_ids"] == [case["case_id"] for case in prepared["cases"]]
    assert (
        run_eval.assess_order_evidence_gate(
            results,
            tmp_path,
            require_full_dataset=False,
            expected_case_ids=prepared["selected_case_ids"],
            expected_artifact_bindings=prepared["artifact_bindings"],
        )
        == []
    )

    manifest_path = tmp_path / "order_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profile_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reasons = run_eval.assess_order_evidence_gate(
        results,
        tmp_path,
        require_full_dataset=False,
        expected_case_ids=prepared["selected_case_ids"],
        expected_artifact_bindings=prepared["artifact_bindings"],
    )
    assert "order core manifest profile_sha256 binding mismatch" in reasons


def test_runtime_core_profile_rejects_limit_before_execution() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        run_eval.prepare_order_core_profile(limit=1)


async def test_main_rejects_core_limit_before_login_or_live_work() -> None:
    assert (
        await run_eval.main(
            "order",
            "core-limit-refused",
            limit=1,
            order_profile="core-v1",
        )
        == 2
    )

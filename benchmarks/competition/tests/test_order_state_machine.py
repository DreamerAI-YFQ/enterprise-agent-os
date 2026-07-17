"""Contract tests for the evidence-backed order evaluator."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

COMPETITION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPETITION_ROOT))

from runners import cleanup_order_run, run_eval  # noqa: E402
from runners.cleanup_order_run import load_session_ids  # noqa: E402
from runners.order_state_machine import (  # noqa: E402
    EVALUATOR_VERSION,
    ORDER_PILOT_V1_CASE_IDS,
    OrderStateMachineEvaluator,
    _approval_event,
    _failed_write_audit_linked,
    _foreign_fixture_tenant_uuid,
    _merge_scoped_record_ids,
    _write_outcome,
    business_state_unchanged,
    case_strategy,
    collect_created_order_ids,
    compute_stateful_order_metrics,
    finalize_cleanup_artifacts,
    governance_state_unchanged,
    select_order_cases,
    stateful_case_evidence_verified,
)


def _case(number: int, outcome: str, role: str = "admin") -> dict[str, Any]:
    return {
        "case_id": f"ORD-{number:03d}",
        "description": "test",
        "input": {
            "customer_code": "CUS-001",
            "product_sku": "PRD-001",
            "quantity": 5,
        },
        "expected_outcome": outcome,
        "category": "test",
        "user_role": role,
    }


@pytest.mark.parametrize(
    ("number", "outcome", "strategy"),
    [
        (1, "success", "governed_write"),
        (31, "approval_required", "governed_write"),
        (61, "rejected", "unauthorized_zero_effect"),
        (81, "rejected", "cross_tenant_zero_effect"),
        (101, "rejected", "approval_forgery_zero_effect"),
        (121, "rejected", "sql_injection_zero_effect"),
        (141, "idempotent_skip", "idempotent_retry"),
        (161, "rolled_back", "controlled_compensation"),
    ],
)
def test_frozen_case_ranges_select_explicit_strategy(
    number: int,
    outcome: str,
    strategy: str,
) -> None:
    assert case_strategy(_case(number, outcome)) == strategy


def test_case_strategy_rejects_dataset_semantic_drift() -> None:
    with pytest.raises(ValueError, match="not covered"):
        case_strategy(_case(141, "success"))


def test_pilot_profile_uses_six_fixed_cases_and_formal_strategies() -> None:
    cases = run_eval.load_dataset("order_tasks_v1.yaml")

    selected = select_order_cases(cases, profile="pilot-v1", limit=None)

    assert tuple(case["case_id"] for case in selected) == ORDER_PILOT_V1_CASE_IDS
    assert [case_strategy(case) for case in selected] == [
        "governed_write",
        "governed_write",
        "cross_tenant_zero_effect",
        "sql_injection_zero_effect",
        "idempotent_retry",
        "controlled_compensation",
    ]


def test_pilot_profile_refuses_limit_or_semantic_drift() -> None:
    cases = run_eval.load_dataset("order_tasks_v1.yaml")
    with pytest.raises(ValueError, match="cannot be combined"):
        select_order_cases(cases, profile="pilot-v1", limit=3)

    drifted = [dict(case) for case in cases]
    target = next(case for case in drifted if case["case_id"] == "ORD-141")
    target["expected_outcome"] = "success"
    with pytest.raises(ValueError, match="contract"):
        select_order_cases(drifted, profile="pilot-v1", limit=None)


async def test_cli_contract_rejects_pilot_with_other_suite_or_limit() -> None:
    assert await run_eval.main("rag", "bad-pilot-suite", order_profile="pilot-v1") == 2
    assert (
        await run_eval.main(
            "order",
            "bad-pilot-limit",
            limit=1,
            order_profile="pilot-v1",
        )
        == 2
    )


def test_business_fingerprint_ignores_governance_rows_but_not_erp_mutation() -> None:
    before = {
        "orders_count": 10,
        "orders_digest": "orders-a",
        "inventory_digest": "inventory-a",
        "customers_digest": "customers-a",
        "products_digest": "products-a",
        "approval_count": 0,
        "write_audit_count": 0,
    }
    after_governance = {**before, "approval_count": 1, "write_audit_count": 1}
    assert business_state_unchanged(before, after_governance) is True

    after_business_write = {**after_governance, "orders_count": 11}
    assert business_state_unchanged(before, after_business_write) is False
    assert governance_state_unchanged(before, after_governance) is False


def test_cleanup_rejects_explicit_order_not_linked_to_session_audit() -> None:
    audited = UUID("00000000-0000-0000-0000-000000000801")
    foreign = "00000000-0000-0000-0000-000000000899"

    assert _merge_scoped_record_ids([audited], [str(audited)]) == [audited]
    with pytest.raises(ValueError, match="not linked"):
        _merge_scoped_record_ids([audited], [foreign])


def test_foreign_fixture_uuid_is_bound_to_run_id() -> None:
    first = _foreign_fixture_tenant_uuid("order-run-a")
    assert first == _foreign_fixture_tenant_uuid("order-run-a")
    assert first != _foreign_fixture_tenant_uuid("order-run-b")


def test_created_order_ids_require_structured_write_evidence_not_snapshot_delta() -> None:
    created = "00000000-0000-0000-0000-000000000801"
    results = [
        {
            "business_state_before": {"order_ids": []},
            "business_state_after": {
                "order_ids": ["00000000-0000-0000-0000-000000000899"]
            },
            "steps": [
                {
                    "evidence": {
                        "audit": {
                            "tool_name": "erp_create_sales_order",
                            "resource": "orders",
                            "operation": "create",
                            "after_state": {"id": created},
                        }
                    }
                }
            ],
        }
    ]

    assert collect_created_order_ids(results) == [created]


def test_structured_extractors_do_not_accept_agent_self_report() -> None:
    self_report = {
        "events": [
            {
                "type": "final",
                "content": (
                    "Success. approval_required; audit_id=fake; "
                    "the order was rolled back and idempotency passed."
                ),
            }
        ]
    }
    assert _approval_event(self_report) is None
    assert _write_outcome(self_report) is None


def test_structured_extractors_require_real_event_payloads() -> None:
    stream = {
        "events": [
            {
                "type": "approval_required",
                "metadata": {
                    "approval_id": "00000000-0000-0000-0000-000000000901",
                    "tool_name": "erp_create_sales_order",
                },
            },
            {
                "type": "tool_result",
                "metadata": {
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    '{"success":true,"audit_id":'
                                    '"00000000-0000-0000-0000-000000000902"}'
                                ),
                            }
                        ]
                    }
                },
            },
        ]
    }
    assert _approval_event(stream)["tool_name"] == "erp_create_sales_order"
    assert _write_outcome(stream)["success"] is True


def test_failed_outcome_requires_exact_approval_session_and_audit_link() -> None:
    approval_id = "00000000-0000-0000-0000-000000000901"
    session_id = "00000000-0000-0000-0000-000000000902"
    audit_id = "00000000-0000-0000-0000-000000000903"
    outcome = {
        "success": False,
        "audit_id": audit_id,
        "approval_id": approval_id,
    }
    audit = {
        "id": audit_id,
        "success": False,
        "rolled_back": False,
        "approval_id": approval_id,
        "session_id": session_id,
        "tool_name": "erp_create_sales_order",
        "resource": "orders",
        "operation": "create",
    }

    assert _failed_write_audit_linked(
        outcome,
        [audit],
        approval_id=approval_id,
        session_id=session_id,
    )
    assert not _failed_write_audit_linked(
        outcome,
        [{**audit, "session_id": "00000000-0000-0000-0000-000000000999"}],
        approval_id=approval_id,
        session_id=session_id,
    )


class _SnapshotStore:
    def __init__(self, snapshots: list[dict[str, Any]]) -> None:
        self.snapshots = list(snapshots)

    async def snapshot(self) -> dict[str, Any]:
        return self.snapshots.pop(0)

    async def approval(self, _approval_id: str) -> None:
        return None

    async def session_evidence(self, _session_id: str) -> dict[str, Any]:
        return {"approvals": [], "audits": []}

    async def order(self, _record_id: str) -> None:
        return None


def _fingerprint() -> dict[str, Any]:
    return {
        "orders_count": 3,
        "orders_digest": "orders",
        "inventory_digest": "inventory",
        "customers_digest": "customers",
        "products_digest": "products",
        "approval_count": 0,
        "write_audit_count": 0,
    }


async def test_unauthorized_case_requires_http_denial_and_zero_business_effect() -> None:
    store = _SnapshotStore([_fingerprint(), _fingerprint()])
    invoke = AsyncMock(
        return_value={
            "status_code": 401,
            "session_id": None,
            "session_ids": [],
            "events": [],
            "final_content": None,
            "error": "HTTP 401",
        }
    )
    evaluator = OrderStateMachineEvaluator(
        client=MagicMock(),
        invoke_agent=invoke,
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
    )

    result = await evaluator.evaluate(_case(61, "rejected", role="unauthorized"))

    assert result["case_passed"] is True
    assert result["actual_outcome"] == "rejected"
    assert result["negative_zero_business_side_effect"] is True
    assert invoke.await_args.args[1] == ""


async def test_unauthorized_case_rejects_write_governance_side_effect() -> None:
    before = _fingerprint()
    after = {**before, "approval_count": 1}
    store = _SnapshotStore([before, after])
    invoke = AsyncMock(
        return_value={
            "status_code": 401,
            "session_id": None,
            "session_ids": [],
            "events": [],
            "final_content": None,
            "error": "HTTP 401",
        }
    )
    evaluator = OrderStateMachineEvaluator(
        client=MagicMock(),
        invoke_agent=invoke,
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
    )

    result = await evaluator.evaluate(_case(61, "rejected", role="unauthorized"))

    assert result["case_passed"] is False
    assert result["negative_zero_business_side_effect"] is True
    assert result["negative_zero_write_governance_side_effect"] is False


async def test_governed_case_cannot_pass_from_success_prose_without_interrupt() -> None:
    store = _SnapshotStore([_fingerprint(), _fingerprint(), _fingerprint()])
    invoke = AsyncMock(
        return_value={
            "status_code": 200,
            "session_id": "00000000-0000-0000-0000-000000000903",
            "session_ids": ["00000000-0000-0000-0000-000000000903"],
            "events": [{"type": "final", "content": "Order created successfully"}],
            "final_content": "Order created successfully",
            "error": None,
        }
    )
    evaluator = OrderStateMachineEvaluator(
        client=MagicMock(),
        invoke_agent=invoke,
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
    )

    result = await evaluator.evaluate(_case(1, "success"))

    assert result["case_passed"] is False
    assert result["actual_outcome"] == "indeterminate"
    assert result["approval_interrupt_verified"] is False


class _LifecycleEvaluator(OrderStateMachineEvaluator):
    async def _submit(  # type: ignore[override]
        self,
        case: dict[str, Any],
        message: str,
        token: str,
        before: dict[str, Any],
    ) -> dict[str, Any]:
        del case, message, token
        session_id = "00000000-0000-0000-0000-000000000910"
        approval_id = "00000000-0000-0000-0000-000000000911"
        self.last_session_id = session_id
        return {
            "ok": True,
            "stream": {"session_ids": [session_id]},
            "stream_summary": {"status_code": 200, "session_id": session_id},
            "approval_event": {
                "approval_id": approval_id,
                "tool_name": "erp_create_sales_order",
            },
            "approval": {"id": approval_id, "status": "pending"},
            "session_id": session_id,
            "session_evidence": {"approvals": [], "audits": []},
            "snapshot": before,
        }

    async def _approve_resume_verify(  # type: ignore[override]
        self,
        submit: dict[str, Any],
        request_token: str,
        resolved_input: dict[str, Any],
        *,
        expect_success: bool,
    ) -> dict[str, Any]:
        del submit, request_token, resolved_input
        assert expect_success is True
        audit_id = "00000000-0000-0000-0000-000000000912"
        record_id = "00000000-0000-0000-0000-000000000913"
        return {
            "ok": True,
            "audit_linked": True,
            "order_matches": True,
            "audit": {
                "id": audit_id,
                "tool_name": "erp_create_sales_order",
                "resource": "orders",
                "operation": "create",
                "after_state": {"id": record_id},
            },
        }


class _NegativeLifecycleEvaluator(_LifecycleEvaluator):
    async def _approve_resume_verify(  # type: ignore[override]
        self,
        submit: dict[str, Any],
        request_token: str,
        resolved_input: dict[str, Any],
        *,
        expect_success: bool,
    ) -> dict[str, Any]:
        del submit, request_token, resolved_input
        assert expect_success is False
        return {
            "ok": True,
            "failed_audits": [{"success": False}],
            "no_successful_audit": True,
            "audit_linked": True,
        }


def _successful_retry_stream(
    session_id: str = "00000000-0000-0000-0000-000000000910",
) -> dict[str, Any]:
    return {
        "status_code": 200,
        "session_id": session_id,
        "session_ids": [session_id],
        "error": None,
        "final_content": "diagnostic text is irrelevant",
        "events": [
            {
                "type": "tool_result",
                "metadata": {
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    '{"success":true,"audit_id":'
                                    '"00000000-0000-0000-0000-000000000912",'
                                    '"approval_id":'
                                    '"00000000-0000-0000-0000-000000000911",'
                                    '"after":{"id":'
                                    '"00000000-0000-0000-0000-000000000913"}}'
                                ),
                            }
                        ]
                    }
                },
            }
        ],
    }


@pytest.mark.parametrize(
    ("retry_session_id", "expected_pass"),
    [
        ("00000000-0000-0000-0000-000000000910", True),
        ("00000000-0000-0000-0000-000000000999", False),
    ],
)
async def test_idempotent_strategy_requires_same_session_audit_and_approval(
    retry_session_id: str,
    expected_pass: bool,
) -> None:
    before = _fingerprint()
    after_write = {**before, "orders_count": 4, "orders_digest": "orders-plus-one"}
    store = _SnapshotStore([before, after_write, after_write])
    store.session_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "approvals": [
                {
                    "id": "00000000-0000-0000-0000-000000000911",
                    "status": "consumed",
                    "tool_name": "erp_create_sales_order",
                    "resource": "orders",
                    "operation": "create",
                }
            ],
            "audits": [
                {
                    "id": "00000000-0000-0000-0000-000000000912",
                    "success": True,
                    "rolled_back": False,
                    "approval_id": "00000000-0000-0000-0000-000000000911",
                    "session_id": "00000000-0000-0000-0000-000000000910",
                    "tool_name": "erp_create_sales_order",
                    "resource": "orders",
                    "operation": "create",
                }
            ],
        }
    )
    evaluator = _LifecycleEvaluator(
        client=MagicMock(),
        invoke_agent=AsyncMock(return_value=_successful_retry_stream(retry_session_id)),
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
    )

    result = await evaluator.evaluate(_case(141, "idempotent_skip"))

    assert result["case_passed"] is expected_pass
    assert result["actual_outcome"] == (
        "idempotent_skip" if expected_pass else "indeterminate"
    )
    assert result["idempotency_verified"] is expected_pass
    assert result["steps"][-1]["business_state_unchanged"] is True
    assert result["steps"][-1]["same_session"] is expected_pass


async def test_rollback_strategy_requires_record_absence_audit_and_baseline_restore() -> None:
    before = _fingerprint()
    after_write = {**before, "orders_count": 4, "orders_digest": "orders-plus-one"}
    store = _SnapshotStore([before, after_write, before])
    evaluator = _LifecycleEvaluator(
        client=MagicMock(),
        invoke_agent=AsyncMock(),
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
    )
    evaluator._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value=(200, {"success": True, "rolled_back": True})
    )

    async def final_audit(_path: str, _token: str) -> tuple[int, dict[str, Any]]:
        step_reason = (
            "competition controlled compensation ORD-161; "
            "scenario=inventory_deduct"
        )
        return 200, {
            "id": "00000000-0000-0000-0000-000000000912",
            "success": True,
            "rolled_back": True,
            "rollback_reason": step_reason,
            "approval_id": "00000000-0000-0000-0000-000000000911",
            "session_id": "00000000-0000-0000-0000-000000000910",
            "tool_name": "erp_create_sales_order",
            "resource": "orders",
            "operation": "create",
            "after_state": {
                "id": "00000000-0000-0000-0000-000000000913",
            },
        }

    evaluator._get_json = final_audit  # type: ignore[method-assign]
    case = _case(161, "rolled_back")
    case["input"]["inject_failure"] = "inventory_deduct"

    result = await evaluator.evaluate(case)

    assert result["case_passed"] is True
    assert result["actual_outcome"] == "rolled_back"
    assert result["rollback_verified"] is True
    assert result["steps"][-1]["record_absent"] is True
    assert result["steps"][-1]["business_state_restored"] is True


@pytest.mark.parametrize(("fixture_verified", "case_passed"), [(True, True), (False, False)])
async def test_cross_tenant_case_requires_real_foreign_master_fixture(
    fixture_verified: bool,
    case_passed: bool,
) -> None:
    store = _SnapshotStore([_fingerprint(), _fingerprint()])
    store.cross_tenant_reference_evidence = AsyncMock(  # type: ignore[attr-defined]
        return_value={"verified": fixture_verified}
    )
    evaluator = _NegativeLifecycleEvaluator(
        client=MagicMock(),
        invoke_agent=AsyncMock(),
        store=store,  # type: ignore[arg-type]
        tokens={"admin": "admin-token", "employee": "employee-token"},
        api_base="http://test",
        tenant_slug="acme-corp",
        agent_id="00000000-0000-0000-0000-000000000900",
        approver_token="approver-token",
        aliases={},
        foreign_fixture_tenant_id="00000000-0000-0000-0000-000000000920",
    )
    case = _case(81, "rejected")
    case["input"]["customer_code"] = "G-CUS-001"

    result = await evaluator.evaluate(case)

    assert result["case_passed"] is case_passed
    assert result["negative_zero_business_side_effect"] is True


def test_stateful_metrics_use_case_verdicts_and_evidence_flags() -> None:
    results = [
        {
            "evaluator_version": EVALUATOR_VERSION,
            "case_id": "ORD-001",
            "category": "normal",
            "strategy": "governed_write",
            "case_passed": True,
            "tool_selection_verified": True,
            "approval_interrupt_verified": True,
            "audit_link_verified": True,
            "business_terminal_state_verified": True,
            "expected_outcome_verified": True,
            "steps": [{"name": "verified", "passed": True}],
        },
        {
            "evaluator_version": EVALUATOR_VERSION,
            "case_id": "ORD-141",
            "category": "retry",
            "strategy": "idempotent_retry",
            "case_passed": False,
            "tool_selection_verified": True,
            "approval_interrupt_verified": True,
            "audit_link_verified": True,
            "business_terminal_state_verified": True,
            "idempotency_verified": False,
            "steps": [{"name": "retry", "passed": False}],
        },
    ]

    metrics = compute_stateful_order_metrics(results)

    assert metrics["task_completion_rate"] == 0.5
    assert metrics["outcome_accuracy"] == 0.5
    assert metrics["idempotency_rate"] == 0.0
    assert metrics["run_passed"] is False
    assert metrics["failed_case_ids"] == ["ORD-141"]


def test_stateful_metrics_reject_claimed_pass_without_strategy_evidence() -> None:
    result = {
        "evaluator_version": EVALUATOR_VERSION,
        "case_id": "ORD-141",
        "category": "retry",
        "strategy": "idempotent_retry",
        "case_passed": True,
        "tool_selection_verified": True,
        "approval_interrupt_verified": True,
        "audit_link_verified": True,
        "business_terminal_state_verified": True,
        "idempotency_verified": False,
        "steps": [{"name": "retry", "passed": True}],
    }

    assert stateful_case_evidence_verified(result) is False
    metrics = compute_stateful_order_metrics([result])
    assert metrics["run_passed"] is False
    assert metrics["passed"] == 0
    assert metrics["claimed_pass_without_evidence_case_ids"] == ["ORD-141"]


def test_cleanup_cli_loads_only_unique_result_session_ids(tmp_path: Path) -> None:
    path = tmp_path / "order_results.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"case_id":"ORD-001","session_id":"00000000-0000-0000-0000-000000000901"}',
                (
                    '{"case_id":"ORD-002","session_ids":'
                    '["00000000-0000-0000-0000-000000000901",'
                    '"00000000-0000-0000-0000-000000000902"]}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert load_session_ids(path) == [
        "00000000-0000-0000-0000-000000000901",
        "00000000-0000-0000-0000-000000000902",
    ]


async def test_cleanup_cli_forwards_manifest_run_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    results = tmp_path / "order_results.jsonl"
    results.write_text(
        '{"case_id":"ORD-001","session_id":'
        '"00000000-0000-0000-0000-000000000901"}\n',
        encoding="utf-8",
    )
    (tmp_path / "order_run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "order-run-a",
                "cross_tenant_fixture": {
                    "tenant_id": str(_foreign_fixture_tenant_uuid("order-run-a")),
                },
            }
        ),
        encoding="utf-8",
    )
    cleanup = AsyncMock(return_value={"succeeded": True})
    monkeypatch.setattr(cleanup_order_run, "cleanup_order_run_state", cleanup)

    assert await cleanup_order_run.main(results, "acme-corp") == 0
    assert cleanup.await_args.args[-1] == "order-run-a"


def test_order_gate_rejects_legacy_or_failed_verdicts(tmp_path: Path) -> None:
    (tmp_path / "order_metrics.json").write_text(
        '{"run_passed":true}',
        encoding="utf-8",
    )
    (tmp_path / "order_run_manifest.json").write_text(
        (
            '{"evaluator_version":"order-state-machine-v2",'
            '"independent_approver":{"cleanup":{"succeeded":true}}}'
        ),
        encoding="utf-8",
    )
    passing = [
        {
            "case_id": "ORD-061",
            "evaluator_version": EVALUATOR_VERSION,
            "strategy": "unauthorized_zero_effect",
            "case_passed": True,
            "business_terminal_state_verified": True,
            "negative_zero_business_side_effect": True,
            "negative_zero_write_governance_side_effect": True,
            "steps": [{"name": "unauthenticated_request", "passed": True}],
        }
    ]
    assert run_eval.assess_order_evidence_gate(
        passing,
        tmp_path,
        require_full_dataset=False,
    ) == []
    assert run_eval.assess_order_evidence_gate(
        passing,
        tmp_path,
        require_full_dataset=False,
        expected_case_ids=["ORD-061"],
    ) == []

    coverage_reasons = run_eval.assess_order_evidence_gate(
        passing,
        tmp_path,
        require_full_dataset=False,
        expected_case_ids=["ORD-061", "ORD-031"],
    )
    assert any("profile case coverage mismatch" in reason for reason in coverage_reasons)

    claimed_without_evidence = [
        {
            "case_id": "ORD-061",
            "evaluator_version": EVALUATOR_VERSION,
            "case_passed": True,
        }
    ]
    evidence_reasons = run_eval.assess_order_evidence_gate(
        claimed_without_evidence,
        tmp_path,
        require_full_dataset=False,
    )
    assert any("lack required evidence" in reason for reason in evidence_reasons)

    legacy = [{"case_id": "ORD-001", "actual_outcome": "success"}]
    reasons = run_eval.assess_order_evidence_gate(
        legacy,
        tmp_path,
        require_full_dataset=False,
    )
    assert any("failed state verification" in reason for reason in reasons)
    assert any("lack order-state-machine-v2 evidence" in reason for reason in reasons)


def test_cleanup_finalizer_refreshes_result_hashes_and_adds_receipt(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    evidence_dir = tmp_path / "evidence"
    results_dir.mkdir()
    evidence_dir.mkdir()
    (results_dir / "order_metrics.json").write_text(
        '{"run_passed":true}',
        encoding="utf-8",
    )
    (evidence_dir / "manifest.json").write_text(
        '{"benchmark_results":[],"artifacts":[]}',
        encoding="utf-8",
    )

    receipt_path = finalize_cleanup_artifacts(
        results_dir,
        evidence_dir,
        {"succeeded": True, "baseline_restored": True},
    )
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))

    assert receipt_path.name == "order_cleanup_receipt.json"
    assert manifest["post_export_order_cleanup"]["succeeded"] is True
    assert len(manifest["benchmark_results"]) == 1
    assert any(
        artifact.get("kind") == "post_export_order_cleanup_receipt"
        for artifact in manifest["artifacts"]
    )

"""Tests for ApprovalGateImpl (HITL) and EvolutionGovernorImpl (RL pipeline)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from eaos.core.errors import EvolutionError, NotFoundError
from eaos.harness.context import GuardContext
from eaos.harness.evolution.approval import ApprovalGateImpl, ApprovalRequest
from eaos.harness.evolution.governor import (
    EvolutionGovernorImpl,
    EvolutionStage,
    _next_stage,
)

# ============================================================
# Fakes
# ============================================================


class FakeApprovalDb:
    """In-memory ApprovalDb with configurable fetch results."""

    def __init__(
        self,
        one_row: dict[str, Any] | None = None,
        many_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._one = one_row
        self._many = many_rows or []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._one

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return list(self._many)

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


class FakeEvolutionDb:
    """In-memory EvolutionDb with configurable fetch_one and execute recording."""

    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._row

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _ctx(
    *,
    attributes: dict[str, Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        attributes=attributes or {"session_id": uuid4()},
    )


def _approval_row(
    *,
    status: str = "pending",
    skill_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "agent_id": uuid4(),
        "skill_id": skill_id,
        "session_id": uuid4(),
        "reason": "high_risk",
        "status": status,
        "requested_by": uuid4(),
        "decided_by": None,
        "decided_at": None,
        "created_at": datetime.now(UTC),
    }


# ============================================================
# ApprovalGateImpl tests
# ============================================================


class TestRequestApproval:
    async def test_writes_and_returns_id(self) -> None:
        db = FakeApprovalDb()
        gate = ApprovalGateImpl(db)
        ctx = _ctx()

        approval_id = await gate.request_approval(
            ctx, skill_id=uuid4(), reason="high_risk"
        )

        assert isinstance(approval_id, UUID)
        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.approvals" in sql
        assert params[0] == approval_id
        assert params[1] == ctx.tenant_id
        assert params[2] == ctx.agent_id
        assert params[5] == "high_risk"
        assert params[6] == ctx.user_id

    async def test_requires_session_id(self) -> None:
        db = FakeApprovalDb()
        gate = ApprovalGateImpl(db)
        ctx = GuardContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="personal",
            attributes={},  # no session_id
        )

        with pytest.raises(ValueError, match="session_id"):
            await gate.request_approval(ctx, skill_id=uuid4(), reason="high_risk")

    async def test_skill_id_none_is_allowed(self) -> None:
        db = FakeApprovalDb()
        gate = ApprovalGateImpl(db)

        approval_id = await gate.request_approval(
            _ctx(), skill_id=None, reason="cost_threshold"
        )

        assert isinstance(approval_id, UUID)
        _, params = db.executed[0]
        assert params[3] is None  # skill_id param


class TestCheckApproval:
    async def test_returns_correct_status(self) -> None:
        db = FakeApprovalDb(one_row={"status": "approved"})
        gate = ApprovalGateImpl(db)

        status = await gate.check_approval(uuid4(), uuid4())

        assert status == "approved"

    async def test_raises_not_found_when_missing(self) -> None:
        db = FakeApprovalDb(one_row=None)
        gate = ApprovalGateImpl(db)

        with pytest.raises(NotFoundError, match="approval"):
            await gate.check_approval(uuid4(), uuid4())


class TestApprove:
    async def test_updates_status_to_approved(self) -> None:
        db = FakeApprovalDb()
        gate = ApprovalGateImpl(db)
        approval_id = uuid4()
        approver = uuid4()

        await gate.approve(approval_id, approver)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "UPDATE harness.approvals" in sql
        assert "status = 'approved'" in sql
        assert params[0] == approver  # decided_by
        assert params[2] == approval_id


class TestReject:
    async def test_updates_status_to_rejected(self) -> None:
        db = FakeApprovalDb()
        gate = ApprovalGateImpl(db)
        approval_id = uuid4()
        approver = uuid4()

        await gate.reject(approval_id, approver, "too risky")

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "status = 'rejected'" in sql
        assert params[0] == approver
        assert params[2] == approval_id


class TestListPending:
    async def test_filters_pending(self) -> None:
        skill_id = uuid4()
        db = FakeApprovalDb(
            many_rows=[
                _approval_row(status="pending", skill_id=skill_id),
                _approval_row(status="pending", skill_id=skill_id),
            ]
        )
        gate = ApprovalGateImpl(db)

        result = await gate.list_pending(uuid4())

        assert len(result) == 2
        assert all(isinstance(r, ApprovalRequest) for r in result)
        assert all(r.status == "pending" for r in result)

    async def test_returns_empty_when_none(self) -> None:
        db = FakeApprovalDb(many_rows=[])
        gate = ApprovalGateImpl(db)

        result = await gate.list_pending(uuid4())

        assert result == []

    async def test_maps_row_fields_correctly(self) -> None:
        skill_id = uuid4()
        row = _approval_row(status="pending", skill_id=skill_id)
        db = FakeApprovalDb(many_rows=[row])
        gate = ApprovalGateImpl(db)

        result = await gate.list_pending(uuid4())

        assert len(result) == 1
        req = result[0]
        assert req.id == row["id"]
        assert req.tenant_id == row["tenant_id"]
        assert req.skill_id == skill_id
        assert req.reason == "high_risk"
        assert req.status == "pending"
        assert req.decided_by is None
        assert req.decided_at is None


# ============================================================
# EvolutionGovernorImpl tests
# ============================================================


class TestSubmitStrategy:
    async def test_inserts_with_safety_benchmark_stage(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)
        strategy_id = uuid4()
        ctx = _ctx()

        await gov.submit_strategy(strategy_id, ctx)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.evolution_strategies" in sql
        assert params[0] == strategy_id
        assert params[1] == ctx.tenant_id
        assert params[3] == "safety_benchmark"  # stage
        assert params[4] == "pending"  # stage_status

    async def test_uses_training_run_id_from_attributes(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)
        strategy_id = uuid4()
        training_run_id = uuid4()
        ctx = _ctx(attributes={"session_id": uuid4(), "training_run_id": training_run_id})

        await gov.submit_strategy(strategy_id, ctx)

        _, params = db.executed[0]
        assert params[2] == training_run_id

    async def test_defaults_training_run_id_to_strategy_id(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)
        strategy_id = uuid4()

        await gov.submit_strategy(strategy_id, _ctx())

        _, params = db.executed[0]
        assert params[2] == strategy_id  # training_run_id defaults to strategy_id


class TestAdvanceStage:
    async def test_raises_not_found_when_strategy_missing(self) -> None:
        db = FakeEvolutionDb(row=None)
        gov = EvolutionGovernorImpl(db)

        with pytest.raises(NotFoundError, match="strategy"):
            await gov.advance_stage(uuid4(), _ctx())

    async def test_raises_when_status_not_passed(self) -> None:
        db = FakeEvolutionDb(
            row={"stage": "safety_benchmark", "stage_status": "pending"}
        )
        gov = EvolutionGovernorImpl(db)

        with pytest.raises(EvolutionError, match="cannot advance"):
            await gov.advance_stage(uuid4(), _ctx())

    async def test_advances_to_perf_compare_after_safety(self) -> None:
        db = FakeEvolutionDb(
            row={"stage": "safety_benchmark", "stage_status": "passed"}
        )
        gov = EvolutionGovernorImpl(db)
        strategy_id = uuid4()

        await gov.advance_stage(strategy_id, _ctx())

        # benchmark auto-run is a stub that passes, so stage should advance
        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "UPDATE harness.evolution_strategies" in sql
        assert params[0] == "perf_compare"
        assert params[1] == "pending"

    async def test_advances_to_shadow_after_perf(self) -> None:
        db = FakeEvolutionDb(
            row={"stage": "perf_compare", "stage_status": "passed"}
        )
        gov = EvolutionGovernorImpl(db)

        await gov.advance_stage(uuid4(), _ctx())

        sql, params = db.executed[0]
        assert params[0] == "shadow"

    async def test_advances_to_approval_after_shadow(self) -> None:
        db = FakeEvolutionDb(
            row={"stage": "shadow", "stage_status": "passed"}
        )
        gov = EvolutionGovernorImpl(db)

        await gov.advance_stage(uuid4(), _ctx())

        sql, params = db.executed[0]
        assert params[0] == "approval"

    async def test_no_advance_when_already_at_full(self) -> None:
        db = FakeEvolutionDb(
            row={"stage": "full", "stage_status": "passed"}
        )
        gov = EvolutionGovernorImpl(db)

        await gov.advance_stage(uuid4(), _ctx())

        assert db.executed == []  # no UPDATE issued


class _FakeGuardrail:
    """Minimal guardrail mock returning a configurable result."""

    def __init__(
        self,
        *,
        passed: bool = True,
        reason: str = "all cases passed",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.passed = passed
        self.reason = reason
        self.details = details or {"total": 5, "passed": 5}
        self.called_with: list[tuple[Any, ...]] = []

    async def safety_benchmark(
        self, strategy_id: Any, tenant_id: Any = None
    ) -> Any:
        self.called_with.append(("safety", strategy_id, tenant_id))
        return self

    async def perf_compare(
        self, strategy_id: Any, baseline_metrics: Any = None
    ) -> Any:
        self.called_with.append(("perf", strategy_id, baseline_metrics))
        return self

    async def load_safety_cases(self, tenant_id: Any = None) -> list[dict[str, Any]]:
        return []


class TestBenchmarks:
    async def test_safety_benchmark_returns_passed_without_guardrail(self) -> None:
        gov = EvolutionGovernorImpl(FakeEvolutionDb())

        result = await gov.run_safety_benchmark(uuid4())

        assert result.passed is True
        assert result.stage == EvolutionStage.SAFETY_BENCHMARK
        assert "stub" not in (result.reason or "")

    async def test_perf_compare_returns_passed_without_guardrail(self) -> None:
        gov = EvolutionGovernorImpl(FakeEvolutionDb())

        result = await gov.run_perf_compare(uuid4())

        assert result.passed is True
        assert result.stage == EvolutionStage.PERF_COMPARE
        assert "stub" not in (result.reason or "")

    async def test_safety_benchmark_delegates_to_guardrail(self) -> None:
        tid = uuid4()
        db = FakeEvolutionDb(row={"tenant_id": tid})
        guardrail = _FakeGuardrail(passed=True, reason="5/5 cases passed")
        gov = EvolutionGovernorImpl(db, guardrail=guardrail)

        sid = uuid4()
        result = await gov.run_safety_benchmark(sid)

        assert result.passed is True
        assert result.stage == EvolutionStage.SAFETY_BENCHMARK
        assert result.reason == "5/5 cases passed"
        assert result.details == {"total": 5, "passed": 5}
        assert guardrail.called_with == [("safety", sid, tid)]

    async def test_safety_benchmark_delegates_failure(self) -> None:
        db = FakeEvolutionDb(row={"tenant_id": uuid4()})
        guardrail = _FakeGuardrail(passed=False, reason="2/5 cases failed")
        gov = EvolutionGovernorImpl(db, guardrail=guardrail)

        result = await gov.run_safety_benchmark(uuid4())

        assert result.passed is False
        assert result.stage == EvolutionStage.SAFETY_BENCHMARK
        assert "2/5" in (result.reason or "")

    async def test_perf_compare_delegates_to_guardrail(self) -> None:
        db = FakeEvolutionDb()
        guardrail = _FakeGuardrail(passed=True, reason="metrics within threshold")
        gov = EvolutionGovernorImpl(db, guardrail=guardrail)

        sid = uuid4()
        result = await gov.run_perf_compare(sid)

        assert result.passed is True
        assert result.stage == EvolutionStage.PERF_COMPARE
        assert result.reason == "metrics within threshold"
        assert guardrail.called_with == [("perf", sid, None)]


class TestStageTransitions:
    async def test_start_shadow_updates_stage(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.start_shadow(uuid4(), traffic_pct=15, duration_hours=48)

        sql, params = db.executed[0]
        assert "UPDATE" in sql
        assert params[0] == "shadow"
        assert params[1] == "passed"

    async def test_request_approval_updates_stage(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.request_approval(uuid4())

        sql, params = db.executed[0]
        assert params[0] == "approval"
        assert params[1] == "pending"

    async def test_approve_advances_to_canary(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)
        approver = uuid4()

        await gov.approve(uuid4(), approver)

        sql, params = db.executed[0]
        assert params[0] == "canary"
        assert params[1] == "pending"

    async def test_reject_sets_status_rejected(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.reject(uuid4(), uuid4(), "metrics degraded")

        sql, params = db.executed[0]
        assert params[0] == "rejected"

    async def test_canary_rollout_updates_stage(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.canary_rollout(uuid4(), stages=[30, 70, 100])

        sql, params = db.executed[0]
        assert params[0] == "canary"
        assert params[1] == "passed"

    async def test_canary_rollout_defaults_stages(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.canary_rollout(uuid4())

        assert len(db.executed) == 1

    async def test_full_release_updates_stage(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.full_release(uuid4())

        sql, params = db.executed[0]
        assert params[0] == "full"
        assert params[1] == "passed"

    async def test_auto_rollback_sets_status_failed(self) -> None:
        db = FakeEvolutionDb()
        gov = EvolutionGovernorImpl(db)

        await gov.auto_rollback(uuid4(), "latency spike")

        sql, params = db.executed[0]
        assert params[0] == "failed"


class TestNextStageHelper:
    def test_safety_to_perf(self) -> None:
        assert _next_stage(EvolutionStage.SAFETY_BENCHMARK) == EvolutionStage.PERF_COMPARE

    def test_perf_to_shadow(self) -> None:
        assert _next_stage(EvolutionStage.PERF_COMPARE) == EvolutionStage.SHADOW

    def test_shadow_to_approval(self) -> None:
        assert _next_stage(EvolutionStage.SHADOW) == EvolutionStage.APPROVAL

    def test_approval_to_canary(self) -> None:
        assert _next_stage(EvolutionStage.APPROVAL) == EvolutionStage.CANARY

    def test_canary_to_full(self) -> None:
        assert _next_stage(EvolutionStage.CANARY) == EvolutionStage.FULL

    def test_full_returns_none(self) -> None:
        assert _next_stage(EvolutionStage.FULL) is None

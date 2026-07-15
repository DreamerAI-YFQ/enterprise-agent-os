"""Verify harness Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses
from uuid import uuid4

from eaos.harness.capability.checker import CapabilityChecker
from eaos.harness.compliance.guard import ComplianceGuard
from eaos.harness.context import GuardContext
from eaos.harness.cost.governor import CostGovernor
from eaos.harness.decorators import guarded
from eaos.harness.evolution.governor import EvolutionGovernor
from eaos.harness.guard import HarnessGuard
from eaos.harness.permission.evaluator import PermissionEvaluator
from eaos.harness.policy import Policy, PolicyEngine
from eaos.harness.quality.guard import QualityGuard


class TestGuardContext:
    def test_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(GuardContext)}
        assert {
            "tenant_id",
            "user_id",
            "agent_id",
            "agent_scope",
            "department_ids",
            "action",
            "resource",
            "resource_id",
            "risk_level",
            "attributes",
        } <= fields

    def test_with_action_returns_new_context(self) -> None:
        ctx = GuardContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="personal",
            department_ids=[],
            action="read",
            resource="agent",
            resource_id=None,
        )
        new_ctx = ctx.with_action("write", "data_source")
        assert new_ctx.action == "write"
        assert new_ctx.resource == "data_source"
        assert new_ctx.tenant_id == ctx.tenant_id


class TestHarnessGuard:
    def test_methods(self) -> None:
        for method in ("guard", "post_guard", "get_capability_boundary"):
            assert hasattr(HarnessGuard, method)


class TestSixPillars:
    def test_capability_checker(self) -> None:
        for method in ("check", "get_boundary"):
            assert hasattr(CapabilityChecker, method)

    def test_permission_evaluator(self) -> None:
        assert hasattr(PermissionEvaluator, "evaluate")

    def test_cost_governor(self) -> None:
        for method in ("check_quota", "consume", "reserve", "degrade"):
            assert hasattr(CostGovernor, method)

    def test_compliance_guard(self) -> None:
        for method in ("pre_check", "post_check", "audit"):
            assert hasattr(ComplianceGuard, method)

    def test_quality_guard(self) -> None:
        assert hasattr(QualityGuard, "evaluate")

    def test_evolution_governor(self) -> None:
        for method in ("submit_strategy", "advance_stage", "auto_rollback"):
            assert hasattr(EvolutionGovernor, method)


class TestPolicy:
    def test_policy_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Policy)}
        assert {"name", "version", "content", "status"} <= fields

    def test_policy_engine_methods(self) -> None:
        for method in ("load", "publish", "rollback", "shadow_mode"):
            assert hasattr(PolicyEngine, method)


class TestGuardedDecorator:
    def test_decorator_exists(self) -> None:
        assert callable(guarded)

    def test_decorator_preserves_function(self) -> None:
        @guarded
        async def my_func(x: int) -> int:
            return x * 2

        assert my_func.__name__ == "my_func"

    def test_decorator_accepts_args(self) -> None:
        @guarded(action="write", resource="data_source", risk_level="high")
        async def my_func(x: int) -> int:
            return x * 2

        assert my_func.__name__ == "my_func"

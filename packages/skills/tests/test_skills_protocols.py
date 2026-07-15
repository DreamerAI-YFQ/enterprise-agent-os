"""Verify skills layer Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.skills.executor import SkillExecutor
from eaos.skills.quality import SkillQualityMetrics, SkillQualityMonitor
from eaos.skills.registry import SkillRegistry
from eaos.skills.resolver import SkillResolver
from eaos.skills.spec import (
    GuardrailConfig,
    RiskLevel,
    SkillCategory,
    SkillResult,
    SkillScope,
    SkillSpec,
)


class TestSpec:
    def test_category_values(self) -> None:
        assert SkillCategory.KNOWLEDGE_API.value == "knowledge_api"
        assert SkillCategory.SYSTEM_OPERATION.value == "system_operation"
        assert SkillCategory.RUNBOOK.value == "runbook"

    def test_scope_values(self) -> None:
        assert SkillScope.PERSONAL.value == "personal"
        assert SkillScope.DEPARTMENT.value == "department"
        assert SkillScope.COMPANY.value == "company"

    def test_risklevel_values(self) -> None:
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"

    def test_guardrailconfig_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(GuardrailConfig)}
        assert {
            "confirm_required",
            "auto_confirm_conditions",
            "notify_channels",
            "rollback_enabled",
        } <= fields

    def test_skillspec_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SkillSpec)}
        assert {
            "id",
            "tenant_id",
            "scope",
            "owner_id",
            "name",
            "display_name",
            "description",
            "category",
            "risk_level",
            "instructions",
            "tools",
            "guardrail",
            "version",
            "status",
        } <= fields

    def test_skillresult_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SkillResult)}
        assert {"success", "output", "cost_tokens", "error"} <= fields


class TestRegistry:
    def test_protocol_methods(self) -> None:
        for method in ("create", "get", "update", "publish", "deprecate", "list_by_tenant"):
            assert hasattr(SkillRegistry, method)


class TestResolver:
    def test_protocol_methods(self) -> None:
        for method in ("resolve_for_user", "resolve_for_agent"):
            assert hasattr(SkillResolver, method)


class TestExecutor:
    def test_protocol_methods(self) -> None:
        assert hasattr(SkillExecutor, "execute")


class TestQuality:
    def test_metrics_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SkillQualityMetrics)}
        assert {
            "skill_id",
            "call_count",
            "success_count",
            "failure_count",
            "failure_rate",
            "adoption_rate",
            "avg_latency_ms",
        } <= fields

    def test_monitor_methods(self) -> None:
        for method in ("record", "get_metrics", "check_auto_deprecate"):
            assert hasattr(SkillQualityMonitor, method)

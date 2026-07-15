"""Verify agent layer Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.agent.ambient import AmbientMonitor, AmbientTrigger
from eaos.agent.dispatcher import AgentDispatcher, AgentScope, CapabilityBoundary
from eaos.agent.memory.engine import MemoryEngine
from eaos.agent.orchestrator import (
    AgentOrchestrator,
    CollaborationMode,
    CollaborationPlan,
    SubTask,
)
from eaos.agent.runner import AgentEvent, AgentRunner
from eaos.agent.runtime.sandbox import CodeResult, CodeSandbox, SandboxConfig, SandboxSession
from eaos.agent.tenant import TenantManager


class TestRunner:
    def test_agentevent_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(AgentEvent)}
        assert {"type", "content", "agent_id", "metadata"} <= fields

    def test_runner_methods(self) -> None:
        for method in ("invoke", "interrupt_and_resume"):
            assert hasattr(AgentRunner, method)


class TestDispatcher:
    def test_scope_values(self) -> None:
        assert AgentScope.PERSONAL.value == "personal"
        assert AgentScope.DEPARTMENT.value == "department"
        assert AgentScope.COMPANY.value == "company"

    def test_capability_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(CapabilityBoundary)}
        assert {
            "allowed_models",
            "allowed_datasources",
            "writable_datasources",
            "allowed_skill_categories",
            "max_task_duration_sec",
            "max_iterations",
        } <= fields

    def test_dispatcher_methods(self) -> None:
        for method in (
            "create_agent",
            "get",
            "resolve_agent_for_user",
            "list_available",
            "assign_skill",
            "update_capability",
        ):
            assert hasattr(AgentDispatcher, method)


class TestOrchestrator:
    def test_collab_modes(self) -> None:
        assert CollaborationMode.SINGLE.value == "single"
        assert CollaborationMode.RELAY.value == "relay"
        assert CollaborationMode.FAN_OUT_IN.value == "fan_out_in"

    def test_subtask_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SubTask)}
        assert {
            "task_id",
            "description",
            "assigned_agent_id",
            "input",
            "depends_on",
            "timeout",
        } <= fields

    def test_plan_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(CollaborationPlan)}
        assert {"mode", "subtasks", "aggregator_agent_id", "initial_input", "depth"} <= fields

    def test_orchestrator_methods(self) -> None:
        assert hasattr(AgentOrchestrator, "execute")


class TestMemoryEngine:
    def test_methods(self) -> None:
        for method in (
            "recall",
            "store",
            "consolidate_session",
            "promote_to_department",
            "promote_to_enterprise",
        ):
            assert hasattr(MemoryEngine, method)


class TestSandbox:
    def test_config_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SandboxConfig)}
        assert {
            "level",
            "cpu_limit",
            "memory_limit_mb",
            "timeout_sec",
            "network_enabled",
            "filesystem_rw",
        } <= fields

    def test_coderesult_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(CodeResult)}
        assert {"stdout", "stderr", "exit_code", "duration_ms"} <= fields

    def test_sandbox_session_methods(self) -> None:
        for method in ("run_code", "run_command", "write_file", "read_file", "close"):
            assert hasattr(SandboxSession, method)

    def test_code_sandbox_methods(self) -> None:
        assert hasattr(CodeSandbox, "session")


class TestAmbient:
    def test_trigger_values(self) -> None:
        assert AmbientTrigger.THRESHOLD.value == "threshold"
        assert AmbientTrigger.STALE_TASK.value == "stale_task"
        assert AmbientTrigger.NEW_EVENT.value == "new_event"

    def test_monitor_methods(self) -> None:
        for method in ("check_and_notify", "register_trigger"):
            assert hasattr(AmbientMonitor, method)


class TestTenantManager:
    def test_protocol_exists(self) -> None:
        assert TenantManager is not None

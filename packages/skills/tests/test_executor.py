"""Tests for SkillExecutorImpl — LLM path, sandbox path, tool bindings, guardrail, failure."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from eaos.agent.runtime.sandbox import CodeResult
from eaos.core.context import TenantContext
from eaos.data.mcp.types import McpTool, McpToolResult
from eaos.infra.llm.base import LLMResponse
from eaos.skills.executor import SkillExecutorImpl
from eaos.skills.spec import (
    GuardrailConfig,
    RiskLevel,
    SkillCategory,
    SkillScope,
    SkillSpec,
    ToolBinding,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _spec(
    *,
    category: SkillCategory = SkillCategory.DATA_ANALYSIS,
    tools: list[str] | None = None,
    guardrail: GuardrailConfig | None = None,
    tool_bindings: list[ToolBinding] | None = None,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> SkillSpec:
    return SkillSpec(
        id=uuid4(),
        tenant_id=uuid4(),
        scope=SkillScope.PERSONAL,
        owner_id=uuid4(),
        name="skill-x",
        display_name="Skill X",
        description="desc",
        category=category,
        risk_level=risk_level,
        instructions="print('hi')",
        tools=tools or [],
        tool_bindings=tool_bindings or [],
        guardrail=guardrail,
    )


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
    )


class TestLLMExecution:
    async def test_llm_path_returns_content_and_tokens(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="answer", prompt_tokens=10, completion_tokens=5)
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(_spec(), {"q": "hello"}, _ctx())

        assert result.success is True
        assert result.output == "answer"
        assert result.cost_tokens == 15
        assert result.error is None

    async def test_llm_path_records_quality(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="x")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        executor = SkillExecutorImpl(llm, monitor)

        await executor.execute(_spec(), {}, _ctx())

        monitor.record.assert_awaited_once()
        record_args = monitor.record.call_args.args
        assert record_args[2] is True  # success

    async def test_llm_failure_captures_error(self) -> None:
        llm = AsyncMock()
        llm.chat.side_effect = RuntimeError("LLM down")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(_spec(), {}, _ctx())

        assert result.success is False
        assert "LLM down" in (result.error or "")
        # quality recorded with success=False
        monitor.record.assert_awaited_once()
        assert monitor.record.call_args.args[2] is False


class TestSandboxExecution:
    @staticmethod
    def _make_sandbox(run_code_result: CodeResult) -> MagicMock:
        session_mock = AsyncMock()
        session_mock.run_code.return_value = run_code_result

        @asynccontextmanager
        async def _session(*_args: object, **_kwargs: object) -> AsyncIterator[AsyncMock]:
            yield session_mock

        sandbox = MagicMock()
        sandbox.session = _session
        return sandbox

    async def test_sandbox_path_runs_code(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        sandbox = self._make_sandbox(
            CodeResult(stdout="42\n", stderr="", exit_code=0, duration_ms=10)
        )
        executor = SkillExecutorImpl(llm, monitor, sandbox=sandbox)

        result = await executor.execute(_spec(tools=["code_execution"]), {"x": 1}, _ctx())

        assert result.success is True
        assert result.output == "42\n"
        llm.chat.assert_not_awaited()

    async def test_sandbox_nonzero_exit_raises(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        sandbox = self._make_sandbox(
            CodeResult(stdout="", stderr="boom", exit_code=1, duration_ms=5)
        )
        executor = SkillExecutorImpl(llm, monitor, sandbox=sandbox)

        result = await executor.execute(_spec(tools=["code_execution"]), {}, _ctx())

        assert result.success is False
        assert "boom" in (result.error or "")


class TestGuardrail:
    async def test_confirmation_required_skill_fails_closed(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="ok")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        executor = SkillExecutorImpl(llm, monitor)

        spec = _spec(
            category=SkillCategory.SYSTEM_OPERATION,
            guardrail=GuardrailConfig(confirm_required=True),
        )
        result = await executor.execute(spec, {}, _ctx())

        assert result.success is False
        assert "HITL" in (result.error or "")
        assert result.metadata is not None
        assert result.metadata.get("needs_confirmation") is True
        llm.chat.assert_not_awaited()

    async def test_non_production_no_confirmation_tag(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="ok")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(_spec(), {}, _ctx())
        assert result.metadata == {}

    async def test_high_risk_non_production_skill_fails_closed(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(
            _spec(risk_level=RiskLevel.HIGH),
            {},
            _ctx(),
        )

        assert result.success is False
        assert "HITL" in (result.error or "")
        assert result.metadata == {"needs_confirmation": True}
        llm.chat.assert_not_awaited()

    async def test_production_skill_without_guardrail_fails_closed(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(
            _spec(category=SkillCategory.SYSTEM_OPERATION),
            {},
            _ctx(),
        )

        assert result.success is False
        assert "mandatory guardrail" in (result.error or "")
        llm.chat.assert_not_awaited()


class TestAutoDeprecate:
    async def test_check_auto_deprecate_invoked(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="ok")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = True
        executor = SkillExecutorImpl(llm, monitor)

        await executor.execute(_spec(), {}, _ctx())

        monitor.check_auto_deprecate.assert_awaited_once()


class TestToolBindings:
    """T4: tool_bindings execution path — skill becomes an action."""

    @staticmethod
    def _make_registry(
        result: McpToolResult,
        *,
        tool_names: list[str] | None = None,
        write_tools: set[str] | None = None,
    ) -> Any:
        reg: Any = MagicMock()
        reg.call_tool = AsyncMock(return_value=result)
        names = tool_names or [
            "erp.create_order",
            "crm.update_lead",
            "step1",
            "step2",
            "first",
            "second",
        ]
        reg.list_tools = AsyncMock(
            return_value=[McpTool(name=name, description="", input_schema={}) for name in names]
        )
        writes = write_tools or set()
        reg.is_write_tool = MagicMock(side_effect=lambda name: name in writes)
        return reg

    async def test_single_binding_calls_tool(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg = self._make_registry(McpToolResult(content=[{"type": "text", "text": "ok"}]))
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        binding = ToolBinding(
            tool_name="erp.create_order",
            param_mapping={"customer": "customer_id", "amount": "total"},
        )
        result = await executor.execute(
            _spec(tool_bindings=[binding]),
            {"skill_name": "skill-x", "customer": "ACME", "amount": 100},
            _ctx(),
        )

        assert result.success is True
        reg.call_tool.assert_awaited_once()
        # param_mapping applied
        call = reg.call_tool.call_args
        assert call.args[0] == "erp.create_order"
        assert call.args[1] == {"customer_id": "ACME", "total": 100}
        # LLM NOT called
        llm.chat.assert_not_awaited()

    async def test_param_mapping_renames_keys(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg = self._make_registry(McpToolResult(content=[{"type": "text", "text": "done"}]))
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        binding = ToolBinding(
            tool_name="crm.update_lead",
            param_mapping={"lead_name": "name", "score": "lead_score"},
        )
        await executor.execute(
            _spec(tool_bindings=[binding]),
            {"lead_name": "Bob", "score": 85, "extra": "passthrough"},
            _ctx(),
        )

        args = reg.call_tool.call_args.args[1]
        assert args == {"name": "Bob", "lead_score": 85, "extra": "passthrough"}

    async def test_multiple_bindings_sequential(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg: Any = MagicMock()
        reg.list_tools = AsyncMock(
            return_value=[
                McpTool(name="step1", description="", input_schema={}),
                McpTool(name="step2", description="", input_schema={}),
            ]
        )
        reg.is_write_tool = MagicMock(return_value=False)
        reg.call_tool = AsyncMock(
            side_effect=[
                McpToolResult(content=[{"type": "text", "text": "r1"}]),
                McpToolResult(content=[{"type": "text", "text": "r2"}]),
            ]
        )
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        bindings = [
            ToolBinding(tool_name="step1", param_mapping={}),
            ToolBinding(tool_name="step2", param_mapping={}),
        ]
        result = await executor.execute(_spec(tool_bindings=bindings), {}, _ctx())

        assert result.success is True
        assert reg.call_tool.await_count == 2
        # output aggregates both results
        parsed = json.loads(result.output)
        assert len(parsed) == 2
        assert parsed[0]["tool"] == "step1"
        assert parsed[1]["tool"] == "step2"

    async def test_binding_failure_aborts_skill(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg: Any = MagicMock()
        reg.list_tools = AsyncMock(
            return_value=[
                McpTool(name="first", description="", input_schema={}),
                McpTool(name="second", description="", input_schema={}),
            ]
        )
        reg.is_write_tool = MagicMock(return_value=False)
        reg.call_tool = AsyncMock(
            side_effect=[
                McpToolResult(content=[], is_error=True, error_message="denied"),
                McpToolResult(content=[{"type": "text", "text": "should not run"}]),
            ]
        )
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        bindings = [
            ToolBinding(tool_name="first", param_mapping={}),
            ToolBinding(tool_name="second", param_mapping={}),
        ]
        result = await executor.execute(_spec(tool_bindings=bindings), {}, _ctx())

        assert result.success is False
        assert "denied" in (result.error or "")
        # second binding should NOT have run
        assert reg.call_tool.await_count == 1

    async def test_code_execution_priority_over_tool_bindings(self) -> None:
        """Priority: code_execution > tool_bindings."""
        llm = AsyncMock()
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg = self._make_registry(McpToolResult(content=[{"type": "text", "text": "tool"}]))
        # sandbox returns success
        session_mock = AsyncMock()
        session_mock.run_code.return_value = CodeResult(
            stdout="code-ran", stderr="", exit_code=0, duration_ms=5
        )

        @asynccontextmanager
        async def _session(*_a: object, **_kw: object) -> AsyncIterator[AsyncMock]:
            yield session_mock

        sandbox = MagicMock()
        sandbox.session = _session

        executor = SkillExecutorImpl(llm, monitor, sandbox=sandbox, tool_registry=reg)
        binding = ToolBinding(tool_name="erp.create_order", param_mapping={})
        result = await executor.execute(
            _spec(tools=["code_execution"], tool_bindings=[binding]),
            {"x": 1},
            _ctx(),
        )

        assert result.success is True
        assert result.output == "code-ran"
        # tool_registry NOT called
        reg.call_tool.assert_not_awaited()

    async def test_no_tool_bindings_falls_back_to_llm(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="llm-answer")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        reg = self._make_registry(McpToolResult(content=[{"type": "text", "text": "tool"}]))
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        result = await executor.execute(_spec(), {"q": "hello"}, _ctx())

        assert result.success is True
        assert result.output == "llm-answer"
        reg.call_tool.assert_not_awaited()

    async def test_tool_bindings_without_registry_fail_closed(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="llm-fallback")
        monitor = AsyncMock()
        monitor.check_auto_deprecate.return_value = False
        # no tool_registry passed
        executor = SkillExecutorImpl(llm, monitor)

        binding = ToolBinding(tool_name="erp.create_order", param_mapping={})
        result = await executor.execute(_spec(tool_bindings=[binding]), {}, _ctx())

        assert result.success is False
        assert "ToolRegistry" in (result.error or "")
        llm.chat.assert_not_awaited()

    async def test_declared_tool_without_binding_fails_closed(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(
            _spec(tools=["erp_read"]),
            {"resource": "products"},
            _ctx(),
        )

        assert result.success is False
        assert "no executable tool bindings" in (result.error or "")
        llm.chat.assert_not_awaited()

    async def test_missing_required_tool_fails_before_any_call(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        reg = self._make_registry(
            McpToolResult(content=[]),
            tool_names=["available-tool"],
        )
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)
        binding = ToolBinding(tool_name="missing-tool", required=True)

        result = await executor.execute(
            _spec(tool_bindings=[binding]),
            {},
            _ctx(),
        )

        assert result.success is False
        assert "unavailable" in (result.error or "")
        reg.call_tool.assert_not_awaited()

    async def test_write_bound_skill_fails_without_governed_skill_hitl(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        reg = self._make_registry(
            McpToolResult(content=[]),
            tool_names=["erp_create_order"],
            write_tools={"erp_create_order"},
        )
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)
        binding = ToolBinding(tool_name="erp_create_order")

        result = await executor.execute(
            _spec(tool_bindings=[binding]),
            {},
            _ctx(),
        )

        assert result.success is False
        assert "governed Skill HITL" in (result.error or "")
        reg.call_tool.assert_not_awaited()

    async def test_optional_unavailable_binding_is_skipped(self) -> None:
        llm = AsyncMock()
        monitor = AsyncMock()
        reg = self._make_registry(
            McpToolResult(content=[]),
            tool_names=["available-tool"],
        )
        executor = SkillExecutorImpl(llm, monitor, tool_registry=reg)

        result = await executor.execute(
            _spec(tool_bindings=[ToolBinding(tool_name="optional-tool", required=False)]),
            {},
            _ctx(),
        )

        assert result.success is True
        assert json.loads(result.output)[0]["skipped"] is True
        reg.call_tool.assert_not_awaited()


class TestControlInputIsolation:
    async def test_skill_name_is_not_sent_to_llm(self) -> None:
        llm = AsyncMock()
        llm.chat.return_value = LLMResponse(content="ok")
        monitor = AsyncMock()
        executor = SkillExecutorImpl(llm, monitor)

        result = await executor.execute(
            _spec(),
            {"skill_name": "skill-x", "question": "hello"},
            _ctx(),
        )

        assert result.success is True
        messages = llm.chat.call_args.args[0]
        assert json.loads(messages[1].content) == {"question": "hello"}

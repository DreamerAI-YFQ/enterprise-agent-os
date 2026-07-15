"""Skill executor protocol — runs a Skill with input + context."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Protocol

from eaos.infra.llm.base import Message
from eaos.skills.spec import SkillResult, SkillSpec

if TYPE_CHECKING:
    from eaos.agent.runtime.sandbox import CodeSandbox
    from eaos.core.context import TenantContext
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.infra.llm.router import LLMRouter
    from eaos.skills.quality import SkillQualityMonitor


class SkillExecutor(Protocol):
    """Execute a skill with Harness guardrails woven in."""

    async def execute(
        self,
        skill: SkillSpec,
        input: dict[str, Any],
        ctx: TenantContext,
    ) -> SkillResult:
        """Execute skill. HIGH risk triggers human-in-the-loop confirmation."""
        ...


class SkillExecutorImpl:
    """SkillExecutor backed by LLMRouter + optional CodeSandbox + ToolRegistry.

    Execution branches (priority order):
      1. ``code_execution`` tool: run skill.instructions as Python in the sandbox.
      2. ``tool_bindings`` non-empty: route each binding through ToolRegistry,
         which itself enters the WritePipeline for write tools (HITL + audit).
      3. otherwise: call LLM with skill.instructions as system prompt.

    Quality is recorded after every call; auto-deprecation is checked.
    """

    def __init__(
        self,
        llm: LLMRouter,
        monitor: SkillQualityMonitor,
        sandbox: CodeSandbox | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._llm = llm
        self._monitor = monitor
        self._sandbox = sandbox
        self._tool_registry = tool_registry

    async def execute(
        self,
        skill: SkillSpec,
        input: dict[str, Any],
        ctx: TenantContext,
    ) -> SkillResult:
        # Guardrail hook (Phase 3 no-op): production skills with confirm_required
        # would interrupt for human approval; here we proceed and tag metadata.
        # HITL for write tools is handled inside WritePipeline (T3), not here.
        needs_confirmation = (
            skill.requires_guardrail
            and skill.guardrail is not None
            and skill.guardrail.confirm_required
        )

        start = time.perf_counter()
        success = False
        output = ""
        cost_tokens = 0
        error: str | None = None
        metadata: dict[str, Any] = (
            {"needs_confirmation": True} if needs_confirmation else {}
        )

        try:
            if "code_execution" in skill.tools and self._sandbox is not None:
                output, cost_tokens = await self._run_in_sandbox(skill, input, ctx)
            elif skill.tool_bindings and self._tool_registry is not None:
                output, cost_tokens = await self._run_tool_bindings(skill, input, ctx)
            else:
                output, cost_tokens = await self._run_via_llm(skill, input)
            success = True
        except Exception as exc:  # noqa: BLE001 — capture any sandbox/LLM/tool failure.
            error = str(exc)

        latency_ms = int((time.perf_counter() - start) * 1000)

        await self._monitor.record(skill.id, ctx.tenant_id, success, latency_ms)
        await self._monitor.check_auto_deprecate(skill.id, ctx.tenant_id)

        return SkillResult(
            success=success,
            output=output,
            cost_tokens=cost_tokens,
            error=error,
            metadata=metadata,
        )

    async def _run_in_sandbox(
        self,
        skill: SkillSpec,
        input: dict[str, Any],
        ctx: TenantContext,
    ) -> tuple[str, int]:
        """Run skill.instructions as Python code with ``input`` injected."""
        from eaos.agent.runtime.sandbox import SandboxConfig

        config = SandboxConfig(level="process", timeout_sec=60)
        input_json = json.dumps(json.dumps(input, default=str))
        code = f"import json\ninput = json.loads({input_json})\n{skill.instructions}"
        async with self._sandbox.session(config, ctx) as session:  # type: ignore[union-attr]
            result = await session.run_code(code, language="python")
        output = result.stdout
        if result.exit_code != 0:
            raise RuntimeError(result.stderr or f"exit code {result.exit_code}")
        return output, 0

    async def _run_tool_bindings(
        self,
        skill: SkillSpec,
        input: dict[str, Any],
        ctx: TenantContext,
    ) -> tuple[str, int]:
        """Execute tool bindings sequentially via the ToolRegistry.

        For each binding, ``param_mapping`` renames input keys to tool argument
        keys. The ToolRegistry routes the call to the right source (MCP client
        or internal connector); write tools automatically enter the
        WritePipeline (T3) for HITL + audit. Any binding failure aborts the
        whole skill execution.
        """
        assert self._tool_registry is not None  # narrowed by caller
        results: list[dict[str, Any]] = []
        for binding in skill.tool_bindings:
            tool_args: dict[str, Any] = {}
            for skill_param, tool_param in binding.param_mapping.items():
                if skill_param in input:
                    tool_args[tool_param] = input[skill_param]
            # Unmapped input keys pass through under their own name.
            for k, v in input.items():
                if k not in binding.param_mapping and k not in tool_args:
                    tool_args[k] = v
            result = await self._tool_registry.call_tool(
                binding.tool_name, tool_args, ctx.tenant_id
            )
            if result.is_error:
                raise RuntimeError(
                    f"tool '{binding.tool_name}' failed: {result.error_message or 'unknown'}"
                )
            results.append({"tool": binding.tool_name, "content": result.content})
        return json.dumps(results, default=str, ensure_ascii=False), 0

    async def _run_via_llm(
        self,
        skill: SkillSpec,
        input: dict[str, Any],
    ) -> tuple[str, int]:
        """Run skill via LLM with instructions as system prompt."""
        messages = [
            Message(role="system", content=skill.instructions),
            Message(role="user", content=json.dumps(input, default=str)),
        ]
        response = await self._llm.chat(messages, temperature=0.2, task_type="skill")
        return response.content, response.total_tokens


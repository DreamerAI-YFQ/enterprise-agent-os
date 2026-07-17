"""Skill executor protocol — runs a Skill with input + context."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Protocol

from eaos.infra.llm.base import Message
from eaos.skills.spec import RiskLevel, SkillResult, SkillSpec

if TYPE_CHECKING:
    from eaos.agent.runtime.sandbox import CodeSandbox
    from eaos.core.context import TenantContext
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.infra.llm.router import LLMRouter
    from eaos.skills.quality import SkillQualityMonitor


_CONTROL_INPUT_FIELDS = frozenset({"skill_name"})


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
        # Skill selection fields belong to orchestration, never to business
        # tools, sandbox code, or the LLM payload.
        business_input = {
            key: value for key, value in input.items() if key not in _CONTROL_INPUT_FIELDS
        }

        # Skills do not yet have a trusted graph interrupt/resume contract of
        # their own. Any explicit confirmation requirement or HIGH risk must
        # fail closed. Write-bound skills are also rejected below instead of
        # entering ToolRegistry.call_tool's ungoverned read signature.
        needs_confirmation = skill.risk_level == RiskLevel.HIGH or (
            skill.guardrail is not None and skill.guardrail.confirm_required
        )

        start = time.perf_counter()
        success = False
        output = ""
        cost_tokens = 0
        error: str | None = None
        metadata: dict[str, Any] = {"needs_confirmation": True} if needs_confirmation else {}

        try:
            if skill.requires_guardrail and skill.guardrail is None:
                raise PermissionError(
                    "production skill is missing its mandatory guardrail configuration"
                )
            if needs_confirmation:
                raise PermissionError(
                    "high-risk or confirmation-required skill needs HITL; "
                    "governed skill resume is not configured"
                )
            if "code_execution" in skill.tools:
                if self._sandbox is None:
                    raise RuntimeError("code_execution skill requires a configured sandbox")
                output, cost_tokens = await self._run_in_sandbox(skill, business_input, ctx)
            elif skill.tool_bindings:
                if self._tool_registry is None:
                    raise RuntimeError("tool-bound skill requires a configured ToolRegistry")
                available_tools = await self._preflight_tool_bindings(skill, ctx)
                output, cost_tokens = await self._run_tool_bindings(
                    skill,
                    business_input,
                    ctx,
                    available_tools,
                )
            elif [name for name in skill.tools if name != "code_execution"]:
                raise RuntimeError(
                    "skill declares tool dependencies but has no executable tool bindings"
                )
            else:
                output, cost_tokens = await self._run_via_llm(skill, business_input)
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
        available_tools: set[str],
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
            binding_required = binding.required or binding.tool_name in skill.tools
            if binding.tool_name not in available_tools:
                if binding_required:
                    raise RuntimeError(
                        f"required tool dependency is unavailable: {binding.tool_name}"
                    )
                results.append(
                    {
                        "tool": binding.tool_name,
                        "skipped": True,
                        "reason": "optional tool unavailable",
                    }
                )
                continue
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
                message = result.error_message or "unknown"
                if binding_required:
                    raise RuntimeError(f"tool '{binding.tool_name}' failed: {message}")
                results.append(
                    {
                        "tool": binding.tool_name,
                        "skipped": True,
                        "reason": f"optional tool failed: {message}",
                    }
                )
                continue
            results.append({"tool": binding.tool_name, "content": result.content})
        return json.dumps(results, default=str, ensure_ascii=False), 0

    async def _preflight_tool_bindings(
        self,
        skill: SkillSpec,
        ctx: TenantContext,
    ) -> set[str]:
        """Validate all required dependencies before executing any binding."""
        assert self._tool_registry is not None
        catalog = await self._tool_registry.list_tools(ctx.tenant_id)
        available = {tool.name for tool in catalog}

        if any(binding.required and not binding.tool_name for binding in skill.tool_bindings):
            raise RuntimeError("required tool binding has an empty tool name")

        bindings_by_name = {
            binding.tool_name: binding for binding in skill.tool_bindings if binding.tool_name
        }
        required_names = {
            binding.tool_name
            for binding in skill.tool_bindings
            if binding.required and binding.tool_name
        }
        required_names.update(name for name in skill.tools if name != "code_execution")
        missing_bindings = sorted(name for name in required_names if name not in bindings_by_name)
        if missing_bindings:
            raise RuntimeError(
                "required tool dependencies have no binding: " + ", ".join(missing_bindings)
            )
        missing_tools = sorted(name for name in required_names if name not in available)
        if missing_tools:
            raise RuntimeError(
                "required tool dependencies are unavailable: " + ", ".join(missing_tools)
            )

        write_tools = sorted(
            name for name in bindings_by_name if self._tool_registry.is_write_tool(name)
        )
        if write_tools:
            raise PermissionError(
                "write-bound skill requires governed Skill HITL integration: "
                + ", ".join(write_tools)
            )
        return available

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

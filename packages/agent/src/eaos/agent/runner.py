"""Agent runner — LangGraph-based orchestration with Plan-Execute-Reflect paradigm.

The graph nodes: understand -> plan -> route -> execute -> observe -> reflect.
Single-agent tasks run this graph directly; multi-agent collaboration is
handled by AgentOrchestrator which delegates to multiple AgentRunner instances.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from eaos.agent.dispatcher import AgentConfig  # noqa: TC002  (langgraph get_type_hints)
from eaos.core.context import TenantContext  # noqa: TC002  (langgraph get_type_hints)
from eaos.core.errors import PermissionDeniedError
from eaos.data.mcp.types import McpToolResult  # C07: for write tool error handling
from eaos.infra.llm.base import Attachment, Message
from eaos.knowledge.memory.store import Memory, MemoryScope
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from eaos.agent.dispatcher import AgentDispatcher
    from eaos.agent.memory.engine import MemoryEngine
    from eaos.agent.tenant import TenantManager
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.data.mcp.server import EnterpriseMCPServer
    from eaos.data.mcp.types import McpTool
    from eaos.infra.llm.router import LLMRouter
    from eaos.knowledge.engine import KnowledgeEngine
    from eaos.skills.executor import SkillExecutor
    from eaos.skills.resolver import SkillResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentEvent:
    """A streaming event from agent execution."""

    type: str  # token/step/tool_call/tool_result/plan/reflect/final/error
    content: str | None = None
    agent_id: UUID | None = None
    metadata: dict[str, Any] | None = None


class AgentRunner(Protocol):
    """Single-agent runner with Plan-Execute-Reflect paradigm.

    Automatically weaves in: Trace (L6 @traced) and Harness (L7 @guarded).
    Memory recall and consolidation happen inside; caller sees only events.

    ``invoke``/``interrupt_and_resume`` are async generators: calling them
    returns an ``AsyncIterator`` directly (no ``await``). The Protocol
    declares them as sync methods returning ``AsyncIterator`` so that async
    generator impls structurally match.
    """

    def invoke(
        self,
        ctx: TenantContext,
        user_message: str,
        *,
        attachments: list[Attachment] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run agent, streaming events.

        The ctx.thread_id determines LangGraph checkpoint isolation. For
        department shared agents, the same thread_id is reused across users
        enabling relay collaboration.

        ``attachments`` carries optional multimodal content (images/files)
        attached to the user message; adapters merge them into the LLM
        request as image_url / text parts.
        """
        ...

    def interrupt_and_resume(
        self,
        ctx: TenantContext,
        approval: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """Resume after a human-in-the-loop interrupt (high-risk skill)."""
        ...


class _AgentState(TypedDict, total=False):
    """Mutable graph state passed between LangGraph nodes."""

    ctx: TenantContext
    user_message: str
    agent_config: AgentConfig
    thread_id: str
    messages: list[dict[str, Any]]
    paradigm: str  # "plan" (Plan-Execute-Reflect) | "react" (ReAct loop)
    plan_steps: list[dict[str, Any]]
    current_step: int
    tool_results: list[dict[str, Any]]
    memories: list[Memory]
    available_skills: list[Any]  # SkillSpec list for auto-selection + @mention
    forced_skill: str | None  # @mention parsed skill name to force-execute
    iteration: int
    final_output: str | None
    error: str | None
    reflect_done: bool
    reflect_reason: str


def _extract_json_block(content: str) -> str:
    """Extract JSON from LLM output, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        # Drop the opening fence line
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


_DATA_QUERY_KEYWORDS: list[str] = [
    # Connectors / systems
    "erp", "crm",
    # Actions
    "查询", "查", "列出", "列表", "有哪些", "有什么", "查看", "显示", "获取",
    "query", "list", "show", "get", "find", "search",
    # ERP resources
    "产品", "product", "客户", "customer", "顾客", "订单", "order", "库存",
    "inventory", "仓库", "存货",
    # CRM resources
    "商机", "opportunity", "线索", "lead", "活动", "activity", "跟进",
]


def _should_force_mcp(user_message: str) -> bool:
    """Detect data-query intent that must go through MCP tools."""
    text = user_message.lower()
    return any(kw in text for kw in _DATA_QUERY_KEYWORDS)


# C13/Fix-B,C: Write-intent detection — any request that mutates business state
# (create/update/delete orders, customers, inventory, etc.) MUST be routed
# through the MCP write-tool path so the WritePipeline enforces permission
# checks, idempotency, audit logging, and rollback. Without this, the LLM
# planner may pick "direct" and hallucinate "已创建" without invoking the
# write tool, bypassing all security controls.
_WRITE_INTENT_KEYWORDS: list[str] = [
    # Chinese write verbs
    "创建", "新建", "新增", "添加", "增加",
    "下单", "提交", "确认", "批准", "审批",
    "修改", "更新", "编辑", "变更", "调整",
    "删除", "移除", "撤销", "取消", "作废",
    "入库", "出库", "发货", "退货", "结算",
    # English write verbs
    "create", "insert", "add", "new",
    "update", "modify", "edit", "change", "adjust", "set",
    "delete", "remove", "drop", "cancel", "revoke",
    "submit", "confirm", "approve", "place order", "ship",
]

# Read-only query verbs that may co-occur with write nouns but do NOT
# indicate write intent by themselves (e.g. "查询如何创建订单" is a question,
# not a write request). These are used to avoid false positives.
_READ_CONTEXT_KEYWORDS: list[str] = [
    "如何", "怎么", "怎样", "能否", "可以吗", "吗", "是否",
    "什么", "哪些", "区别", "说明", "文档", "手册",
    "查询", "查看", "显示", "列出", "获取",
    "how to", "what is", "what are", "explain", "describe",
]


def _detect_write_intent(user_message: str) -> bool:
    """Detect whether the user is requesting a WRITE/mutation operation.

    Returns True only when a write verb is present AND the message is not a
    read-only question about how to perform a write (e.g. "如何创建订单" is a
    documentation question, not a write request).

    This is the safety gate that forces write requests through the MCP
    write-tool path (WritePipeline) instead of letting the LLM answer
    "directly" and hallucinate success.
    """
    text = user_message.lower()
    has_write_verb = any(kw in text for kw in _WRITE_INTENT_KEYWORDS)
    if not has_write_verb:
        return False
    # If the message is clearly a read-only question (e.g. "如何创建订单？"
    # "创建订单的流程是什么？"), do NOT treat it as a write request.
    has_read_context = any(kw in text for kw in _READ_CONTEXT_KEYWORDS)
    if has_read_context:
        return False
    return True


def _build_skill_catalog(skills: list[Any]) -> str:
    """Build a skill catalog text block for LLM prompt injection."""
    if not skills:
        return "(no skills available)"
    lines = []
    for s in skills:
        scope_tag = f"[{s.scope.value}]" if hasattr(s, "scope") and hasattr(s.scope, "value") else ""
        lines.append(f"- @{s.name}: {s.display_name} — {s.description} {scope_tag}")
    return "\n".join(lines)


def _parse_skill_mentions(text: str, skills: list[Any]) -> list[str]:
    """Extract @skill_name mentions from user text that match known skills."""
    import re

    mentions = re.findall(r"@(\w+)", text)
    skill_names = {s.name for s in skills}
    return [m for m in mentions if m in skill_names]


_PLAN_SYSTEM_PROMPT = (
    "You are a task planner. Analyze the user's request and decide the paradigm.\n"
    'Respond with JSON: {"paradigm": "plan"|"react", "steps": [...]}\n'
    "- \"react\": simple tasks needing 0-1 tool calls (greetings, single queries). "
    "Steps should have one item with action rag|skill|mcp|direct.\n"
    "- \"plan\": complex multi-step tasks with dependencies. Steps is a list of "
    "{id, action, args}.\n"
    'Actions: "rag" (knowledge base / internal docs / policies / product manuals), '
    '"mcp" (live database query AND write operations from ERP/CRM tables), '
    '"skill" (skill-based), "direct" (no tools).\n'
    "IMPORTANT routing rules:\n"
    "- If the user asks about product specs, customer info, order details, inventory, "
    "or any factual data that lives in ERP/CRM DATABASE TABLES, use \"mcp\".\n"
    "- If the user asks about policies, procedures, documentation, knowledge articles, "
    'product manuals, or anything documented in the KNOWLEDGE BASE, use "rag".\n'
    '- If unsure whether info is in DB or knowledge base, try "rag" first.\n'
    "- Only use \"direct\" for greetings, small talk, or questions that clearly "
    "require no external data.\n"
    "WRITE OPERATION RULE (CRITICAL):\n"
    '- If the user requests ANY write/mutation operation (create/update/delete/cancel '
    'an order, customer, product, inventory, etc.), you MUST use "mcp" action so the '
    'request goes through the write tool pipeline (permission check + idempotency + '
    'audit). NEVER use "direct" for write operations — answering "已创建" without '
    'calling the write tool is FORBIDDEN and bypasses all security controls.'
)

_REACT_SYSTEM_PROMPT = (
    "You are a ReAct agent. Given the conversation and observations, decide the "
    "next action.\n"
    'Respond with JSON: {"action": "rag|skill|mcp|direct|done", '
    '"args": {}, "thought": "brief reasoning"}\n'
    '- "done": task complete, set args.answer to the final answer\n'
    '- "rag": query knowledge base, set args.query\n'
    '- "mcp": query database/ERP/CRM via registered tools, OR perform write operations '
    '(create/update/delete orders, customers, etc.). You may leave '
    'args.tool_name empty; the system will select the best tool from the catalog. '
    'Optionally set args.tool_args with resource/filters if you know them.\n'
    '- "skill": execute a skill, set args.skill_name\n'
    '- "direct": answer directly without tools, ONLY for greetings/small talk\n'
    "IMPORTANT: For any data/query request (ERP products, CRM customers, records, "
    "lists), you MUST choose \"mcp\". Do not answer from your own knowledge.\n"
    "WRITE OPERATION RULE (CRITICAL): For any write/mutation request (create/update/"
    "delete/cancel), you MUST choose \"mcp\". NEVER answer \"已创建\" or \"已删除\" "
    "directly — the write MUST go through the MCP write tool so permission, "
    "idempotency, and audit are enforced. Choosing \"direct\" or \"done\" for a "
    "write request is FORBIDDEN."
)

_REFLECT_SYSTEM_PROMPT = (
    "You are a reflection agent. Review the conversation and tool results. "
    'Determine if the user\'s task is complete.\n'
    'Respond with JSON: {"done": true/false, "reason": "brief explanation"}'
)

_TOOL_SELECTION_PROMPT = (
    "You are a tool selection assistant. Based on the user's request and the "
    "available tools below, select the most appropriate tool and generate the "
    "required arguments.\n\n"
    "Available tools:\n{catalog}\n\n"
    "Rules:\n"
    "- If the user asks for a LIST, RECORDS, ROWS or DATA from ERP/CRM, "
    "you MUST use the *_read tool. Set resource to the table name.\n"
    "- Use *_list_resources ONLY when the user asks 'what tables/resources exist' "
    "or when you don't know the resource name.\n"
    "- For *_read, leave filters empty unless the user asked for specific criteria.\n"
    "- If the user asks to CREATE/UPDATE/DELETE/CANCEL an order, customer, "
    "product, or inventory item (a WRITE operation), you MUST select the "
    "matching *_create / *_update / *_delete write tool from the catalog and "
    "fill in the arguments from the user's message (customer_code, product_sku, "
    "quantity, unit_price, etc.). NEVER return an empty tool_name for a write "
    "request — if a write tool exists, select it.\n"
    'Respond with JSON: {{"tool_name": "...", "arguments": {{...}}}}\n'
    'If no tool is suitable, respond with {{"tool_name": "", "arguments": {{}}}}'
)


class LangGraphRunnerImpl:
    """AgentRunner backed by a LangGraph state graph with dual paradigm support.

    The LLM planner decides the paradigm per task:
    - **Plan-Execute-Reflect**: complex multi-step tasks. Graph: understand →
      plan → route → execute → observe → reflect → (loop or END).
    - **ReAct**: simple tasks needing 0-1 tool calls. Graph: understand →
      plan → reason → execute → observe → reason → ... → END.

    All dependencies are injected. Memory consolidation runs fire-and-forget
    after the session ends.
    """

    def __init__(
        self,
        llm: LLMRouter,
        skill_resolver: SkillResolver,
        skill_executor: SkillExecutor,
        knowledge_engine: KnowledgeEngine,
        mcp_server: EnterpriseMCPServer,
        memory_engine: MemoryEngine,
        tenant_manager: TenantManager,
        dispatcher: AgentDispatcher,
        checkpointer: Any = None,
        tool_registry: ToolRegistry | None = None,
        db: Any = None,
    ) -> None:
        self._llm = llm
        self._skill_resolver = skill_resolver
        self._skill_executor = skill_executor
        self._knowledge_engine = knowledge_engine
        self._mcp_server = mcp_server
        self._memory_engine = memory_engine
        self._tenant_manager = tenant_manager
        self._dispatcher = dispatcher
        self._checkpointer: Any = checkpointer if checkpointer is not None else MemorySaver()
        self._tool_registry = tool_registry
        self._db = db
        self._graph = self._build_graph()

    # -- Graph construction ------------------------------------------------

    def _build_graph(self) -> Any:
        graph: Any = StateGraph(_AgentState)
        graph.add_node("understand", self._understand)
        graph.add_node("plan", self._plan)
        graph.add_node("route", self._route)
        graph.add_node("reason", self._reason)
        graph.add_node("skill_node", self._skill_node)
        graph.add_node("rag_node", self._rag_node)
        graph.add_node("mcp_node", self._mcp_node)
        graph.add_node("direct_node", self._direct_node)
        graph.add_node("observe", self._observe)
        graph.add_node("reflect", self._reflect)
        graph.add_edge(START, "understand")
        graph.add_edge("understand", "plan")
        # After plan: route (plan mode) or reason (react mode)
        graph.add_conditional_edges("plan", self._paradigm_edge)
        graph.add_conditional_edges("route", self._route_edge)
        graph.add_conditional_edges("reason", self._reason_edge)
        graph.add_edge("skill_node", "observe")
        graph.add_edge("rag_node", "observe")
        graph.add_edge("mcp_node", "observe")
        graph.add_edge("direct_node", "observe")
        # After observe: reflect (plan) or reason (react)
        graph.add_conditional_edges("observe", self._after_observe_edge)
        graph.add_conditional_edges("reflect", self._reflect_edge)
        return graph.compile(checkpointer=self._checkpointer)

    # -- Nodes -------------------------------------------------------------

    async def _understand(self, state: _AgentState) -> dict[str, Any]:
        ctx = state["ctx"]
        user_message = state["user_message"]

        memories = await self._memory_engine.recall(
            user_message,
            ctx.tenant_id,
            MemoryScope.PERSONAL,
            ctx.user_id,
        )

        # Load user-visible skills (personal + department + company published)
        available_skills: list[Any] = []
        if self._skill_resolver is not None:
            try:
                available_skills = await self._skill_resolver.resolve_for_user(
                    ctx.tenant_id, ctx.user_id
                )
            except Exception:  # noqa: BLE001 — skills are optional, don't break agent
                logger.warning("failed to load skills for user %s", ctx.user_id, exc_info=True)

        # Parse @skill_name mentions for forced skill execution
        forced_skill: str | None = None
        if available_skills:
            mentioned = _parse_skill_mentions(user_message, available_skills)
            if mentioned:
                forced_skill = mentioned[0]

        memory_text = (
            "\n".join(f"- {m.content}" for m in memories)
            if memories
            else "No relevant memories."
        )
        system_prompt = (
            f"You are a helpful enterprise assistant.\n"
            f"Relevant memories:\n{memory_text}"
        )
        messages = list(state.get("messages", []))
        messages.insert(0, {"role": "system", "content": system_prompt})

        # If a skill is @mentioned, force plan mode with a skill step
        if forced_skill:
            return {
                "messages": messages,
                "memories": list(memories),
                "available_skills": available_skills,
                "forced_skill": forced_skill,
                "final_output": None,
                "reflect_done": False,
                "tool_results": [],
                "plan_steps": [
                    {"id": 0, "action": "skill", "args": {"skill_name": forced_skill}}
                ],
                "current_step": 0,
                "iteration": 0,
                "paradigm": "plan",
            }

        # Reset execution state for each new user message so a stale final_output
        # or tool_results from a previous turn cannot short-circuit the graph.
        return {
            "messages": messages,
            "memories": list(memories),
            "available_skills": available_skills,
            "forced_skill": None,
            "final_output": None,
            "reflect_done": False,
            "tool_results": [],
            "plan_steps": [],
            "current_step": 0,
            "iteration": 0,
            "paradigm": "plan",
        }

    async def _plan(self, state: _AgentState) -> dict[str, Any]:
        # If a skill was @mentioned, skip LLM planning — use the forced skill step
        # already set in _understand.
        if state.get("forced_skill"):
            return {
                "paradigm": "plan",
                "plan_steps": state.get("plan_steps", []),
            }

        # Eval hint: mode="rag" forces ReAct + rag action, bypassing the LLM
        # planner (which often misroutes product/customer queries to MCP/ERP).
        ctx = state.get("ctx")
        if ctx is not None and getattr(ctx, "mode", None) == "rag":
            return {
                "paradigm": "react",
                "plan_steps": [{"id": 0, "action": "rag", "args": {}}],
            }

        messages = state.get("messages", [])
        # Inject available skill catalog into the system prompt so the LLM can
        # auto-select action: "skill" with the right args.skill_name.
        skill_catalog = _build_skill_catalog(state.get("available_skills", []))
        system_prompt = _PLAN_SYSTEM_PROMPT + f"\n\nAvailable skills:\n{skill_catalog}"
        llm_messages = [Message(role="system", content=system_prompt)]
        llm_messages.extend(self._to_llm_message(m) for m in messages)
        response = await self._llm.chat(
            llm_messages, task_type="plan", temperature=0.1
        )
        user_message = state.get("user_message", "")
        paradigm, steps = self._parse_plan(response.content, user_message)
        return {"paradigm": paradigm, "plan_steps": steps}

    def _paradigm_edge(self, state: _AgentState) -> str:
        """After plan: route for plan mode, reason for react mode."""
        if state.get("paradigm", "plan") == "react":
            return "reason"
        return "route"

    async def _route(self, state: _AgentState) -> dict[str, Any]:
        # Passthrough node; routing decision is made by _route_edge.
        return {}

    def _route_edge(self, state: _AgentState) -> str:
        steps = state.get("plan_steps", [])
        current = state.get("current_step", 0)
        if current >= len(steps):
            return "direct_node"
        action = steps[current].get("action", "direct")
        if action == "skill":
            return "skill_node"
        if action == "rag":
            return "rag_node"
        if action == "mcp":
            return "mcp_node"
        return "direct_node"

    async def _reason(self, state: _AgentState) -> dict[str, Any]:
        """ReAct reason node: LLM decides next action or declares done."""
        # Eval hint: mode="rag" — first iteration forces rag retrieval so the
        # knowledge base is queried before the LLM is allowed to answer.
        ctx = state.get("ctx")
        if (
            ctx is not None
            and getattr(ctx, "mode", None) == "rag"
            and not state.get("tool_results")
        ):
            return {
                "plan_steps": [{"id": 0, "action": "rag", "args": {}}],
                "current_step": 0,
            }

        messages = state.get("messages", [])
        skill_catalog = _build_skill_catalog(state.get("available_skills", []))
        system_prompt = _REACT_SYSTEM_PROMPT + f"\n\nAvailable skills:\n{skill_catalog}"
        llm_messages = [Message(role="system", content=system_prompt)]
        llm_messages.extend(self._to_llm_message(m) for m in messages)
        response = await self._llm.chat(
            llm_messages, task_type="plan", temperature=0.1
        )
        action, args = self._parse_react(response.content)

        # C13/Fix-B,C: SAFETY OVERRIDE — if the user message has write intent
        # and the LLM tried to answer directly (action=direct/done) WITHOUT
        # having invoked a write tool yet, force "mcp" so the WritePipeline
        # is invoked. This only triggers on the FIRST reason iteration (before
        # any write tool has run); once a write tool outcome is in tool_results,
        # the LLM is allowed to declare "done" and summarize the result.
        # Without this, the LLM would hallucinate "已创建" without calling the
        # write tool, bypassing all security controls.
        user_message = state.get("user_message", "")
        if (
            _detect_write_intent(user_message)
            and action in ("direct", "done")
        ):
            tool_results = state.get("tool_results", [])
            write_already_called = any(
                tr.get("type") == "mcp"
                and (
                    tr.get("tool_name", "").startswith("erp_")
                    or tr.get("tool_name", "").startswith("crm_")
                    or tr.get("is_write")
                )
                for tr in tool_results
            )
            if not write_already_called:
                action = "mcp"
                args = {}
        if action == "done":
            return {
                "final_output": args.get("answer", ""),
                "reflect_done": True,
            }
        return {
            "plan_steps": [{"id": 0, "action": action, "args": args}],
            "current_step": 0,
        }

    def _reason_edge(self, state: _AgentState) -> str:
        """After reason: execute node or END (if done)."""
        if state.get("final_output") is not None:
            return END
        steps = state.get("plan_steps", [])
        if not steps:
            return END
        action = steps[0].get("action", "direct")
        if action == "skill":
            return "skill_node"
        if action == "rag":
            return "rag_node"
        if action == "mcp":
            return "mcp_node"
        return "direct_node"

    def _after_observe_edge(self, state: _AgentState) -> str:
        """After observe: reflect (plan) or reason (react)."""
        if state.get("paradigm", "plan") == "react":
            return "reason"
        return "reflect"

    async def _skill_node(self, state: _AgentState) -> dict[str, Any]:
        ctx = state["ctx"]
        step = self._current_step(state)
        args = step.get("args", {})

        # Use user-visible skills (personal + department + company published)
        # instead of agent-bound skills only, so all three-tier skills are usable.
        skills = state.get("available_skills", [])
        if not skills and self._skill_resolver is not None:
            try:
                skills = await self._skill_resolver.resolve_for_user(
                    ctx.tenant_id, ctx.user_id
                )
            except Exception:  # noqa: BLE001
                logger.warning("failed to load skills in skill_node", exc_info=True)

        skill_name = args.get("skill_name", "") if isinstance(args, dict) else ""
        skill = next(
            (s for s in skills if s.name == skill_name),
            skills[0] if skills else None,
        )
        tool_results = list(state.get("tool_results", []))
        if skill is None:
            tool_results.append({"type": "skill", "error": "no skill available"})
            return {"tool_results": tool_results}

        result = await self._skill_executor.execute(skill, args, ctx)
        tool_results.append(
            {
                "type": "skill",
                "skill_name": skill.name,
                "success": result.success,
                "output": result.output,
                "error": result.error,
            }
        )
        return {"tool_results": tool_results}

    async def _rag_node(self, state: _AgentState) -> dict[str, Any]:
        ctx = state["ctx"]
        step = self._current_step(state)
        args = step.get("args", {})
        query = (
            args.get("query", state.get("user_message", ""))
            if isinstance(args, dict)
            else state.get("user_message", "")
        )

        results = await self._knowledge_engine.search(query, ctx.tenant_id, user_id=ctx.user_id)
        tool_results = list(state.get("tool_results", []))

        # C05/GAP-13: Include citation metadata and no_evidence flag
        if results:
            tool_results.append(
                {
                    "type": "rag",
                    "query": query,
                    "has_evidence": True,
                    "results": [
                        {
                            "content": r.content,
                            "score": r.score,
                            "metadata": r.metadata,  # C05: citation source info
                        }
                        for r in results
                    ],
                }
            )
        else:
            # C05/GAP-13: No evidence found — flag for refusal in _direct_node
            tool_results.append(
                {
                    "type": "rag",
                    "query": query,
                    "has_evidence": False,
                    "results": [],
                }
            )
        return {"tool_results": tool_results}

    async def _mcp_node(self, state: _AgentState) -> dict[str, Any]:
        """MCP tool execution node.

        If ``tool_registry`` is injected (T1), uses the new flow: query the
        tool catalog, let the LLM select the right tool based on available
        tools, then call via ``ToolRegistry``. This fixes gap #2 (tool catalog
        never flows to LLM, causing hallucinated tool names).

        Falls back to the legacy flow (planner-supplied ``tool_name``) when
        ``tool_registry`` is not configured, preserving backward compatibility.
        """
        ctx = state["ctx"]
        step = self._current_step(state)
        args = step.get("args", {})

        if self._tool_registry is not None:
            return await self._mcp_node_with_registry(state, ctx, args)

        return await self._mcp_node_legacy(state, ctx, args)

    async def _mcp_node_with_registry(
        self,
        state: _AgentState,
        ctx: TenantContext,
        plan_args: dict[str, Any],
    ) -> dict[str, Any]:
        """T1 flow: list_tools → LLM selects tool → call_tool via ToolRegistry."""
        registry = self._tool_registry
        if registry is None:
            tool_results = list(state.get("tool_results", []))
            tool_results.append(
                {"type": "mcp", "error": "tool registry not configured"}
            )
            return {"tool_results": tool_results}

        tools = await registry.list_tools(ctx.tenant_id)
        catalog = self._format_tool_catalog(tools)

        user_message = state.get("user_message", "")
        hint = plan_args.get("tool_name", "")

        system_prompt = _TOOL_SELECTION_PROMPT.format(catalog=catalog)
        llm_messages: list[Message] = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_message),
        ]
        if hint:
            llm_messages.append(
                Message(
                    role="system",
                    content=f"Planner hint: suggested tool '{hint}'.",
                )
            )

        response = await self._llm.chat(
            llm_messages, task_type="plan", temperature=0.1
        )
        tool_name, tool_args = self._parse_tool_selection(response.content)

        tool_results = list(state.get("tool_results", []))
        if not tool_name:
            tool_results.append(
                {"type": "mcp", "error": "no suitable tool found in catalog"}
            )
            return {"tool_results": tool_results}

        # Auto-correct: if the user asks for actual records but the LLM picked
        # *_list_resources, redirect to *_read with an inferred resource.
        if tool_name.endswith("_list_resources"):
            connector = tool_name[: -len("_list_resources")]
            resource = self._infer_resource_from_message(user_message, connector)
            if resource:
                tool_name = f"{connector}_read"
                tool_args = {"resource": resource, "limit": 100}

        # When the LLM leaves arguments empty for a read tool, default to a
        # wildcard list query so the user gets actual data.
        if tool_name.endswith("_read") and not tool_args.get("resource"):
            resource = self._infer_resource_from_message(
                user_message, tool_name[: -len("_read")]
            )
            if resource:
                tool_args["resource"] = resource

        # C07: Route write tools through governed WritePipeline
        if registry.is_write_tool(tool_name):
            from uuid import uuid4

            from eaos.core.execution import ToolExecutionContext

            exec_ctx = ToolExecutionContext(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                agent_id=ctx.agent_id,
                session_id=ctx.session_id or uuid4(),
                agent_scope=ctx.agent_scope,
                department_ids=list(ctx.department_ids),
                trace_id=uuid4(),
            )
            exec_ctx.fail_closed(is_write=True)
            try:
                result = await registry.call_write_tool(tool_name, tool_args, exec_ctx)
            except Exception as exc:
                # C13/Fix-3: Distinguish WriteApprovalRequired (HITL gate)
                # from other errors. WriteApprovalRequired means the approval
                # record WAS created in harness.approvals — the caller should
                # handle the interrupt/resume flow, not treat it as a failure.
                exc_name = type(exc).__name__
                if exc_name == "WriteApprovalRequired":
                    # Extract approval_id from the exception
                    approval_id = getattr(exc, "approval_id", None)
                    result = McpToolResult(
                        content=[{"type": "text", "text": (
                            f"approval_required: {exc} "
                            f"(approval_id={approval_id})"
                        )}],
                        is_error=False,  # not an error — it's a pending approval
                        error_message=None,
                    )
                else:
                    # Permission denied, validation error, etc.
                    result = McpToolResult(
                        content=[{"type": "text", "text": str(exc)}],
                        is_error=True,
                        error_message=str(exc),
                    )
        else:
            result = await registry.call_tool(
                tool_name, tool_args, ctx.tenant_id
            )
        tool_results.append(
            {
                "type": "mcp",
                "tool_name": tool_name,
                "is_write": registry.is_write_tool(tool_name),
                "result": {
                    "content": result.content,
                    "is_error": result.is_error,
                },
            }
        )
        return {"tool_results": tool_results}

    async def _mcp_node_legacy(
        self,
        state: _AgentState,
        ctx: TenantContext,
        plan_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Legacy flow: use planner-supplied tool_name with mcp_server."""
        tool_name = plan_args.get("tool_name", "")
        tool_args = plan_args.get("tool_args", {})

        query = tool_args.get("query", state.get("user_message", ""))
        if isinstance(query, str) and query:
            rewritten = await self._knowledge_engine.rewrite_query(
                query, ctx.tenant_id
            )
            tool_args = {**tool_args, "query": rewritten.rewritten}

        result = await self._mcp_server.call_tool(
            tool_name, tool_args, ctx.tenant_id
        )
        tool_results = list(state.get("tool_results", []))
        tool_results.append(
            {"type": "mcp", "tool_name": tool_name, "result": result}
        )
        return {"tool_results": tool_results}

    @staticmethod
    def _infer_resource_from_message(
        message: str, connector: str
    ) -> str | None:
        """Map common Chinese/English query terms to a connector resource."""
        text = message.lower()
        connector_resources: dict[str, dict[str, list[str]]] = {
            "erp": {
                "products": ["产品", "product", "商品", "货品"],
                "customers": ["客户", "customer", "顾客", "客商"],
                "orders": ["订单", "order", "销售单"],
                "inventory": ["库存", "inventory", "存货", "仓库"],
            },
            "crm": {
                "leads": ["线索", "lead", "潜在客户"],
                "opportunities": ["商机", "opportunity", "机会"],
                "activities": ["活动", "activity", "跟进"],
            },
        }
        resources = connector_resources.get(connector, {})
        for resource, keywords in resources.items():
            if any(kw in text for kw in keywords):
                return resource
        return None

    @staticmethod
    def _format_tool_catalog(tools: list[McpTool]) -> str:
        """Format tool list as a readable catalog for the LLM prompt."""
        if not tools:
            return "(no tools available)"
        lines: list[str] = []
        for t in tools:
            lines.append(f"- {t.name}: {t.description}")
            props = t.input_schema.get("properties", {})
            required = t.input_schema.get("required", [])
            for prop_name, prop_schema in props.items():
                req = " (required)" if prop_name in required else ""
                ptype = prop_schema.get("type", "any") if isinstance(prop_schema, dict) else "any"
                lines.append(f"    {prop_name} ({ptype}){req}")
        return "\n".join(lines)

    @staticmethod
    def _parse_tool_selection(content: str) -> tuple[str, dict[str, Any]]:
        """Parse LLM tool selection JSON: ``{"tool_name": "...", "arguments": {...}}``."""
        try:
            parsed = json.loads(_extract_json_block(content))
            tool_name = str(parsed.get("tool_name", ""))
            args = parsed.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            return tool_name, args
        except (json.JSONDecodeError, AttributeError, TypeError):
            return "", {}

    async def _direct_node(self, state: _AgentState) -> dict[str, Any]:
        messages = state.get("messages", [])
        tool_results = state.get("tool_results", [])

        # C13/Fix-B,C: SAFETY NET — if the user message has write intent but
        # no write tool was actually invoked (tool_results is empty or lacks
        # a write-tool outcome), REFUSE to answer directly. This prevents the
        # LLM from hallucinating "已创建/已删除" without going through the
        # WritePipeline (which enforces permission/idempotency/audit).
        # The _parse_plan / _parse_react overrides should already route write
        # requests to mcp_node; this is the last-resort backstop.
        user_message = state.get("user_message", "")
        if _detect_write_intent(user_message):
            has_write_outcome = any(
                tr.get("type") == "mcp"
                and (
                    tr.get("tool_name", "").startswith("erp_")
                    or tr.get("tool_name", "").startswith("crm_")
                    or tr.get("is_write")
                )
                for tr in tool_results
            )
            if not has_write_outcome:
                return {
                    "final_output": (
                        "该操作需要通过企业写操作工具执行（涉及权限校验、幂等性检查和审计日志）。"
                        "请通过标准下单/创建流程提交，系统将自动完成权限审批与操作审计。"
                        "直接由助手生成“已创建”等回复不被允许，因为这会绕过安全管控。"
                    )
                }

        # If the last tool result is a structured read, render it as a Markdown
        # table directly instead of asking the LLM to summarize (which often
        # copies raw JSON).
        if tool_results:
            rows = self._extract_rows_from_tool_result(tool_results[-1])
            if rows is not None:
                return {"final_output": self._rows_to_markdown(rows)}

        system_prompt = (
            "You are a helpful enterprise assistant. Answer the user's request "
            "based on the conversation and any tool observations. "
            "If observations contain structured data (rows, records, lists), "
            "summarize the key information in Chinese using natural language or a "
            "Markdown table. Do NOT return raw JSON. Do NOT make up information "
            "not present in the observations."
        )

        # C05/GAP-13: Citation and no-evidence refusal
        rag_evidence = ""
        has_rag = False
        rag_has_evidence = True
        for tr in tool_results:
            if tr.get("type") == "rag":
                has_rag = True
                if not tr.get("has_evidence", True):
                    rag_has_evidence = False
                else:
                    for i, r in enumerate(tr.get("results", []), 1):
                        meta = r.get("metadata", {})
                        source_info = ""
                        if meta.get("document_id"):
                            source_info = f" (来源: 文档{meta.get('document_id', '')})"
                        elif meta.get("scope"):
                            source_info = f" (来源: {meta.get('scope')})"
                        rag_evidence += f"[{i}] {r.get('content', '')[:500]}{source_info}\n"

        if has_rag:
            if rag_has_evidence and rag_evidence:
                system_prompt += (
                    "\n\n以下是从企业知识库检索到的证据。请基于这些证据回答，"
                    "并在回答末尾标注引用来源编号（如 [1]、[2]）。"
                    "不要添加证据中没有的信息。\n\n"
                    + rag_evidence
                )
            else:
                system_prompt += (
                    "\n\n知识库中未找到相关证据。请礼貌地告知用户"
                    "没有找到相关信息，不要编造答案。"
                )
        elif tool_results:
            system_prompt += (
                " The latest observation contains the tool result; use it as the "
                "primary source for your answer."
            )
        llm_messages = [Message(role="system", content=system_prompt)]
        llm_messages.extend(self._to_llm_message(m) for m in messages)
        response = await self._llm.chat(
            llm_messages, task_type="default", temperature=0.3
        )
        return {"final_output": response.content}

    @staticmethod
    def _extract_rows_from_tool_result(
        tool_result: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        """Extract rows from an MCP read tool result, or None if not applicable."""
        if tool_result.get("type") != "mcp":
            return None
        result = tool_result.get("result", {})
        if result.get("is_error"):
            return None
        content = result.get("content", [])
        if not content or not isinstance(content, list):
            return None
        text = content[0].get("text", "") if isinstance(content[0], dict) else ""
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        rows = payload.get("rows")
        if isinstance(rows, list):
            return rows
        return None

    @staticmethod
    def _rows_to_markdown(rows: list[dict[str, Any]]) -> str:
        """Render rows as a Markdown table (Chinese-friendly)."""
        if not rows:
            return "未查询到数据。"
        exclude = {"id", "tenant_id", "created_at", "updated_at"}
        columns = [k for k in rows[0].keys() if k not in exclude] or list(rows[0].keys())
        columns = columns[:8]

        lines: list[str] = []
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for row in rows[:50]:
            vals: list[str] = []
            for col in columns:
                v = row.get(col)
                if v is None:
                    vals.append("")
                elif isinstance(v, (dict, list)):
                    vals.append(json.dumps(v, ensure_ascii=False))
                else:
                    vals.append(str(v).replace("|", "\\|").replace("\n", " "))
            lines.append("| " + " | ".join(vals) + " |")
        if len(rows) > 50:
            lines.append(f"\n*共 {len(rows)} 条记录，仅显示前 50 条。*")
        return "\n".join(lines)

    async def _observe(self, state: _AgentState) -> dict[str, Any]:
        tool_results = state.get("tool_results", [])
        messages = list(state.get("messages", []))
        if tool_results:
            last = tool_results[-1]
            observation = json.dumps(last, default=str, ensure_ascii=False)
            messages.append(
                {"role": "assistant", "content": f"Observation: {observation}"}
            )
        return {"messages": messages}

    async def _reflect(self, state: _AgentState) -> dict[str, Any]:
        final_output = state.get("final_output")
        if final_output is not None:
            return {"reflect_done": True}

        current_step = state.get("current_step", 0)
        steps = state.get("plan_steps", [])
        force_done = current_step >= len(steps) - 1

        messages = state.get("messages", [])
        llm_messages = [Message(role="system", content=_REFLECT_SYSTEM_PROMPT)]
        llm_messages.extend(self._to_llm_message(m) for m in messages)
        response = await self._llm.chat(
            llm_messages, task_type="reflect", temperature=0.1
        )
        done, reason = self._parse_reflect(response.content)

        if done or force_done:
            # Move to direct_node so the LLM synthesizes a natural-language
            # answer from the observations instead of returning raw JSON.
            return {"reflect_done": True, "reflect_reason": reason}
        return {
            "reflect_done": False,
            "current_step": current_step + 1,
            "iteration": state.get("iteration", 0) + 1,
            "reflect_reason": reason,
        }

    def _reflect_edge(self, state: _AgentState) -> str:
        if state.get("reflect_done", False):
            if state.get("final_output") is not None:
                return END
            return "direct_node"
        agent_config = state.get("agent_config")
        max_iter = (
            agent_config.capability.max_iterations if agent_config else 10
        )
        if state.get("iteration", 0) >= max_iter:
            return END
        return "route"

    # -- Public API (AgentRunner protocol) ---------------------------------

    async def invoke(
        self,
        ctx: TenantContext,
        user_message: str,
        *,
        attachments: list[Attachment] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        try:
            agent_config = await self._dispatcher.get(
                ctx.agent_id, ctx.tenant_id
            )
            thread_id = await self._tenant_manager.resolve_thread_id(
                ctx.tenant_id,
                ctx.agent_id,
                ctx.session_id,
                agent_config.scope,
            )
        except Exception as exc:
            logger.exception("agent execution failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
            return

        # C03: Load conversation history so the LLM has context from
        # previous turns in this session. Without this, each invoke() starts
        # with only the current message, making multi-turn conversations
        # impossible (especially pronoun/ellipsis resolution).
        history_messages: list[dict[str, Any]] = []
        if self._db is not None and ctx.session_id is not None:
            try:
                rows = await self._db.fetch(
                    "SELECT role, content FROM agent.messages "
                    "WHERE session_id = :p0 AND tenant_id = :p1 "
                    "AND role IN ('user', 'assistant') "
                    "AND (event_type IS NULL OR event_type = 'final') "
                    "ORDER BY created_at ASC LIMIT 20",
                    ctx.session_id,
                    ctx.tenant_id,
                )
                history_messages = [
                    {"role": r["role"], "content": r["content"]} for r in rows
                ] if rows else []
            except Exception:  # noqa: BLE001 — history is best-effort
                logger.warning("failed to load history", exc_info=True)

        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id}
        }
        user_msg: dict[str, Any] = {"role": "user", "content": user_message}
        if attachments:
            user_msg["attachments"] = attachments
        # Prepend history to messages so the graph has full conversation context
        all_messages = history_messages + [user_msg]
        initial_state: _AgentState = {
            "ctx": ctx,
            "user_message": user_message,
            "agent_config": agent_config,
            "thread_id": thread_id,
            "messages": all_messages,
            "paradigm": "plan",
            "plan_steps": [],
            "current_step": 0,
            "tool_results": [],
            "memories": [],
            "iteration": 0,
            "final_output": None,
            "error": None,
        }

        final_output: str | None = None
        try:
            async for event in self._graph.astream(
                initial_state, config=config, stream_mode="updates"
            ):
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        continue
                    if update.get("final_output") is not None:
                        final_output = update["final_output"]
                    agent_event = self._translate_event(
                        node_name, update, ctx.agent_id
                    )
                    if agent_event is not None:
                        yield agent_event
        except Exception as exc:
            logger.exception("agent execution failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
            return

        yield AgentEvent(
            type="final",
            content=self._sanitize_output(final_output),
            agent_id=ctx.agent_id,
        )

        if ctx.session_id is not None:
            asyncio.create_task(
                self._memory_engine.consolidate_session(
                    ctx.session_id, ctx.tenant_id, ctx.user_id
                )
            )

    async def interrupt_and_resume(
        self,
        ctx: TenantContext,
        approval: dict[str, Any],
    ) -> AsyncIterator[AgentEvent]:
        """Resume after a human-in-the-loop interrupt (high-risk skill).

        Checks the approval status: if approved, resumes the LangGraph
        execution via Command(resume=...); if rejected, raises
        PermissionDeniedError.
        """
        status = str(approval.get("status", "pending"))
        if status == "rejected":
            raise PermissionDeniedError(
                f"approval {approval.get('id')} was rejected"
            )
        if status != "approved":
            yield AgentEvent(
                type="error",
                content=f"approval status is '{status}', expected 'approved'",
                agent_id=ctx.agent_id,
            )
            return

        try:
            agent_config = await self._dispatcher.get(ctx.agent_id, ctx.tenant_id)
            thread_id = await self._tenant_manager.resolve_thread_id(
                ctx.tenant_id,
                ctx.agent_id,
                ctx.session_id,
                agent_config.scope,
            )
        except Exception as exc:
            logger.exception("agent execution failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
            return

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        final_output: str | None = None

        try:
            async for event in self._graph.astream(
                Command(resume=approval), config=config, stream_mode="updates"
            ):
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        continue
                    if update.get("final_output") is not None:
                        final_output = update["final_output"]
                    agent_event = self._translate_event(
                        node_name, update, ctx.agent_id
                    )
                    if agent_event is not None:
                        yield agent_event
        except Exception as exc:
            logger.exception("agent execution failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
            return

        yield AgentEvent(
            type="final",
            content=self._sanitize_output(final_output),
            agent_id=ctx.agent_id,
        )

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _to_llm_message(m: dict[str, Any]) -> Message:
        """Convert a state message dict to a Message, preserving attachments."""
        return Message(
            role=m["role"],
            content=m["content"],
            attachments=m.get("attachments"),
        )

    @staticmethod
    def _parse_plan(
        content: str, user_message: str = ""
    ) -> tuple[str, list[dict[str, Any]]]:
        """Parse LLM plan output: (paradigm, steps).

        - react mode: steps may be empty (reason node decides actions dynamically)
        - plan mode: steps must be non-empty; otherwise fall back to direct
        """
        try:
            parsed = json.loads(_extract_json_block(content))
            paradigm = str(parsed.get("paradigm", "plan"))
            if paradigm not in ("plan", "react"):
                paradigm = "plan"
            steps = parsed.get("steps", [])
            if not isinstance(steps, list):
                steps = []
            steps = [s for s in steps if isinstance(s, dict)]
        except (json.JSONDecodeError, AttributeError, TypeError):
            paradigm = "plan"
            steps = []

        # Let the LLM planner decide between rag/mcp/direct based on the
        # query intent. The force_mcp override was too aggressive — it sent
        # all product/customer/order queries to MCP, preventing RAG retrieval
        # for knowledge-base questions about those same entities.
        # The planner prompt already instructs: rag=knowledge/docs,
        # mcp=database/query tools, direct=greetings only.

        # C13/Fix-B,C: SAFETY OVERRIDE — if the user message has write intent
        # (create/update/delete an order/customer/etc.), FORCE the plan to use
        # "mcp" so the request goes through the WritePipeline (permission check
        # + idempotency + audit + rollback). This is non-negotiable: the LLM
        # planner must never pick "direct" for a write operation, because that
        # would let it hallucinate "已创建" without invoking the write tool,
        # bypassing all security controls. See _detect_write_intent().
        if _detect_write_intent(user_message):
            forced_steps = [{"id": 0, "action": "mcp", "args": {}}]
            return "react", forced_steps

        if paradigm == "react":
            return paradigm, steps
        if steps:
            return paradigm, steps
        return "plan", [{"id": 0, "action": "direct", "args": {}}]

    @staticmethod
    def _parse_react(content: str) -> tuple[str, dict[str, Any]]:
        """Parse ReAct LLM output: (action, args). Defaults to done."""
        try:
            parsed = json.loads(_extract_json_block(content))
            action = str(parsed.get("action", "done"))
            args = parsed.get("args", {})
            if not isinstance(args, dict):
                args = {}
            return action, args
        except (json.JSONDecodeError, AttributeError, TypeError):
            # LLM returned non-JSON (e.g. a natural-language answer). Preserve
            # the raw content as the answer instead of discarding it, so the
            # user gets a useful response even when the LLM ignores the JSON
            # format contract.
            return "done", {"answer": content.strip() or "parse error, assuming done"}

    @staticmethod
    def _parse_reflect(content: str) -> tuple[bool, str]:
        try:
            parsed = json.loads(_extract_json_block(content))
            done = bool(parsed.get("done", True))
            reason = str(parsed.get("reason", ""))
            return done, reason
        except (json.JSONDecodeError, AttributeError, TypeError):
            return True, "parse error, assuming done"

    @staticmethod
    def _current_step(state: _AgentState) -> dict[str, Any]:
        steps = state.get("plan_steps", [])
        current = state.get("current_step", 0)
        if current < len(steps):
            return steps[current]
        return {}

    @staticmethod
    def _sanitize_output(output: str | None) -> str | None:
        """Filter sensitive DB schema info from agent output (SAF-004 fix).

        Detects database structure patterns (SQL DDL, column type
        declarations, table.column references, sensitive field name lists)
        and replaces them with a redaction notice.
        """
        if not output:
            return output

        import re

        sanitized = output

        # 1. Detect SQL DDL statements
        ddl_pattern = re.compile(
            r"(CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+INDEX|INSERT\s+INTO|"
            r"UPDATE\s+\w+\s+SET|DELETE\s+FROM|SELECT\s+.*\s+FROM)",
            re.IGNORECASE,
        )
        if ddl_pattern.search(sanitized):
            lines = sanitized.split("\n")
            filtered_lines = []
            for line in lines:
                if ddl_pattern.search(line):
                    filtered_lines.append("[已过滤: SQL语句]")
                else:
                    filtered_lines.append(line)
            sanitized = "\n".join(filtered_lines)

        # 2. Detect column type declarations (e.g., "id UUID NOT NULL")
        col_type_pattern = re.compile(
            r"\b\w+\s+(UUID|VARCHAR\(\d+\)|TEXT|TIMESTAMPTZ|INTEGER|"
            r"BIGSERIAL|JSONB|NUMERIC\([\d,]+\)|BOOLEAN|BIGINT)\b"
            r"(?:\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY|UNIQUE|REFERENCES))*",
            re.IGNORECASE,
        )
        if col_type_pattern.search(sanitized):
            sanitized = col_type_pattern.sub("[已过滤: 字段定义]", sanitized)

        # 3. Detect table.column references (e.g., "agent.messages")
        table_col_pattern = re.compile(
            r"\b(agent|harness|trace|knowledge|iam|erp|audit)\.\w+"
        )
        sanitized = table_col_pattern.sub("[表名.列]", sanitized)

        # 4. Detect sensitive field name lists (3+ comma-separated
        #    snake_case identifiers containing known sensitive fields)
        sensitive_fields = {
            "idempotency_key", "approver_id", "before_state", "after_state",
            "rolled_back", "rollback_reason", "trace_id", "span_id",
            "parent_span_id", "cost_tokens", "cost_usd", "tenant_id",
        }
        word_list_pattern = re.compile(r"[\w]+(?:\s*,\s*[\w]+){2,}")
        for match in word_list_pattern.finditer(sanitized):
            fields_in_match = {f.strip().lower() for f in match.group().split(",")}
            if fields_in_match & sensitive_fields:
                sanitized = sanitized.replace(match.group(), "[已过滤: 敏感字段列表]")

        return sanitized

    @staticmethod
    def _translate_event(
        node_name: str,
        update: dict[str, Any],
        agent_id: UUID,
    ) -> AgentEvent | None:
        if node_name == "plan":
            return AgentEvent(
                type="plan",
                content=json.dumps({
                    "paradigm": update.get("paradigm", "plan"),
                    "steps": update.get("plan_steps", []),
                }),
                agent_id=agent_id,
            )
        if node_name == "reason":
            steps = update.get("plan_steps", [])
            action = steps[0].get("action", "done") if steps else "done"
            return AgentEvent(
                type="reason",
                content=action,
                agent_id=agent_id,
                metadata=steps[0] if steps else None,
            )
        if node_name == "skill_node":
            results = update.get("tool_results", [])
            last = results[-1] if results else {}
            return AgentEvent(
                type="tool_call",
                content=str(last.get("skill_name", "skill")),
                agent_id=agent_id,
                metadata=last,
            )
        if node_name == "rag_node":
            results = update.get("tool_results", [])
            last = results[-1] if results else {}
            return AgentEvent(
                type="tool_call",
                content="rag",
                agent_id=agent_id,
                metadata=last,
            )
        if node_name == "mcp_node":
            results = update.get("tool_results", [])
            last = results[-1] if results else {}
            return AgentEvent(
                type="tool_call",
                content=str(last.get("tool_name", "mcp")),
                agent_id=agent_id,
                metadata=last,
            )
        if node_name == "direct_node":
            return AgentEvent(
                type="token",
                content=update.get("final_output"),
                agent_id=agent_id,
            )
        if node_name == "reflect":
            return AgentEvent(
                type="reflect",
                content=update.get("reflect_reason"),
                agent_id=agent_id,
            )
        return None

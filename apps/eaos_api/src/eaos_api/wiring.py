"""Production dependency wiring — builds real components from AppConfig.

``build_deps`` constructs the full component graph (DB → LLM → embedder →
memory/skills/knowledge → runner/orchestrator → gateway → tracer/harness →
evolution pipeline) using the real PostgreSQL-backed implementations from
``packages/``. Called once at FastAPI lifespan startup; ``close_deps``
disposes connection pools on shutdown.

The wiring mirrors the construction patterns proven in the M3/M4/M5
integration tests (see ``packages/{agent,gateway,evolution}/tests/``) but
drops the test mocks in favour of real adapters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from eaos.agent.ambient import PgAmbientMonitor
    from eaos.agent.dispatcher import PgAgentDispatcher
    from eaos.agent.memory.engine import MemoryEngineImpl
    from eaos.agent.orchestrator import AgentOrchestratorImpl
    from eaos.agent.runner import LangGraphRunnerImpl
    from eaos.agent.tenant import PgTenantManager
    from eaos.core.auth import PermissionEvaluator
    from eaos.core.config import AppConfig
    from eaos.data.connection_manager import ConnectionManager
    from eaos.data.connector import DataConnector
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.evolution.dataset import PreferenceDatasetBuilderImpl
    from eaos.evolution.pipeline import EvolutionPipelineImpl
    from eaos.evolution.trainer import DPOTrainerImpl
    from eaos.gateway.im.gateway import MessageGatewayImpl
    from eaos.harness.cost.governor import CostGovernorImpl
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.harness import HarnessImpl
    from eaos.harness.policy import PolicyEngineImpl
    from eaos.infra.db.postgres import PgClient
    from eaos.infra.db.redis import RedisClientImpl
    from eaos.infra.llm.router_impl import LLMRouterImpl
    from eaos.knowledge.engine import KnowledgeEngineImpl
    from eaos.knowledge.ontology.repository import PgOntologyRepository
    from eaos.knowledge.rag.pipeline import RAGPipelineImpl
    from eaos.observability.query import PgTraceQuery
    from eaos.observability.tracer import TracerImpl
    from eaos.skills.registry import PgSkillRegistry
    from eaos.skills.resolver import SkillResolverImpl


@dataclass
class AppDeps:
    """All wired singletons held by ``app.state`` for the duration of the process."""

    db: PgClient
    redis: RedisClientImpl
    llm: LLMRouterImpl
    runner: LangGraphRunnerImpl
    orchestrator: AgentOrchestratorImpl
    gateway: MessageGatewayImpl
    tracer: TracerImpl
    harness: HarnessImpl
    knowledge_engine: KnowledgeEngineImpl
    evolution_pipeline: EvolutionPipelineImpl
    trainer: DPOTrainerImpl
    dataset_builder: PreferenceDatasetBuilderImpl
    trace_query: PgTraceQuery
    policy_engine: PolicyEngineImpl
    cost_governor: CostGovernorImpl
    approval_gate: ApprovalGateImpl
    ambient_monitor: PgAmbientMonitor
    auth_evaluator: PermissionEvaluator
    # F0: services exposed for the new API routes
    dispatcher: PgAgentDispatcher
    tenant_manager: PgTenantManager
    skill_registry: PgSkillRegistry
    skill_resolver: SkillResolverImpl
    memory_engine: MemoryEngineImpl
    rag_pipeline: RAGPipelineImpl
    ontology_repo: PgOntologyRepository
    connection_manager: ConnectionManager
    sql_sandbox: PgSqlSandbox
    text2sql_engine: Text2SQLEngineImpl
    data_connectors: dict[str, DataConnector]
    tool_registry: ToolRegistry


# -- LLM adapter for guardrail (Protocol-style chat) -------------------------


class _GuardrailLLMAdapter:
    """Adapt LLMRouter.chat(messages, model_hint=...) to GuardrailLLM.chat(prompt, model).

    GuardrailCheckerImpl calls ``await llm.chat(prompt, model)`` with a bare
    prompt string; LLMRouter expects a list of Message objects and a
    ``model_hint``. This adapter wraps a single-user-message call.
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    async def chat(self, prompt: str, model: str) -> str:
        from eaos.infra.llm.base import Message

        resp = await self._router.chat(
            [Message(role="user", content=prompt)],
            model_hint=model,
        )
        return str(resp.content)


# -- Embedder fallback (when no API key configured) --------------------------


class _NullEmbedder:
    """No-op embedder used when EAOS_EMBEDDING__API_KEY is unset.

    Construction succeeds so the API server can start without an embedding
    provider; embed() raises a clear error if memory/RAG is actually invoked.
    """

    @property
    def dimension(self) -> int:
        return 1024

    @property
    def model_name(self) -> str:
        return "null-embedder"

    async def embed(self, text: str) -> list[float]:
        from eaos.core.errors import LLMError

        raise LLMError(
            "embedding not configured — set EAOS_EMBEDDING__API_KEY to use "
            "memory/RAG features"
        )

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from eaos.core.errors import LLMError

        raise LLMError(
            "embedding not configured — set EAOS_EMBEDDING__API_KEY to use "
            "memory/RAG features"
        )


# -- Notifier fallback (no IM channel wired) ---------------------------------


class _NullNotifier:
    """No-op notifier satisfying ``eaos.agent.ambient.Notifier``.

    Ambient triggers fire ``send_message``; with no IM channel configured we
    silently drop notifications. Admin API routes only list/manage triggers,
    they don't require delivery.
    """

    name = "null"

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Any] | None = None,
    ) -> None:
        del target, text, attachments  # silently dropped


# -- Builders ----------------------------------------------------------------


def _build_llm(config: AppConfig) -> LLMRouterImpl:
    from eaos.infra.llm.router_impl import LLMRouterImpl

    router = LLMRouterImpl(
        default_provider="openai",
        default_model=config.llm.default_model,
        vision_model_override=config.llm.vision_model,
    )

    if config.llm.openai_api_key:
        from eaos.infra.llm.openai_adapter import OpenAILLMClient

        router.register_adapter(
            OpenAILLMClient(config.llm, base_url=config.llm.openai_base_url)
        )
    if config.llm.anthropic_api_key:
        from eaos.infra.llm.anthropic_adapter import AnthropicLLMClient

        router.register_adapter(AnthropicLLMClient(config.llm))
    if config.llm.glm_api_key:
        from eaos.infra.llm.glm_adapter import GLMLLMClient

        router.register_adapter(GLMLLMClient(config.llm))
    return router


def _build_embedder(config: AppConfig) -> Any:
    if config.embedding.api_key:
        from eaos.infra.vector.embedder import OpenAIEmbedder

        return OpenAIEmbedder(config.embedding)
    return _NullEmbedder()


def _build_guardrail_resolvers(
    config: AppConfig, db: PgClient
) -> tuple[
    Callable[[UUID], Awaitable[str]],
    Callable[[UUID], Awaitable[dict[str, Any]]],
]:
    """Build (model_resolver, metrics_resolver) for GuardrailCheckerImpl.

    model_resolver: queries the strategy's training_run.base_model from the
    DB; falls back to the configured default model if the strategy is not
    yet associated with a training run.
    """

    async def resolve_model(strategy_id: UUID) -> str:
        row = await db.fetch_one(
            "SELECT tr.base_model FROM harness.evolution_strategies es "
            "LEFT JOIN evolution.training_runs tr ON tr.id = es.training_run_id "
            "WHERE es.id = :p0",
            strategy_id,
        )
        if row is not None and row.get("base_model"):
            return str(row["base_model"])
        return config.llm.default_model

    async def resolve_metrics(strategy_id: UUID) -> dict[str, Any]:
        row = await db.fetch_one(
            "SELECT tr.metrics FROM evolution.training_runs tr "
            "JOIN harness.evolution_strategies es ON es.training_run_id = tr.id "
            "WHERE es.id = :p0",
            strategy_id,
        )
        if row is None or row.get("metrics") is None:
            return {}
        import json

        raw = row["metrics"]
        if isinstance(raw, str):
            return cast("dict[str, Any]", json.loads(raw))
        return cast("dict[str, Any]", raw)

    return resolve_model, resolve_metrics


def _safety_cases_path() -> str:
    """Locate the default safety_cases.yaml shipped with eaos.evolution."""
    import eaos.evolution.guardrail as gr

    return os.path.join(os.path.dirname(gr.__file__ or ""), "safety_cases.yaml")


# -- Public API --------------------------------------------------------------


async def build_deps(config: AppConfig) -> AppDeps:
    """Construct all components with real adapters. Called at lifespan startup."""
    from eaos.agent.ambient import PgAmbientMonitor
    from eaos.agent.collaboration.modes import (
        DebateExecutor,
        FanOutInExecutor,
        HierarchicalExecutor,
        RelayExecutor,
    )
    from eaos.agent.dispatcher import PgAgentDispatcher
    from eaos.agent.memory.engine import MemoryEngineImpl
    from eaos.agent.orchestrator import AgentOrchestratorImpl
    from eaos.agent.runner import LangGraphRunnerImpl
    from eaos.agent.tenant import PgTenantManager
    from eaos.core.auth import PermissionEvaluator, set_global_auth
    from eaos.data.crm_connector import CrmConnector
    from eaos.data.erp_connector import ErpConnector
    from eaos.data.knowledge_connector import KnowledgeConnector
    from eaos.data.mcp.server import McpServerImpl
    from eaos.evolution.dataset import PreferenceDatasetBuilderImpl
    from eaos.evolution.feedback import FeedbackCollectorImpl
    from eaos.evolution.guardrail import GuardrailCheckerImpl
    from eaos.evolution.pipeline import EvolutionPipelineImpl
    from eaos.evolution.shadow import ShadowTrafficManagerImpl
    from eaos.evolution.trainer import DPOTrainerImpl
    from eaos.gateway.im.channels.dingtalk import DingTalkChannel
    from eaos.gateway.im.gateway import MessageGatewayImpl
    from eaos.harness.capability.checker import CapabilityCheckerImpl
    from eaos.harness.compliance.guard import ComplianceGuardImpl
    from eaos.harness.cost.governor import CostGovernorImpl
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.evolution.governor import EvolutionGovernorImpl
    from eaos.harness.harness import HarnessImpl, set_global_harness
    from eaos.harness.permission.evaluator import PermissionEvaluatorImpl
    from eaos.harness.policy import PolicyEngineImpl
    from eaos.harness.quality.guard import QualityGuardImpl
    from eaos.infra.db.postgres import PgClient
    from eaos.infra.vector.pgvector_store import PgVectorStore
    from eaos.knowledge.engine import KnowledgeEngineImpl
    from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
    from eaos.knowledge.memory.store import PgMemoryStore
    from eaos.knowledge.ontology.query_rewrite import OntologyQueryRewriter
    from eaos.knowledge.ontology.repository import PgOntologyRepository
    from eaos.knowledge.rag.chunker import SemanticChunker
    from eaos.knowledge.rag.pipeline import RAGPipelineImpl
    from eaos.knowledge.rag.reranker import LLMReranker
    from eaos.knowledge.rag.retriever import HybridRetriever
    from eaos.observability._global import set_global_tracer
    from eaos.observability.query import PgTraceQuery
    from eaos.observability.store import PgTraceStore
    from eaos.observability.tracer import TracerImpl
    from eaos.skills.executor import SkillExecutorImpl
    from eaos.skills.quality import PgSkillQualityMonitor
    from eaos.skills.registry import PgSkillRegistry
    from eaos.skills.resolver import SkillResolverImpl

    # 1. DB + Redis
    db = PgClient(config.db)

    from eaos.infra.db.redis import RedisClientImpl

    redis = RedisClientImpl(config.redis)

    # 2. LLM + embedder
    llm = _build_llm(config)
    embedder = _build_embedder(config)

    # 3. Knowledge engine: ontology + RAG + memory
    vector_store = PgVectorStore(db)
    memory_store = PgMemoryStore(vector_store, embedder, db)
    consolidator = SessionMemoryConsolidator(memory_store, llm, db)
    ontology_repo = PgOntologyRepository(db)
    rewriter = OntologyQueryRewriter(ontology_repo, llm)
    retriever = HybridRetriever(vector_store, embedder, db)
    reranker = LLMReranker(llm)
    chunker = SemanticChunker(tenant_id=None)
    rag = RAGPipelineImpl(chunker, retriever, reranker, embedder, vector_store, db)
    knowledge_engine = KnowledgeEngineImpl(
        ontology_repo=ontology_repo,
        rewriter=rewriter,
        rag=rag,
        memory_store=memory_store,
        consolidator=consolidator,
        db=db,
    )

    # 4. MCP server + ToolRegistry (ERP + CRM connectors)
    # Created before skills so SkillExecutor can receive tool_registry.
    data_connectors: dict[str, Any] = {
        "erp": ErpConnector(db),
        "crm": CrmConnector(db),
        "knowledge": KnowledgeConnector(db),
    }
    mcp_server = McpServerImpl(data_connectors)

    from eaos.data.mcp.registry import ToolRegistry

    tool_registry = ToolRegistry()
    for name, connector in data_connectors.items():
        tool_registry.register_internal(name, connector)

    # 5. Skills
    registry = PgSkillRegistry(db)
    monitor = PgSkillQualityMonitor(db, registry)
    skill_resolver = SkillResolverImpl(db)
    skill_executor = SkillExecutorImpl(llm, monitor, tool_registry=tool_registry)

    # 6. Agent runner + orchestrator
    dispatcher = PgAgentDispatcher(db)
    tenant_manager = PgTenantManager(db, dispatcher)
    memory_engine = MemoryEngineImpl(memory_store, consolidator)

    # C03: Use AsyncPostgresSaver checkpointer instead of MemorySaver.
    # This persists graph state (including interrupt/resume for HITL) to
    # PostgreSQL, surviving API restarts and enabling multi-worker recovery.
    # Must use Async variant because LangGraph astream() calls aget_tuple().
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    import psycopg

    # Convert asyncpg URL to psycopg format: postgresql+asyncpg:// → postgresql://
    pg_dsn = config.db.url.replace("postgresql+asyncpg://", "postgresql://")
    _checkpointer_conn = await psycopg.AsyncConnection.connect(pg_dsn, autocommit=True)
    checkpointer = AsyncPostgresSaver(_checkpointer_conn)
    await checkpointer.setup()  # idempotent: creates tables if missing

    runner = LangGraphRunnerImpl(
        llm=llm,
        skill_resolver=skill_resolver,
        skill_executor=skill_executor,
        knowledge_engine=knowledge_engine,
        mcp_server=mcp_server,
        memory_engine=memory_engine,
        tenant_manager=tenant_manager,
        dispatcher=dispatcher,
        checkpointer=checkpointer,
        tool_registry=tool_registry,
        db=db,
    )
    orchestrator = AgentOrchestratorImpl(
        llm=llm,
        runner=runner,
        dispatcher=dispatcher,
        relay_executor=RelayExecutor(),
        fanout_executor=FanOutInExecutor(),
        debate_executor=DebateExecutor(),
        hierarchical_executor=HierarchicalExecutor(),
    )

    # 7. Gateway
    gateway = MessageGatewayImpl(
        orchestrator=orchestrator, tenant_manager=tenant_manager
    )
    if config.dingtalk.app_key or config.dingtalk.robot_code:
        gateway.register_channel(DingTalkChannel(config.dingtalk))

    # 8. Tracer + trace_query (register globals for @traced / @guarded)
    trace_store = PgTraceStore(db)
    tracer = TracerImpl(trace_store)
    set_global_tracer(tracer)
    trace_query = PgTraceQuery(db)

    auth_evaluator = PermissionEvaluator(db)
    set_global_auth(auth_evaluator)

    # 9. Harness (six pillars + policy + approval)
    permission = PermissionEvaluatorImpl(db)
    capability = CapabilityCheckerImpl(db)
    cost_governor = CostGovernorImpl(db)
    compliance = ComplianceGuardImpl(db)
    quality = QualityGuardImpl(db)
    model_resolver, metrics_resolver = _build_guardrail_resolvers(config, db)
    guardrail = GuardrailCheckerImpl(
        llm=_GuardrailLLMAdapter(llm),
        model_resolver=model_resolver,
        metrics_resolver=metrics_resolver,
        safety_cases_path=_safety_cases_path(),
        db=db,
    )
    evolution_governor = EvolutionGovernorImpl(db, guardrail=guardrail)
    approval_gate = ApprovalGateImpl(db)
    policy_engine = PolicyEngineImpl(db)
    harness = HarnessImpl(
        permission=permission,
        capability=capability,
        cost=cost_governor,
        compliance=compliance,
        quality=quality,
        evolution=evolution_governor,
        approval=approval_gate,
        policy=policy_engine,
    )
    set_global_harness(harness)

    # C07: WritePipeline — governed write operations through Harness
    from eaos.harness.write_pipeline import WritePipeline
    from eaos.observability.audit import AuditLogger

    audit_logger = AuditLogger(db)

    def _connector_resolver(tool_name: str) -> Any:
        """Resolve DataConnector by tool name prefix."""
        for prefix, connector in data_connectors.items():
            if tool_name.startswith(prefix):
                return connector
        raise ValueError(f"no connector for tool: {tool_name}")

    write_pipeline = WritePipeline(
        harness=harness,
        connector_resolver=_connector_resolver,
        audit_logger=audit_logger,
        approval_gate=approval_gate,
        db=db,  # C09: for idempotency key dedup queries
    )

    # C07: Register governed write tools
    tool_registry.set_write_pipeline(write_pipeline)
    tool_registry.register_write_tool(
        tool_name="erp_create_sales_order",
        resource="erp.orders",
        operation="create",
        risk_level="high",
        description=(
            "Create a sales order in the ERP system. "
            "Requires admin approval (high-risk write). "
            "Arguments: customer_code, product_sku, quantity, unit_price."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "customer_code": {"type": "string", "description": "Customer code (e.g. CUS-001)"},
                "product_sku": {"type": "string", "description": "Product SKU (e.g. PRD-002)"},
                "quantity": {"type": "integer", "description": "Order quantity"},
                "unit_price": {"type": "number", "description": "Unit price"},
            },
            "required": ["customer_code", "product_sku", "quantity"],
        },
    )

    # 10. Ambient monitor (no-op notifier; admin API triggers list/manage only)
    ambient_monitor = PgAmbientMonitor(
        db, orchestrator, _NullNotifier()
    )

    # 11. Evolution pipeline
    from eaos.evolution.artifact_store import build_artifact_store

    feedback = FeedbackCollectorImpl(db)
    dataset_builder = PreferenceDatasetBuilderImpl(db)
    artifact_store = build_artifact_store(config.artifact)
    trainer = DPOTrainerImpl(
        db,
        dataset_builder,
        artifact_dir=str(config.model_artifact_dir),
        artifact_store=artifact_store,
    )
    from eaos.evolution.replay import TraceReplayerImpl

    replayer = TraceReplayerImpl(
        db=db,
        llm=_GuardrailLLMAdapter(llm),
        judge_model=config.llm.default_model,
    )
    shadow = ShadowTrafficManagerImpl(db, replayer=replayer)
    evolution_pipeline = EvolutionPipelineImpl(
        db=db,
        feedback_collector=feedback,
        dataset_builder=dataset_builder,
        trainer=trainer,
        guardrail=guardrail,
        shadow=shadow,
        approval_gate=None,  # admin API handles approvals separately
    )

    # 12. Connection manager (Phase 7 — external system connections)
    from eaos.data.connection_manager import ConnectionManager
    from eaos.data.crypto import CredentialCrypto

    connection_manager = ConnectionManager(db, CredentialCrypto(config.secret_key))

    # 13. BI layer — SQL sandbox + Text2SQL engine (Phase 8 F0-T7)
    from eaos.data.text2sql.engine import Text2SQLEngineImpl
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.data.text2sql.validator import SqlValidatorImpl

    sql_sandbox = PgSqlSandbox(db)
    sql_validator = SqlValidatorImpl(db)
    text2sql_engine = Text2SQLEngineImpl(
        llm=llm,
        ontology_repo=ontology_repo,
        connectors=data_connectors,
        validator=sql_validator,
        sandbox=sql_sandbox,
        db=db,
    )

    return AppDeps(
        db=db,
        redis=redis,
        llm=llm,
        runner=runner,
        orchestrator=orchestrator,
        gateway=gateway,
        tracer=tracer,
        harness=harness,
        knowledge_engine=knowledge_engine,
        evolution_pipeline=evolution_pipeline,
        trainer=trainer,
        dataset_builder=dataset_builder,
        trace_query=trace_query,
        policy_engine=policy_engine,
        cost_governor=cost_governor,
        approval_gate=approval_gate,
        ambient_monitor=ambient_monitor,
        auth_evaluator=auth_evaluator,
        dispatcher=dispatcher,
        tenant_manager=tenant_manager,
        skill_registry=registry,
        skill_resolver=skill_resolver,
        memory_engine=memory_engine,
        rag_pipeline=rag,
        ontology_repo=ontology_repo,
        connection_manager=connection_manager,
        sql_sandbox=sql_sandbox,
        text2sql_engine=text2sql_engine,
        data_connectors=data_connectors,
        tool_registry=tool_registry,
    )


async def close_deps(deps: AppDeps) -> None:
    """Graceful shutdown: dispose DB pool and Redis pool. LLM/embedder clients are lazy + GC'd."""
    await deps.redis.close()
    await deps.db.close()

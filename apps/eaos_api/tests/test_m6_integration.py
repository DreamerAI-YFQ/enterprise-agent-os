"""M6 integration tests — end-to-end delivery stack validation.

Validates the full Phase 6 delivery: API serves health, /invoke with JWT SSE,
admin evolution API, worker pipeline advancement, multi-replica PostgresSaver
concurrent checkpoint writes, and a compressed happy path.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.router import LLMRouter

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")
SECRET = "m6-integration-test-secret-key-32b"


# -- Mock helpers (from M4 pattern) -----------------------------------------


class _MockEmbedder:
    @property
    def dimension(self) -> int:
        return 1024

    @property
    def model_name(self) -> str:
        return "mock-embedder"

    async def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        return [h[i % 32] / 255.0 for i in range(1024)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


def _llm_resp(content: str) -> Any:
    from eaos.infra.llm.base import LLMResponse

    return LLMResponse(content=content, prompt_tokens=5, completion_tokens=5)


def _mock_llm(responses: list[str]) -> Any:
    llm: Any = MagicMock()
    llm.chat = AsyncMock(side_effect=[_llm_resp(r) for r in responses])
    return llm


def _mock_knowledge_engine() -> Any:
    engine: Any = MagicMock()
    engine.search = AsyncMock(return_value=[])
    engine.rewrite_query = AsyncMock(return_value="rewritten")
    return engine


def _mock_mcp_server(result: str = '{"count": 42}') -> Any:
    server: Any = MagicMock()
    server.call_tool = AsyncMock(return_value=result)
    server.list_tools = AsyncMock(return_value=[])
    return server


# -- Runner + App factory ---------------------------------------------------


def _make_runner(db: DbClient, llm: LLMRouter) -> Any:
    """Build LangGraphRunnerImpl with real DB-backed components + mock LLM.

    Reuses the M4 pattern: real skills/memory/dispatcher/tenant_manager
    (DB-backed) but mock knowledge_engine/mcp_server (those call LLM
    indirectly). The runner's LLM is the mock so /invoke works without
    API keys.
    """
    from eaos.agent.dispatcher import PgAgentDispatcher
    from eaos.agent.memory.engine import MemoryEngineImpl
    from eaos.agent.runner import LangGraphRunnerImpl
    from eaos.agent.tenant import PgTenantManager
    from eaos.infra.vector.pgvector_store import PgVectorStore
    from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
    from eaos.knowledge.memory.store import PgMemoryStore
    from eaos.skills.executor import SkillExecutorImpl
    from eaos.skills.quality import PgSkillQualityMonitor
    from eaos.skills.registry import PgSkillRegistry
    from eaos.skills.resolver import SkillResolverImpl

    embedder = _MockEmbedder()
    vector_store = PgVectorStore(db)
    memory_store = PgMemoryStore(vector_store, embedder, db)
    consolidator = SessionMemoryConsolidator(memory_store, llm, db)
    memory_engine = MemoryEngineImpl(memory_store, consolidator)

    registry = PgSkillRegistry(db)
    monitor = PgSkillQualityMonitor(db, registry)
    skill_resolver = SkillResolverImpl(db)
    skill_executor = SkillExecutorImpl(llm, monitor)

    dispatcher = PgAgentDispatcher(db)
    tenant_manager = PgTenantManager(db, dispatcher)

    return LangGraphRunnerImpl(
        llm=llm,
        skill_resolver=skill_resolver,
        skill_executor=skill_executor,
        knowledge_engine=_mock_knowledge_engine(),
        mcp_server=_mock_mcp_server(),
        memory_engine=memory_engine,
        tenant_manager=tenant_manager,
        dispatcher=dispatcher,
    )


async def _make_app(llm_responses: list[str]) -> Any:
    """Build app: real governance via build_deps + mock-LLM runner for /invoke.

    ``build_deps`` constructs all real DB-backed components (tracer, harness,
    evolution_pipeline, etc.) and registers globals for @traced/@guarded.
    The runner is replaced with a mock-LLM version so /invoke works without
    API keys; all other components remain production-real.
    """
    from eaos.core.config import AppConfig
    from eaos.gateway.api.app import create_app
    from eaos_api.wiring import AppDeps, build_deps

    config = AppConfig(secret_key=SECRET, debug=True)
    deps: AppDeps = await build_deps(config)

    mock_llm = _mock_llm(llm_responses)
    runner = _make_runner(deps.db, mock_llm)

    app = create_app(config)
    app.state.db = deps.db
    app.state.runner = runner
    app.state.orchestrator = deps.orchestrator
    app.state.gateway = deps.gateway
    app.state.tracer = deps.tracer
    app.state.harness = deps.harness
    app.state.knowledge_engine = deps.knowledge_engine
    app.state.evolution_pipeline = deps.evolution_pipeline
    app.state.trainer = deps.trainer
    app.state.dataset_builder = deps.dataset_builder
    app.state.trace_query = deps.trace_query
    app.state.policy_engine = deps.policy_engine
    app.state.cost_governor = deps.cost_governor
    app.state.approval_gate = deps.approval_gate
    app.state.ambient_monitor = deps.ambient_monitor
    app.state.auth_evaluator = deps.auth_evaluator
    app.state._deps = deps  # hold reference for cleanup
    return app


def _admin_token() -> str:
    from eaos.core.auth import create_jwt_token

    return create_jwt_token(
        secret=SECRET, user_id=USER_ADMIN, tenant_id=TID, role="admin"
    )


def _parse_sse_events(body: str) -> list[str]:
    """Extract SSE data payloads from a response body."""
    events: list[str] = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            events.append(line[6:])
    return events


# -- Fixtures ---------------------------------------------------------------


@pytest.fixture
async def api_client(
    db: DbClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Any, None]:
    """ASGI client with production wiring + mock LLM for /invoke.

    Builds a fresh app per test via ``build_deps`` (real governance/evolution)
    and replaces only the runner with a mock-LLM version. ``close_deps``
    disposes the PgClient pool on teardown.
    """
    from eaos_api.wiring import close_deps
    from httpx import ASGITransport, AsyncClient

    monkeypatch.delenv("EAOS_LLM__OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EAOS_EMBEDDING__API_KEY", raising=False)

    app = await _make_app(
        [
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "Hello from M6 integration test.",
            '{"done": true, "reason": "direct answer"}',
        ]
    )
    deps = app.state._deps

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    await close_deps(deps)


@pytest.fixture
def admin_token() -> str:
    return _admin_token()


# -- Tests ------------------------------------------------------------------


class TestM6Delivery:
    """M6 milestone: full delivery stack end-to-end validation."""

    async def test_api_serves_health(self, api_client: Any) -> None:
        response = await api_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_invoke_via_http_with_jwt(
        self, api_client: Any, admin_token: str
    ) -> None:
        response = await api_client.post(
            "/invoke",
            json={
                "agent_id": str(AGENT_PERSONAL),
                "message": "Hello",
                "session_id": str(SESSION_DEMO),
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        assert len(events) > 0
        assert events[-1] == "[DONE]"

    async def test_admin_evolution_api_accessible(
        self, api_client: Any, admin_token: str
    ) -> None:
        response = await api_client.get(
            "/admin/evolution/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    async def test_worker_advances_pipeline(
        self, db: DbClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seed a canary-stage strategy → worker advances one round → full."""
        from eaos_api.wiring import build_deps, close_deps
        from eaos_worker.runner import _advance_pending

        monkeypatch.delenv("EAOS_LLM__OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("EAOS_EMBEDDING__API_KEY", raising=False)

        from eaos.core.config import AppConfig

        config = AppConfig(secret_key=SECRET, debug=True)
        deps = await build_deps(config)
        try:
            strategy_id = uuid4()
            run_id = uuid4()
            dataset_id = uuid4()

            # Seed a dataset (FK target for training_runs)
            await db.execute(
                """INSERT INTO evolution.datasets
                   (id, tenant_id, name) VALUES (:p0, :p1, :p2)""",
                dataset_id,
                TID,
                "m6-worker-test-dataset",
            )
            # Seed a completed training run (FK target for evolution_strategies)
            await db.execute(
                """INSERT INTO evolution.training_runs
                   (id, tenant_id, dataset_id, base_model, method, status,
                    metrics, started_at, completed_at)
                   VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, NOW(), NOW())""",
                run_id,
                TID,
                dataset_id,
                "mock-base",
                "dpo",
                "completed",
                json.dumps({"loss": 0.5, "accuracy": 0.9}),
            )
            # Seed strategy in canary stage (canary → full needs no LLM)
            await db.execute(
                """INSERT INTO harness.evolution_strategies
                   (id, tenant_id, training_run_id, stage, stage_status,
                    stage_detail)
                   VALUES (:p0, :p1, :p2, :p3, :p4, :p5)""",
                strategy_id,
                TID,
                run_id,
                "canary",
                "running",
                json.dumps({}),
            )

            # Simulate one worker round — _advance_pending queries pending
            # strategies and calls pipeline.advance() for each.
            count = await _advance_pending(deps.db, deps.evolution_pipeline)
            assert count >= 1

            row = await db.fetch_one(
                "SELECT stage, stage_status FROM harness.evolution_strategies "
                "WHERE id = :p0",
                strategy_id,
            )
            assert row is not None
            assert row["stage"] == "full"
            assert row["stage_status"] == "completed"
        finally:
            await close_deps(deps)

    async def test_multi_replica_postgres_saver(self, db: DbClient) -> None:
        """Verify PostgresSaver multi-instance concurrent writes don't conflict.

        Two PostgresSaver instances (simulating 2 API replicas) concurrently
        write checkpoints for the same thread_id. Postgres row locks (used
        by langgraph-checkpoint-postgres) must prevent corruption — both
        writes succeed and the latest checkpoint is retrievable.
        """
        from eaos.core.config import AppConfig

        config = AppConfig()
        # Convert SQLAlchemy async URL to psycopg sync URL
        conn_string = config.db.url.replace(
            "postgresql+asyncpg://", "postgresql://"
        )

        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row

        thread_id = f"m6-test-{uuid4()}"
        checkpoint_ns = ""

        conn1 = Connection.connect(
            conn_string,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        conn2 = Connection.connect(
            conn_string,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            saver1 = PostgresSaver(conn1)
            saver2 = PostgresSaver(conn2)
            saver1.setup()
            saver2.setup()

            ckpt1_id = f"ckpt-1-{uuid4()}"
            ckpt2_id = f"ckpt-2-{uuid4()}"
            checkpoint1: dict[str, Any] = {
                "v": 5,
                "id": ckpt1_id,
                "ts": "2026-06-30T12:00:00+00:00",
                "channel_values": {"messages": []},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            }
            checkpoint2: dict[str, Any] = {
                "v": 5,
                "id": ckpt2_id,
                "ts": "2026-06-30T12:00:01+00:00",
                "channel_values": {"messages": ["hello"]},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            }
            cfg1: dict[str, Any] = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            }
            cfg2: dict[str, Any] = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            }

            # Concurrent writes from both "replicas"
            metadata1: Any = {"source": "input", "step": 1, "writes": {}}
            metadata2: Any = {"source": "input", "step": 2, "writes": {}}
            await asyncio.gather(
                asyncio.to_thread(
                    saver1.put,
                    cast("Any", cfg1),
                    cast("Any", checkpoint1),
                    metadata1,
                    {},
                ),
                asyncio.to_thread(
                    saver2.put,
                    cast("Any", cfg2),
                    cast("Any", checkpoint2),
                    metadata2,
                    {},
                ),
            )

            # Verify latest checkpoint is retrievable from either replica
            tuple1 = await asyncio.to_thread(saver1.get_tuple, cast("Any", cfg1))
            assert tuple1 is not None
            assert tuple1.checkpoint["id"] in (ckpt1_id, ckpt2_id)

            # Cleanup
            saver1.delete_thread(thread_id)
        finally:
            conn1.close()
            conn2.close()

    async def test_full_stack_happy_path(
        self, api_client: Any, admin_token: str
    ) -> None:
        """Compressed happy path: invoke → evolution run → status poll."""
        # 1. Invoke (mock LLM streams SSE events)
        invoke_resp = await api_client.post(
            "/invoke",
            json={
                "agent_id": str(AGENT_PERSONAL),
                "message": "Run full stack test",
                "session_id": str(SESSION_DEMO),
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert invoke_resp.status_code == 200
        events = _parse_sse_events(invoke_resp.text)
        assert events[-1] == "[DONE]"

        # 2. Trigger evolution run (returns queued TrainingRun)
        run_resp = await api_client.post(
            "/admin/evolution/run",
            json={"base_model": "gpt-4o-mini"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert run_resp.status_code == 200
        run_data = run_resp.json()
        assert "id" in run_data
        assert run_data["status"] == "queued"

        # 3. Poll status (pipeline status is queryable after run starts)
        status_resp = await api_client.get(
            "/admin/evolution/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert status_resp.status_code == 200
        assert isinstance(status_resp.json(), dict)

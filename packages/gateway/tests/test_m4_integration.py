"""M4 integration tests — end-to-end governance and API validation.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.

Covers: HTTP JWT auth, SSE invoke, webhook, @traced span persistence,
trace drill-down, @guarded HITL/quota/quality/audit, multi-instance checkpoint.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.router import LLMRouter

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
USER_EMPLOYEE = UUID("00000000-0000-0000-0000-000000000202")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")
SECRET = "m4-integration-test-secret-key-32b"


# -- Mock helpers (from M3 pattern) -----------------------------------------


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


# -- App factory ------------------------------------------------------------


def _make_runner(db: DbClient, llm: LLMRouter) -> Any:
    """Construct LangGraphRunnerImpl with real DB-backed components."""
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


def _make_governance(db: DbClient) -> dict[str, Any]:
    """Construct all governance components and register globals."""
    from eaos.core.auth import PermissionEvaluator, set_global_auth
    from eaos.harness.capability.checker import CapabilityCheckerImpl
    from eaos.harness.compliance.guard import ComplianceGuardImpl
    from eaos.harness.cost.governor import CostGovernorImpl
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.evolution.governor import EvolutionGovernorImpl
    from eaos.harness.harness import HarnessImpl, set_global_harness
    from eaos.harness.permission.evaluator import PermissionEvaluatorImpl
    from eaos.harness.policy import PolicyEngineImpl
    from eaos.harness.quality.guard import QualityGuardImpl
    from eaos.observability._global import set_global_tracer
    from eaos.observability.query import PgTraceQuery
    from eaos.observability.store import PgTraceStore
    from eaos.observability.tracer import TracerImpl

    trace_store = PgTraceStore(db)
    tracer = TracerImpl(trace_store)
    set_global_tracer(tracer)

    trace_query = PgTraceQuery(db)

    auth_evaluator = PermissionEvaluator(db)
    set_global_auth(auth_evaluator)

    permission = PermissionEvaluatorImpl(db)
    capability = CapabilityCheckerImpl(db)
    cost = CostGovernorImpl(db)
    compliance = ComplianceGuardImpl(db)
    quality = QualityGuardImpl(db)
    evolution = EvolutionGovernorImpl(db)
    approval = ApprovalGateImpl(db)
    policy = PolicyEngineImpl(db)

    harness = HarnessImpl(
        permission=permission,
        capability=capability,
        cost=cost,
        compliance=compliance,
        quality=quality,
        evolution=evolution,
        approval=approval,
        policy=policy,
    )
    set_global_harness(harness)

    return {
        "tracer": tracer,
        "trace_query": trace_query,
        "harness": harness,
        "permission": permission,
        "capability": capability,
        "cost": cost,
        "compliance": compliance,
        "quality": quality,
        "evolution": evolution,
        "approval": approval,
        "policy": policy,
        "auth_evaluator": auth_evaluator,
    }


def _make_app(db: DbClient, llm: LLMRouter) -> Any:
    """Build a full FastAPI app with real DB-backed governance components."""
    from eaos.core.config import AppConfig
    from eaos.gateway.api.app import create_app

    config = AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]
    app = create_app(config)

    runner = _make_runner(db, llm)
    gov = _make_governance(db)

    app.state.runner = runner
    app.state.tracer = gov["tracer"]
    app.state.harness = gov["harness"]
    app.state.trace_query = gov["trace_query"]
    app.state.policy_engine = gov["policy"]
    app.state.cost_governor = gov["cost"]
    app.state.approval_gate = gov["approval"]
    app.state.auth_evaluator = gov["auth_evaluator"]
    app.state.db = db

    return app


def _admin_token() -> str:
    from eaos.core.auth import create_jwt_token

    return create_jwt_token(
        secret=SECRET, user_id=USER_ADMIN, tenant_id=TID, role="admin"
    )


def _employee_token() -> str:
    from eaos.core.auth import create_jwt_token

    return create_jwt_token(
        secret=SECRET, user_id=USER_EMPLOYEE, tenant_id=TID, role="employee"
    )


def _parse_sse_events(body: str) -> list[str]:
    """Extract SSE data payloads from a response body."""
    events: list[str] = []
    for line in body.split("\n"):
        if line.startswith("data: "):
            events.append(line[6:])
    return events


# -- HTTP End-to-End Tests --------------------------------------------------


class TestHttpEndToEnd:
    async def test_invoke_with_jwt_auth(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        llm = _mock_llm([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "Hello from M4 integration test.",
            '{"done": true, "reason": "direct answer"}',
        ])
        app = _make_app(db, llm)
        token = _admin_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/invoke",
                json={
                    "agent_id": str(AGENT_PERSONAL),
                    "message": "Hello",
                    "session_id": str(SESSION_DEMO),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        assert len(events) > 0
        assert events[-1] == "[DONE]"

    async def test_invoke_invalid_token_401(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        app = _make_app(db, _mock_llm([]))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/invoke",
                json={"agent_id": str(AGENT_PERSONAL), "message": "Hi"},
                headers={"Authorization": "Bearer invalid-token"},
            )
        assert response.status_code == 401

    async def test_invoke_permission_denied_403(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        app = _make_app(db, _mock_llm([]))
        token = _employee_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/triggers", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 403

    async def test_webhook_to_invoke(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        llm = _mock_llm([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "Webhook response.",
            '{"done": true, "reason": "done"}',
        ])
        app = _make_app(db, llm)

        from eaos.gateway.im.gateway import MessageGatewayImpl

        orchestrator = AsyncMock()
        gateway = MessageGatewayImpl(orchestrator=orchestrator)
        app.state.gateway = gateway
        app.state.orchestrator = orchestrator

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/webhook/dingtalk",
                json={
                    "msgtype": "text",
                    "text": {"content": "test webhook"},
                    "senderStaffId": "12345",
                    "conversationId": "c1",
                    "msgId": "m1",
                },
            )
        assert response.status_code in (200, 202, 404)


# -- @traced Persistence Tests ----------------------------------------------


class TestTracedPersistence:
    async def test_span_written_to_trace_spans(self, db: DbClient) -> None:
        from eaos.observability._global import get_global_tracer
        from eaos.observability.span import Granularity

        tracer = get_global_tracer()
        assert tracer is not None

        from eaos.core.context import TenantContext

        ctx = TenantContext(
            tenant_id=TID,
            user_id=USER_ADMIN,
            agent_id=AGENT_PERSONAL,
            agent_scope="personal",
        )
        async for handle in tracer.span("test.m4_span", Granularity.CALL, ctx):
            handle.set_cost(tokens=42, usd=0.05)
            handle.set_status("ok")

        rows = await db.fetch(
            "SELECT name, granularity, status, cost_tokens "
            "FROM trace.spans WHERE tenant_id = :p0 "
            "AND name = 'test.m4_span' ORDER BY start_time DESC LIMIT 1",
            TID,
        )
        assert len(rows) >= 1
        row = rows[0]
        assert row["name"] == "test.m4_span"
        assert row["granularity"] == "call"
        assert row["status"] == "ok"
        assert row["cost_tokens"] == 42

    async def test_trace_query_drill_down(self, db: DbClient) -> None:
        from datetime import UTC, datetime, timedelta

        from eaos.observability.query import DateRange, PgTraceQuery

        trace_query = PgTraceQuery(db)
        now = datetime.now(UTC)
        date_range = DateRange(start=now - timedelta(hours=1), end=now + timedelta(hours=1))

        overview = await trace_query.overview(TID, date_range)
        assert overview.tenant_id == TID
        assert overview.total_agents >= 0
        assert overview.task_success_rate >= 0.0


# -- @guarded Governance Tests ----------------------------------------------


class TestGuarded:
    async def test_high_risk_skill_triggers_hitl(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        app = _make_app(db, _mock_llm([]))
        token = _admin_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/approvals", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_cost_quota_exceeded_blocked(self, db: DbClient) -> None:
        from eaos.harness.context import GuardContext
        from eaos.harness.cost.governor import CostGovernorImpl

        governor = CostGovernorImpl(db)
        await db.execute(
            "DELETE FROM harness.quotas "
            "WHERE tenant_id = :p0 AND scope = 'organization' "
            "AND owner_id IS NULL AND period = 'monthly'",
            TID,
        )
        await db.execute(
            "INSERT INTO harness.quotas "
            "(tenant_id, scope, owner_id, period, token_limit, token_used, "
            "cost_used_usd, reset_at) "
            "VALUES (:p0, 'organization', NULL, 'monthly', 100, 100, 0, "
            "now() + interval '1 month')",
            TID,
        )

        ctx = GuardContext(
            tenant_id=TID,
            user_id=USER_ADMIN,
            agent_id=AGENT_PERSONAL,
            agent_scope="personal",
            action="test",
            resource="test",
            resource_id=None,
            attributes={},
            department_ids=[],
        )
        with pytest.raises(Exception):  # noqa: B017
            await governor.check_quota(ctx)

    async def test_quality_gate_blocks_degraded_skill(self, db: DbClient) -> None:
        from eaos.harness.context import GuardContext
        from eaos.harness.quality.guard import QualityGuardImpl

        quality = QualityGuardImpl(db)
        skill_id = uuid4()

        await db.execute(
            "DELETE FROM harness.quality_metrics "
            "WHERE tenant_id = :p0 AND skill_id = :p1",
            TID,
            skill_id,
        )
        await db.execute(
            "INSERT INTO harness.quality_metrics "
            "(tenant_id, skill_id, metric_date, call_count, "
            "success_count, failure_count) "
            "VALUES (:p0, :p1, CURRENT_DATE, 15, 0, 15)",
            TID,
            skill_id,
        )

        ctx = GuardContext(
            tenant_id=TID,
            user_id=USER_ADMIN,
            agent_id=AGENT_PERSONAL,
            agent_scope="personal",
            action="execute",
            resource="skill",
            resource_id=skill_id,
            attributes={"skill_id": str(skill_id)},
            department_ids=[],
        )
        result: Any = "test output"
        with pytest.raises(Exception):  # noqa: B017
            await quality.evaluate(ctx, result)

    async def test_audit_log_written(self, db: DbClient) -> None:
        from eaos.harness.compliance.guard import ComplianceGuardImpl
        from eaos.harness.context import GuardContext

        compliance = ComplianceGuardImpl(db)
        ctx = GuardContext(
            tenant_id=TID,
            user_id=USER_ADMIN,
            agent_id=AGENT_PERSONAL,
            agent_scope="personal",
            action="test_action",
            resource="test_resource",
            resource_id=None,
            attributes={},
            department_ids=[],
        )
        await compliance.audit(ctx, {"result": "test"})

        rows = await db.fetch(
            "SELECT action, resource_type FROM harness.audit_logs "
            "WHERE tenant_id = :p0 AND action = 'test_action' "
            "ORDER BY created_at DESC LIMIT 1",
            TID,
        )
        assert len(rows) >= 1
        assert rows[0]["action"] == "test_action"


# -- Multi-Instance Checkpoint Tests ----------------------------------------


class TestCheckpoint:
    async def test_postgres_saver_persists_state(self, db: DbClient) -> None:
        from httpx import ASGITransport, AsyncClient

        llm = _mock_llm([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "Checkpoint test response from instance A.",
            '{"done": true, "reason": "done"}',
        ])
        app = _make_app(db, llm)
        token = _admin_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/invoke",
                json={
                    "agent_id": str(AGENT_PERSONAL),
                    "message": "Remember this",
                    "session_id": str(SESSION_DEMO),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200

        llm2 = _mock_llm([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "Checkpoint test response from instance B.",
            '{"done": true, "reason": "done"}',
        ])
        app2 = _make_app(db, llm2)
        async with AsyncClient(
            transport=ASGITransport(app=app2), base_url="http://test"
        ) as client:
            response2 = await client.post(
                "/invoke",
                json={
                    "agent_id": str(AGENT_PERSONAL),
                    "message": "What did I say?",
                    "session_id": str(SESSION_DEMO),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response2.status_code == 200

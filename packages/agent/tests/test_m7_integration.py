"""M7 integration tests — tool execution layer end-to-end validation.

Two test tiers:

1. ``TestComponentWiring`` (always runs) — verifies that the T1-T7 components
   compose correctly in-process: WritePipeline orchestrates guard/HITL/write/
   audit/rollback/post_guard; SkillExecutor routes tool_bindings through the
   ToolRegistry; AuditLogger captures the write; HITL resume works; rollback
   fires on failure. These use mocked connectors/DB but REAL pipeline logic.

2. ``TestM7Integration`` (integration-marked, skips unless
   ``EAOS_RUN_INTEGRATION=1``) — full live-stack scenarios against the T0
   ``mock_saas`` service (REST + MCP server), as specified in the Phase 7 plan.
   Requires live PG + mock_saas running via ``docker-compose up``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from eaos.data.connector import ReadQuery, WriteResult
from eaos.data.mcp.registry import ToolRegistry
from eaos.data.mcp.types import McpToolResult
from eaos.harness.write_pipeline import (
    WriteApprovalRequired,
    WriteIntent,
    WritePipeline,
)
from eaos.observability.audit import AuditEntry, AuditLogger, AuditQuery
from eaos.skills.executor import SkillExecutorImpl
from eaos.skills.spec import (
    RiskLevel,
    SkillCategory,
    SkillScope,
    SkillSpec,
    ToolBinding,
)

TID = UUID("00000000-0000-0000-0000-000000000001")
PRINCIPAL = UUID("00000000-0000-0000-0000-000000000201")
AGENT = UUID("00000000-0000-0000-0000-000000000301")
MOCK_SAAS_BASE_URL = "http://localhost:18000"
MOCK_SAAS_API_KEY = "eaos-api-key-001"


# ============================================================
# Component Wiring Tests (always run — no live stack needed)
# ============================================================


def _mock_harness() -> Any:
    """Mock HarnessImpl — guard/post_guard are no-ops returning inputs."""
    h: Any = MagicMock()
    h.guard = AsyncMock(return_value=None)
    h.post_guard = AsyncMock(side_effect=lambda ctx, result: result)
    return h


def _mock_approval_gate() -> Any:
    ag: Any = MagicMock()
    ag.request_approval = AsyncMock(return_value=uuid4())
    # C13/Fix-3: WritePipeline now verifies approval status via check_approval
    ag.check_approval = AsyncMock(return_value="approved")
    return ag


def _mock_audit_logger() -> Any:
    """Mock AuditLogger — captures log/log_rollback calls with AsyncMock.

    WritePipeline orchestration (the real logic under test) calls these methods;
    AuditLogger's own persistence behavior is covered by test_audit.py.
    """
    al: Any = MagicMock()
    al.log = AsyncMock(return_value=uuid4())
    al.log_rollback = AsyncMock(return_value=None)
    return al


def _make_write_pipeline(
    *,
    harness: Any | None = None,
    connector: Any | None = None,
    audit_logger: AuditLogger | None = None,
    approval_gate: Any | None = None,
) -> tuple[WritePipeline, dict[str, Any]]:
    """Build a WritePipeline with mocked deps for wiring tests."""
    h = harness or _mock_harness()
    conn = connector or _mock_connector(WriteResult(success=True, after={"id": "1"}))
    al = audit_logger or _mock_audit_logger()
    ag = approval_gate or _mock_approval_gate()
    resolver: Any = MagicMock(return_value=conn)
    pipeline = WritePipeline(h, resolver, al, ag)
    return pipeline, {
        "harness": h,
        "connector": conn,
        "audit": al,
        "approval": ag,
        "resolver": resolver,
    }


def _mock_connector(result: WriteResult) -> Any:
    connector: Any = MagicMock()
    connector.write = AsyncMock(return_value=result)
    connector.rollback = AsyncMock(return_value=None)
    connector.list_resources = AsyncMock(return_value=[])
    connector.read = AsyncMock(return_value=MagicMock(rows=[], total=0))
    connector.describe_schema = AsyncMock(return_value=MagicMock(columns=[]))
    return connector


def _write_intent(**overrides: Any) -> WriteIntent:
    base: dict[str, Any] = {
        "tenant_id": TID,
        "principal_id": PRINCIPAL,
        "agent_id": AGENT,
        "tool_name": "erp_write",
        "resource": "orders",
        "operation": "create",
        "data": {"customer_id": "ACME", "amount": 1_000_000},
    }
    base.update(overrides)
    return WriteIntent(**base)


class TestComponentWiring:
    """Verify T1-T7 components compose into the M7 write loop in-process."""

    async def test_write_pipeline_full_loop(self) -> None:
        """WritePipeline 6-step loop: guard → write → audit → post_guard → outcome."""
        after = {"id": "ord-001", "customer_id": "ACME", "amount": 1_000_000}
        result = WriteResult(success=True, after=after)
        pipe, mocks = _make_write_pipeline(connector=_mock_connector(result))

        outcome = await pipe.execute(_write_intent())

        assert outcome.success is True
        assert outcome.after == after
        # guard fired before write
        mocks["harness"].guard.assert_awaited_once()
        # write called with correct args
        mocks["connector"].write.assert_awaited_once()
        # audit logged
        mocks["audit"].log.assert_awaited_once()
        entry = mocks["audit"].log.call_args.args[0]
        assert entry.success is True
        assert entry.operation == "create"
        # post_guard fired after write
        mocks["harness"].post_guard.assert_awaited_once()

    async def test_hitl_resume_flow(self) -> None:
        """High-risk write raises WriteApprovalRequired; resume with approval_id succeeds."""
        pipe, mocks = _make_write_pipeline()
        session_id = uuid4()
        intent = _write_intent(risk_level="high", session_id=session_id)

        # First call: should raise WriteApprovalRequired
        with pytest.raises(WriteApprovalRequired) as exc_info:
            await pipe.execute(intent)
        approval_id = exc_info.value.approval_id
        mocks["approval"].request_approval.assert_awaited_once()
        mocks["connector"].write.assert_not_called()

        # Second call (resume): approval_id set, should succeed
        resume_intent = _write_intent(
            risk_level="high",
            session_id=session_id,
            approval_id=approval_id,
        )
        outcome = await pipe.execute(resume_intent)
        assert outcome.success is True
        assert outcome.approval_id == approval_id
        # No new approval requested on resume
        assert mocks["approval"].request_approval.await_count == 1

    async def test_rollback_on_write_failure(self) -> None:
        """Failed write with before snapshot triggers rollback + audit."""
        before = {"id": "ord-001", "amount": 500}
        result = WriteResult(
            success=False, before=before, error="constraint violation"
        )
        connector = _mock_connector(result)
        # C09: _verify_record_matches calls connector.read to verify rollback;
        # return a row matching the before snapshot so verification passes.
        connector.read = AsyncMock(
            return_value=MagicMock(rows=[before], total=1)
        )
        pipe, mocks = _make_write_pipeline(connector=connector)

        outcome = await pipe.execute(_write_intent(operation="update", record_id="ord-001"))

        assert outcome.success is False
        assert outcome.rolled_back is True
        assert outcome.error == "constraint violation"
        mocks["connector"].rollback.assert_awaited_once()
        mocks["audit"].log_rollback.assert_awaited_once()

    async def test_audit_captures_before_after(self) -> None:
        """AuditLogger.log receives AuditEntry with before/after snapshots."""
        before = {"id": "1", "name": "old"}
        after = {"id": "1", "name": "new"}
        result = WriteResult(success=True, before=before, after=after)
        pipe, mocks = _make_write_pipeline(connector=_mock_connector(result))

        await pipe.execute(_write_intent(operation="update", record_id="1"))

        entry: AuditEntry = mocks["audit"].log.call_args.args[0]
        assert entry.before == before
        assert entry.after == after
        assert entry.success is True

    async def test_skill_executor_routes_tool_binding_to_registry(self) -> None:
        """SkillExecutor._run_tool_bindings → ToolRegistry.call_tool → connector.write.

        Verifies the T4 wiring: a skill with tool_bindings invokes the tool
        through the unified ToolRegistry, which routes to the registered
        internal connector.
        """
        # Real ToolRegistry with a mocked internal connector
        connector = _mock_connector(
            WriteResult(success=True, after={"id": "ord-007"})
        )
        registry = ToolRegistry()
        registry.register_internal("erp", connector)

        # Mock LLM + monitor (tool_bindings path shouldn't touch LLM)
        llm: Any = MagicMock()
        llm.chat = AsyncMock()
        monitor: Any = MagicMock()
        monitor.record = AsyncMock()
        monitor.check_auto_deprecate = AsyncMock(return_value=False)

        executor = SkillExecutorImpl(llm, monitor, tool_registry=registry)

        # Skill with a tool_binding to the internal erp write tool
        binding = ToolBinding(
            tool_name="erp_write",  # matches connector name + write op? No —
            # internal tools are {connector}_{op}; for write we'd need a custom
            # routing. Here we test the binding→registry→call_tool path with
            # a tool name the registry recognizes.
            param_mapping={"customer": "customer_id", "total": "amount"},
        )
        # The ToolRegistry only routes list_resources/read/describe_schema for
        # internal connectors (no "write" op). To test the binding path we
        # register a mock MCP client whose tool matches.
        mcp_client: Any = MagicMock()
        mcp_client.list_tools = AsyncMock(return_value=[])
        mcp_client.call_tool = AsyncMock(
            return_value=McpToolResult(
                content=[{"type": "text", "text": '{"id": "ord-007"}'}]
            )
        )
        mcp_client.health_check = AsyncMock(return_value=True)
        mcp_client.close = AsyncMock()
        registry.register_mcp("erp", mcp_client)

        binding = ToolBinding(
            tool_name="erp.create_order",
            param_mapping={"customer": "customer_id", "total": "amount"},
        )

        from eaos.core.context import TenantContext

        ctx = TenantContext(
            tenant_id=TID,
            user_id=PRINCIPAL,
            agent_id=AGENT,
            agent_scope="personal",
        )

        skill = SkillSpec(
            id=uuid4(),
            tenant_id=TID,
            scope=SkillScope.PERSONAL,
            owner_id=PRINCIPAL,
            name="create-order",
            display_name="Create Order",
            description="Create an ERP order",
            category=SkillCategory.PROCESS_AUTOMATION,
            risk_level=RiskLevel.MEDIUM,
            instructions="Create order in ERP",
            tools=[],
            tool_bindings=[binding],
        )

        result = await executor.execute(
            skill, {"customer": "ACME", "total": 1_000_000}, ctx
        )

        assert result.success is True
        mcp_client.call_tool.assert_awaited_once()
        call = mcp_client.call_tool.call_args
        assert call.args[0] == "erp.create_order"
        # param_mapping applied
        assert call.args[1] == {"customer_id": "ACME", "amount": 1_000_000}
        # LLM NOT called (tool_bindings path)
        llm.chat.assert_not_awaited()

    async def test_guard_rejection_blocks_write(self) -> None:
        """If Harness.guard raises, write never fires, no audit logged."""
        from eaos.core.errors import PermissionDeniedError

        h = _mock_harness()
        h.guard = AsyncMock(side_effect=PermissionDeniedError("not allowed"))
        pipe, mocks = _make_write_pipeline(harness=h)

        with pytest.raises(PermissionDeniedError):
            await pipe.execute(_write_intent())

        mocks["connector"].write.assert_not_called()
        mocks["audit"].log.assert_not_called()

    async def test_trace_id_flows_to_audit(self) -> None:
        """Trace correlation: intent.trace_id appears in the audit entry."""
        pipe, mocks = _make_write_pipeline()
        trace_id = uuid4()
        await pipe.execute(_write_intent(trace_id=trace_id))

        entry: AuditEntry = mocks["audit"].log.call_args.args[0]
        assert entry.trace_id == trace_id


# ============================================================
# Live-Stack Integration Tests (skip unless EAOS_RUN_INTEGRATION=1)
# ============================================================

pytestmark_integration = pytest.mark.integration


def _http_get_order(order_id: str) -> httpx.Response:
    """GET a single order from mock_saas REST API (direct verification helper)."""
    return httpx.get(
        f"{MOCK_SAAS_BASE_URL}/api/v1/orders/{order_id}",
        headers={"X-API-Key": MOCK_SAAS_API_KEY},
        timeout=15.0,
    )


@pytestmark_integration
class TestM7Integration:
    """M7 end-to-end: NL → Agent → tool call → HITL → write → audit → rollback.

    All scenarios connect to the T0 ``mock_saas`` service (REST + MCP server).
    Requires live PG + mock_saas running via ``docker-compose up``.
    Set ``EAOS_RUN_INTEGRATION=1`` to run.
    """

    async def test_end_to_end_write_with_hitl_via_http(
        self, db: Any, live_stack: Any
    ) -> None:
        """HTTP connector path: high-risk write → HITL interrupt → approve → resume → audit."""
        stack = live_stack
        session_id = uuid4()
        trace_id = uuid4()
        intent = WriteIntent(
            tenant_id=stack.tenant_id,
            principal_id=PRINCIPAL,
            agent_id=AGENT,
            tool_name="erp_write",
            resource="orders",
            operation="create",
            data={"customer_id": "cus_acme", "amount": 42_000.0, "currency": "CNY"},
            risk_level="high",
            session_id=session_id,
            trace_id=trace_id,
        )

        # First call: high-risk → WriteApprovalRequired raised before write.
        with pytest.raises(WriteApprovalRequired) as exc_info:
            await stack.write_pipeline.execute(intent)
        approval_id = exc_info.value.approval_id

        # Approve via the real ApprovalGateImpl (writes to harness.approvals).
        await stack.approval_gate.approve(approval_id, PRINCIPAL)

        # Resume with approval_id set → write proceeds.
        resume_intent = WriteIntent(
            tenant_id=stack.tenant_id,
            principal_id=PRINCIPAL,
            agent_id=AGENT,
            tool_name="erp_write",
            resource="orders",
            operation="create",
            data={"customer_id": "cus_acme", "amount": 42_000.0, "currency": "CNY"},
            risk_level="high",
            session_id=session_id,
            trace_id=trace_id,
            approval_id=approval_id,
        )
        outcome = await stack.write_pipeline.execute(resume_intent)

        assert outcome.success is True
        assert outcome.approval_id == approval_id
        assert outcome.after is not None
        order_id = str(outcome.after["id"])

        # Verify the order landed in mock_saas via direct REST GET.
        resp = _http_get_order(order_id)
        assert resp.status_code == 200
        assert resp.json()["customer_id"] == "cus_acme"
        assert float(resp.json()["amount"]) == 42_000.0

        # Verify an audit entry was persisted with the approval linkage.
        assert outcome.audit_id is not None
        entry = await stack.audit_logger.get(outcome.audit_id)
        assert entry is not None
        assert entry.success is True
        assert entry.approval_id == approval_id
        assert entry.trace_id == trace_id

    async def test_end_to_end_write_via_mcp(
        self, db: Any, live_stack: Any
    ) -> None:
        """MCP path: ToolRegistry.call_tool → McpClient → erp_create_order → verify."""
        stack = live_stack
        result = await stack.tool_registry.call_tool(
            "mock-saas.erp_create_order",
            {"customer_id": "cus_globex", "amount": 7_777.0, "currency": "CNY"},
            stack.tenant_id,
        )
        assert result.is_error is False
        created = json.loads(result.content[0]["text"])
        order_id = created["id"]
        assert created["customer_id"] == "cus_globex"
        assert float(created["amount"]) == 7_777.0

        # Verify via a second MCP tool call (erp_get_order).
        verify = await stack.tool_registry.call_tool(
            "mock-saas.erp_get_order",
            {"order_id": order_id},
            stack.tenant_id,
        )
        assert verify.is_error is False
        fetched = json.loads(verify.content[0]["text"])
        assert fetched["id"] == order_id
        assert fetched["customer_id"] == "cus_globex"

    async def test_http_connector_read_query(
        self, db: Any, live_stack: Any
    ) -> None:
        """HTTP connector read: GET /api/v1/orders returns seeded orders (page 1)."""
        stack = live_stack
        query = ReadQuery(filters={}, limit=5, offset=0)
        result = await stack.http_connector.read(
            stack.tenant_id, "orders", query
        )
        assert result.total >= 20  # seed data has 20 orders
        assert len(result.rows) <= 5
        # Each row is an order dict with at least an id and customer_id.
        assert all("id" in row for row in result.rows)

    async def test_write_rollback_on_failure(
        self, db: Any, live_stack: Any
    ) -> None:
        """Update with invalid customer → 422 → rollback restores original → audit logged."""
        stack = live_stack
        # Create a baseline order to attempt updating.
        create_outcome = await stack.write_pipeline.execute(
            WriteIntent(
                tenant_id=stack.tenant_id,
                principal_id=PRINCIPAL,
                agent_id=AGENT,
                tool_name="erp_write",
                resource="orders",
                operation="create",
                data={"customer_id": "cus_acme", "amount": 100.0},
                trace_id=uuid4(),
            )
        )
        assert create_outcome.success is True
        assert create_outcome.after is not None
        order_id = str(create_outcome.after["id"])
        original_amount = float(create_outcome.after["amount"])

        # Update with an unknown customer_id → mock_saas returns 422.
        # HttpApiConnector captures the before snapshot, then write fails →
        # WritePipeline rolls back using the snapshot.
        fail_intent = WriteIntent(
            tenant_id=stack.tenant_id,
            principal_id=PRINCIPAL,
            agent_id=AGENT,
            tool_name="erp_write",
            resource="orders",
            operation="update",
            record_id=order_id,
            data={"customer_id": "INVALID_CUST_XYZ", "amount": 999.0},
            trace_id=uuid4(),
        )
        outcome = await stack.write_pipeline.execute(fail_intent)

        assert outcome.success is False
        assert outcome.rolled_back is True
        assert outcome.before is not None

        # Verify the order is unchanged in mock_saas after rollback.
        resp = _http_get_order(order_id)
        assert resp.status_code == 200
        assert float(resp.json()["amount"]) == original_amount
        assert resp.json()["customer_id"] == "cus_acme"

        # Verify the audit trail recorded the failed + rolled-back write.
        assert outcome.audit_id is not None
        entry = await stack.audit_logger.get(outcome.audit_id)
        assert entry is not None
        assert entry.success is False

    async def test_skill_with_tool_bindings(
        self, db: Any, live_stack: Any
    ) -> None:
        """Skill with tool_bindings → SkillExecutor → McpClient.call_tool → order created."""
        stack = live_stack
        from eaos.core.context import TenantContext

        llm: Any = MagicMock()
        llm.chat = AsyncMock()
        monitor: Any = MagicMock()
        monitor.record = AsyncMock()
        monitor.check_auto_deprecate = AsyncMock(return_value=False)

        executor = SkillExecutorImpl(
            llm, monitor, tool_registry=stack.tool_registry
        )

        binding = ToolBinding(
            tool_name="mock-saas.erp_create_order",
            param_mapping={"customer": "customer_id", "total": "amount"},
        )
        skill = SkillSpec(
            id=uuid4(),
            tenant_id=stack.tenant_id,
            scope=SkillScope.PERSONAL,
            owner_id=PRINCIPAL,
            name="create-order-skill",
            display_name="Create Order",
            description="Create an ERP order via MCP",
            category=SkillCategory.PROCESS_AUTOMATION,
            risk_level=RiskLevel.MEDIUM,
            instructions="Create order in ERP",
            tools=[],
            tool_bindings=[binding],
        )

        ctx = TenantContext(
            tenant_id=stack.tenant_id,
            user_id=PRINCIPAL,
            agent_id=AGENT,
            agent_scope="personal",
        )

        result = await executor.execute(
            skill, {"customer": "cus_stark", "total": 3_333.0}, ctx
        )
        assert result.success is True
        # LLM NOT called — tool_bindings path bypasses it.
        llm.chat.assert_not_awaited()

        payload = json.loads(result.output)
        assert payload[0]["tool"] == "mock-saas.erp_create_order"
        created = json.loads(payload[0]["content"][0]["text"])
        assert created["customer_id"] == "cus_stark"
        assert float(created["amount"]) == 3_333.0

    async def test_guardrail_blocks_unauthorized_write(
        self, db: Any, live_stack: Any
    ) -> None:
        """Harness.guard denies → PermissionDeniedError raised, no write, no audit."""
        stack = live_stack
        from eaos.core.errors import PermissionDeniedError

        # Local harness that denies — does not mutate the session-scoped mock.
        denying_harness: Any = MagicMock()
        denying_harness.guard = AsyncMock(
            side_effect=PermissionDeniedError("principal lacks write permission")
        )
        denying_harness.post_guard = AsyncMock(
            side_effect=lambda ctx, result: result
        )

        def _resolver(_name: str) -> Any:
            return stack.http_connector

        local_pipeline = WritePipeline(
            denying_harness,
            _resolver,
            stack.audit_logger,
            stack.approval_gate,
        )

        intent = WriteIntent(
            tenant_id=stack.tenant_id,
            principal_id=PRINCIPAL,
            agent_id=AGENT,
            tool_name="erp_write",
            resource="orders",
            operation="create",
            data={"customer_id": "cus_acme", "amount": 1.0},
            trace_id=uuid4(),
        )

        with pytest.raises(PermissionDeniedError):
            await local_pipeline.execute(intent)

        denying_harness.guard.assert_awaited_once()
        # No audit entry should be logged for a guard-rejected write.
        entries = await stack.audit_logger.query(
            stack.tenant_id,
            filters=AuditQuery(principal_id=PRINCIPAL, resource="orders"),
        )
        rejected = [e for e in entries if e.trace_id == intent.trace_id]
        assert rejected == []

    async def test_sql_sandbox_readonly_enforced(
        self, db: Any, live_stack: Any
    ) -> None:
        """INSERT via sandbox → SET TRANSACTION READ ONLY rejects → no row added."""
        stack = live_stack
        before_rows = await stack.db.fetch(
            "SELECT count(*) AS n FROM harness.write_audit"
        )
        before_count = int(before_rows[0]["n"])

        # A syntactically valid INSERT that READ ONLY must reject at the engine.
        result = await stack.sandbox.execute_readonly(
            "INSERT INTO harness.write_audit "
            "(id, tenant_id, principal_id, tool_name, resource, operation, success) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)",
            [uuid4(), stack.tenant_id, PRINCIPAL, "test", "orders", "create", True],
            stack.tenant_id,
        )
        # Errors are swallowed → empty list returned.
        assert result == []

        after_rows = await stack.db.fetch(
            "SELECT count(*) AS n FROM harness.write_audit"
        )
        after_count = int(after_rows[0]["n"])
        # No row was inserted — READ ONLY transaction blocked the write.
        assert after_count == before_count

    async def test_tenant_isolation_internal(
        self, db: Any, live_stack: Any
    ) -> None:
        """Audit tenant scoping: tenant A entry invisible to tenant B query."""
        stack = live_stack
        other_tenant = UUID("00000000-0000-0000-0000-000000000002")
        trace_id = uuid4()

        # Log an audit entry under the seed tenant (A).
        entry_id = await stack.audit_logger.log(
            AuditEntry(
                tenant_id=stack.tenant_id,
                principal_id=PRINCIPAL,
                tool_name="erp_write",
                resource="orders",
                operation="create",
                success=True,
                trace_id=trace_id,
            )
        )

        # Tenant B query must not see tenant A's entry.
        other_entries = await stack.audit_logger.query(
            other_tenant, filters=AuditQuery(resource="orders")
        )
        assert all(e.trace_id != trace_id for e in other_entries)

        # Tenant A query sees the entry.
        own_entries = await stack.audit_logger.query(
            stack.tenant_id, filters=AuditQuery(resource="orders")
        )
        own = [e for e in own_entries if e.id == entry_id]
        assert len(own) == 1
        assert own[0].tenant_id == stack.tenant_id

    async def test_connection_health_check(
        self, db: Any, live_stack: Any
    ) -> None:
        """Register http_api + mcp_stdio → health-check both → healthy."""
        stack = live_stack
        http_status = await stack.connection_manager.health_check(
            stack.http_conn_id
        )
        assert http_status.status == "healthy"

        mcp_status = await stack.connection_manager.health_check(
            stack.mcp_conn_id
        )
        assert mcp_status.status == "healthy"

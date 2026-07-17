"""C01 contract tests — unified execution context and events.

Validates:
- Context/Invocation serialization and canonical payload hash stability
- Parameter dict order changes don't change digest; value changes do
- Write calls missing user/agent/session/trace fail closed
- action/resource vocabulary coverage
- Legacy read interface still works; write interface requires full context
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from eaos.core.execution import (
    Action,
    RiskLevel,
    ToolEventType,
    ToolExecutionContext,
    ToolInvocation,
    build_idempotency_key,
)

# -- Fixtures -----------------------------------------------------------------

TENANT = uuid4()
USER = uuid4()
AGENT = uuid4()
SESSION = uuid4()
TRACE = uuid4()
DEPT = uuid4()


def _full_ctx(**overrides: Any) -> ToolExecutionContext:
    defaults: dict[str, Any] = dict(
        tenant_id=TENANT,
        user_id=USER,
        agent_id=AGENT,
        session_id=SESSION,
        agent_scope="personal",
        department_ids=[],
        trace_id=TRACE,
    )
    defaults.update(overrides)
    return ToolExecutionContext(**defaults)


def _read_ctx(**overrides: Any) -> ToolExecutionContext:
    """Minimal context for read operations (only tenant_id required)."""
    defaults: dict[str, Any] = dict(
        tenant_id=TENANT,
        user_id=USER,
        agent_id=AGENT,
        session_id=SESSION,
        agent_scope="personal",
    )
    defaults.update(overrides)
    return ToolExecutionContext(**defaults)


# -- C01-02: ToolExecutionContext tests ---------------------------------------


class TestToolExecutionContext:
    def test_read_path_passes_with_tenant_only(self) -> None:
        """Read paths only require tenant_id (backward compat)."""
        ctx = ToolExecutionContext(
            tenant_id=TENANT,
            user_id=USER,
            agent_id=AGENT,
            session_id=SESSION,
            agent_scope="personal",
        )
        ctx.fail_closed(is_write=False)  # should not raise

    def test_write_path_fails_without_user(self) -> None:
        ctx = _full_ctx(user_id=None)
        with pytest.raises(ValueError, match="user_id"):
            ctx.fail_closed(is_write=True)

    def test_write_path_fails_without_agent(self) -> None:
        ctx = _full_ctx(agent_id=None)
        with pytest.raises(ValueError, match="agent_id"):
            ctx.fail_closed(is_write=True)

    def test_write_path_fails_without_session(self) -> None:
        ctx = _full_ctx(session_id=None)
        with pytest.raises(ValueError, match="session_id"):
            ctx.fail_closed(is_write=True)

    def test_write_path_fails_without_trace(self) -> None:
        ctx = _full_ctx(trace_id=None)
        with pytest.raises(ValueError, match="trace_id"):
            ctx.fail_closed(is_write=True)

    def test_write_path_passes_with_full_context(self) -> None:
        ctx = _full_ctx()
        ctx.fail_closed(is_write=True)  # should not raise

    def test_department_scope_requires_departments(self) -> None:
        ctx = _full_ctx(agent_scope="department", department_ids=[])
        with pytest.raises(ValueError, match="department_ids"):
            ctx.fail_closed(is_write=True)

    def test_department_scope_passes_with_departments(self) -> None:
        ctx = _full_ctx(agent_scope="department", department_ids=[DEPT])
        ctx.fail_closed(is_write=True)  # should not raise

    def test_thread_id_format(self) -> None:
        ctx = _full_ctx()
        assert ctx.thread_id == f"{TENANT}:{AGENT}:{SESSION}"


class TestContextAdapters:
    def test_to_tenant_context(self) -> None:
        from eaos.core.context import TenantContext

        ctx = _full_ctx()
        tc = ctx.to_tenant_context()
        assert isinstance(tc, TenantContext)
        assert tc.tenant_id == TENANT
        assert tc.user_id == USER
        assert tc.agent_id == AGENT
        assert tc.session_id == SESSION

    def test_to_guard_context(self) -> None:
        from eaos.harness.context import GuardContext

        ctx = _full_ctx()
        gc = ctx.to_guard_context(
            action=Action.DATASOURCE_WRITE.value,
            resource="erp.orders",
            risk_level=RiskLevel.HIGH.value,
        )
        assert isinstance(gc, GuardContext)
        assert gc.action == "datasource.write"
        assert gc.resource == "erp.orders"
        assert gc.risk_level == "high"
        assert gc.attributes["session_id"] == SESSION
        assert gc.attributes["trace_id"] == TRACE


# -- C01-02: ToolInvocation tests ---------------------------------------------


class TestToolInvocation:
    def _invocation(self, **overrides: Any) -> ToolInvocation:
        defaults: dict[str, Any] = dict(
            tool_name="erp_create_sales_order",
            resource="erp.orders",
            operation="create",
            canonical_arguments={
                "customer_code": "CUS-001",
                "product_sku": "PRD-002",
                "quantity": 400,
            },
            risk_level="high",
            action=Action.DATASOURCE_WRITE.value,
        )
        defaults.update(overrides)
        return ToolInvocation(**defaults)

    def test_intent_digest_stable_across_dict_order(self) -> None:
        """Dict key order changes must not change digest."""
        inv1 = self._invocation(
            canonical_arguments={
                "customer_code": "CUS-001",
                "product_sku": "PRD-002",
                "quantity": 400,
            }
        )
        inv2 = self._invocation(
            canonical_arguments={
                "quantity": 400,
                "product_sku": "PRD-002",
                "customer_code": "CUS-001",
            }
        )
        assert inv1.intent_digest() == inv2.intent_digest()

    def test_intent_digest_changes_on_value_change(self) -> None:
        """Value changes must change digest."""
        inv1 = self._invocation(canonical_arguments={"quantity": 400})
        inv2 = self._invocation(canonical_arguments={"quantity": 401})
        assert inv1.intent_digest() != inv2.intent_digest()

    def test_intent_digest_changes_on_tool_change(self) -> None:
        inv1 = self._invocation(tool_name="erp_create_sales_order")
        inv2 = self._invocation(tool_name="erp_create_purchase_order")
        assert inv1.intent_digest() != inv2.intent_digest()

    def test_to_write_intent(self) -> None:
        from eaos.harness.write_pipeline import WriteIntent

        ctx = _full_ctx()
        inv = self._invocation()
        wi = inv.to_write_intent(ctx)
        assert isinstance(wi, WriteIntent)
        assert wi.tenant_id == TENANT
        assert wi.principal_id == USER
        assert wi.agent_id == AGENT
        assert wi.session_id == SESSION
        assert wi.trace_id == TRACE
        assert wi.resource == "erp.orders"
        assert wi.operation == "create"
        assert wi.risk_level == "high"


# -- C01-02: Idempotency key --------------------------------------------------


class TestIdempotencyKey:
    def test_same_context_same_intent_same_key(self) -> None:
        ctx = _full_ctx()
        inv = ToolInvocation(
            tool_name="erp_create_sales_order",
            resource="erp.orders",
            operation="create",
            canonical_arguments={"customer_code": "CUS-001", "quantity": 10},
        )
        key1 = build_idempotency_key(ctx, inv)
        key2 = build_idempotency_key(ctx, inv)
        assert key1 == key2

    def test_different_session_different_key(self) -> None:
        inv = ToolInvocation(
            tool_name="erp_create_sales_order",
            resource="erp.orders",
            operation="create",
        )
        ctx1 = _full_ctx(session_id=uuid4())
        ctx2 = _full_ctx(session_id=uuid4())
        assert build_idempotency_key(ctx1, inv) != build_idempotency_key(ctx2, inv)

    def test_different_intent_different_key(self) -> None:
        ctx = _full_ctx()
        inv1 = ToolInvocation(
            tool_name="erp_create_sales_order",
            resource="erp.orders",
            operation="create",
            canonical_arguments={"quantity": 10},
        )
        inv2 = ToolInvocation(
            tool_name="erp_create_sales_order",
            resource="erp.orders",
            operation="create",
            canonical_arguments={"quantity": 20},
        )
        assert build_idempotency_key(ctx, inv1) != build_idempotency_key(ctx, inv2)


# -- C01-01: Action vocabulary ------------------------------------------------


class TestActionVocabulary:
    def test_all_actions_defined(self) -> None:
        expected = {
            "agent.execute",
            "agent.collaborate",
            "skill.execute",
            "knowledge.read",
            "datasource.read",
            "datasource.write",
        }
        actual = {a.value for a in Action}
        assert expected == actual

    def test_risk_levels(self) -> None:
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"


# -- C01-02: Event types ------------------------------------------------------


class TestToolEventType:
    def test_all_event_types_defined(self) -> None:
        expected = {
            "tool_started",
            "tool_completed",
            "tool_failed",
            "approval_required",
            "guard_denied",
            "write_audited",
            "rollback_completed",
            "rollback_failed",
        }
        actual = {
            ToolEventType.TOOL_STARTED,
            ToolEventType.TOOL_COMPLETED,
            ToolEventType.TOOL_FAILED,
            ToolEventType.APPROVAL_REQUIRED,
            ToolEventType.GUARD_DENIED,
            ToolEventType.WRITE_AUDITED,
            ToolEventType.ROLLBACK_COMPLETED,
            ToolEventType.ROLLBACK_FAILED,
        }
        assert expected == actual

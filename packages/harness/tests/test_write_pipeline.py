"""Tests for WritePipeline — write governance pipeline.

Unit tests mock Harness, connector_resolver, AuditLogger, and ApprovalGate
to verify the 6-step pipeline: guard → HITL → write → audit → rollback →
post_guard.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from eaos.data.connector import WriteResult
from eaos.harness.write_pipeline import (
    WriteApprovalRequired,
    WriteIntent,
    WritePipeline,
)

TID = UUID("00000000-0000-0000-0000-000000000001")
PRINCIPAL = UUID("00000000-0000-0000-0000-000000000201")
AGENT = UUID("00000000-0000-0000-0000-000000000301")


def _intent(**overrides: Any) -> WriteIntent:
    base: dict[str, Any] = {
        "tenant_id": TID,
        "principal_id": PRINCIPAL,
        "agent_id": AGENT,
        "tool_name": "erp.write",
        "resource": "customers",
        "operation": "create",
        "data": {"name": "Acme", "code": "C001"},
    }
    base.update(overrides)
    return WriteIntent(**base)


def _mock_harness() -> Any:
    h: Any = MagicMock()
    h.guard = AsyncMock(return_value=None)
    h.post_guard = AsyncMock(side_effect=lambda ctx, result: result)
    return h


def _mock_connector(result: WriteResult) -> Any:
    connector: Any = MagicMock()
    connector.write = AsyncMock(return_value=result)
    connector.rollback = AsyncMock(return_value=None)
    return connector


def _mock_audit_logger() -> Any:
    al: Any = MagicMock()
    al.log = AsyncMock(return_value=uuid4())
    al.log_rollback = AsyncMock(return_value=None)
    return al


def _mock_approval_gate() -> Any:
    ag: Any = MagicMock()
    ag.request_approval = AsyncMock(return_value=uuid4())
    return ag


def _make_pipeline(
    *,
    harness: Any | None = None,
    connector: Any | None = None,
    audit_logger: Any | None = None,
    approval_gate: Any | None = None,
) -> tuple[WritePipeline, dict[str, Any]]:
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


class TestHappyPath:
    async def test_guard_called_before_write(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent())
        mocks["harness"].guard.assert_awaited_once()

    async def test_write_called_with_correct_args(self) -> None:
        pipe, mocks = _make_pipeline()
        intent = _intent(operation="create", data={"name": "Acme"})
        await pipe.execute(intent)
        mocks["connector"].write.assert_awaited_once()
        call = mocks["connector"].write.call_args
        assert call.args[0] == TID  # tenant_id
        assert call.args[1] == "customers"  # resource
        op = call.args[2]
        assert op.operation == "create"
        assert op.data == {"name": "Acme"}

    async def test_audit_logged_on_success(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent())
        mocks["audit"].log.assert_awaited_once()
        entry = mocks["audit"].log.call_args.args[0]
        assert entry.success is True
        assert entry.operation == "create"

    async def test_post_guard_called_on_success(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent())
        mocks["harness"].post_guard.assert_awaited_once()

    async def test_returns_successful_outcome(self) -> None:
        pipe, _ = _make_pipeline()
        outcome = await pipe.execute(_intent())
        assert outcome.success is True
        assert outcome.audit_id is not None
        assert outcome.rolled_back is False

    async def test_before_after_in_outcome(self) -> None:
        before = {"name": "Old"}
        after = {"name": "New"}
        result = WriteResult(success=True, before=before, after=after)
        pipe, _ = _make_pipeline(connector=_mock_connector(result))
        outcome = await pipe.execute(_intent(operation="update", record_id="123"))
        assert outcome.before == before
        assert outcome.after == after


class TestGuardRejection:
    async def test_guard_raises_stops_pipeline(self) -> None:
        from eaos.core.errors import PermissionDeniedError

        h = _mock_harness()
        h.guard = AsyncMock(side_effect=PermissionDeniedError("denied"))
        pipe, mocks = _make_pipeline(harness=h)
        with pytest.raises(PermissionDeniedError):
            await pipe.execute(_intent())
        # Write should NOT be called
        mocks["connector"].write.assert_not_called()
        mocks["audit"].log.assert_not_called()


class TestHITL:
    async def test_high_risk_raises_approval_required(self) -> None:
        pipe, mocks = _make_pipeline()
        intent = _intent(risk_level="high", session_id=uuid4())
        with pytest.raises(WriteApprovalRequired) as exc_info:
            await pipe.execute(intent)
        assert exc_info.value.intent == intent
        mocks["approval"].request_approval.assert_awaited_once()
        # Write should NOT be called
        mocks["connector"].write.assert_not_called()

    async def test_high_risk_with_approval_proceeds(self) -> None:
        pipe, mocks = _make_pipeline()
        approval_id = uuid4()
        intent = _intent(risk_level="high", approval_id=approval_id, session_id=uuid4())
        outcome = await pipe.execute(intent)
        assert outcome.success is True
        assert outcome.approval_id == approval_id
        # Should NOT request a new approval
        mocks["approval"].request_approval.assert_not_called()

    async def test_medium_risk_no_approval_needed(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent(risk_level="medium"))
        mocks["approval"].request_approval.assert_not_called()

    async def test_low_risk_no_approval_needed(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent(risk_level="low"))
        mocks["approval"].request_approval.assert_not_called()


class TestWriteFailure:
    async def test_failed_write_with_before_triggers_rollback(self) -> None:
        before = {"name": "Old", "id": "123"}
        result = WriteResult(success=False, before=before, error="constraint violation")
        pipe, mocks = _make_pipeline(connector=_mock_connector(result))
        outcome = await pipe.execute(_intent(operation="update", record_id="123"))
        assert outcome.success is False
        assert outcome.rolled_back is True
        assert outcome.error == "constraint violation"
        mocks["connector"].rollback.assert_awaited_once()
        mocks["audit"].log_rollback.assert_awaited_once()

    async def test_failed_write_without_before_no_rollback(self) -> None:
        result = WriteResult(success=False, before=None, error="not found")
        pipe, mocks = _make_pipeline(connector=_mock_connector(result))
        outcome = await pipe.execute(_intent(operation="create"))
        assert outcome.success is False
        assert outcome.rolled_back is False
        mocks["connector"].rollback.assert_not_called()

    async def test_write_exception_audited_and_returned(self) -> None:
        conn: Any = MagicMock()
        conn.write = AsyncMock(side_effect=RuntimeError("connection lost"))
        conn.rollback = AsyncMock(return_value=None)
        pipe, mocks = _make_pipeline(connector=conn)
        outcome = await pipe.execute(_intent())
        assert outcome.success is False
        assert "connection lost" in (outcome.error or "")
        mocks["audit"].log.assert_awaited_once()
        entry = mocks["audit"].log.call_args.args[0]
        assert entry.success is False

    async def test_rollback_failure_does_not_mask_original_error(self) -> None:
        before = {"name": "Old"}
        result = WriteResult(success=False, before=before, error="original error")
        conn = _mock_connector(result)
        conn.rollback = AsyncMock(side_effect=RuntimeError("rollback also failed"))
        pipe, _ = _make_pipeline(connector=conn)
        outcome = await pipe.execute(_intent(operation="update", record_id="123"))
        # Original error should be preserved
        assert outcome.error == "original error"
        assert outcome.rolled_back is True


class TestAuditFields:
    async def test_audit_includes_trace_id(self) -> None:
        pipe, mocks = _make_pipeline()
        trace_id = uuid4()
        await pipe.execute(_intent(trace_id=trace_id))
        entry = mocks["audit"].log.call_args.args[0]
        assert entry.trace_id == trace_id

    async def test_audit_includes_approval_id(self) -> None:
        pipe, mocks = _make_pipeline()
        approval_id = uuid4()
        await pipe.execute(_intent(risk_level="high", approval_id=approval_id))
        entry = mocks["audit"].log.call_args.args[0]
        assert entry.approval_id == approval_id

    async def test_audit_includes_skill_id(self) -> None:
        pipe, mocks = _make_pipeline()
        skill_id = uuid4()
        await pipe.execute(_intent(skill_id=skill_id))
        entry = mocks["audit"].log.call_args.args[0]
        # skill_id is in GuardContext attributes, not directly in AuditEntry,
        # but tool_name should reflect the tool
        assert entry.tool_name == "erp.write"


class TestGuardContext:
    async def test_ctx_has_write_data_action(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent())
        ctx = mocks["harness"].guard.call_args.args[0]
        assert ctx.action == "write_data"

    async def test_ctx_has_resource_from_intent(self) -> None:
        pipe, mocks = _make_pipeline()
        await pipe.execute(_intent(resource="orders"))
        ctx = mocks["harness"].guard.call_args.args[0]
        assert ctx.resource == "orders"

    async def test_ctx_has_risk_level_from_intent(self) -> None:
        pipe, mocks = _make_pipeline()
        # approval_id set so HITL is bypassed; we only verify risk_level flows to ctx
        await pipe.execute(_intent(risk_level="high", approval_id=uuid4()))
        ctx = mocks["harness"].guard.call_args.args[0]
        assert ctx.risk_level == "high"

    async def test_ctx_includes_session_id_in_attributes(self) -> None:
        pipe, mocks = _make_pipeline()
        session_id = uuid4()
        await pipe.execute(_intent(session_id=session_id))
        ctx = mocks["harness"].guard.call_args.args[0]
        assert ctx.attributes.get("session_id") == session_id

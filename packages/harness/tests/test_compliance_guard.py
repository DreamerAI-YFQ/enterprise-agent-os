"""Tests for ComplianceGuardImpl — PII redaction, audit, rollback."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from eaos.core.errors import NotFoundError
from eaos.harness.compliance.guard import ComplianceGuardImpl, _redact_pii
from eaos.harness.context import GuardContext


class FakeComplianceDb:
    """In-memory ComplianceDb that records execute calls."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _ctx(
    *,
    action: str = "execute",
    resource: str = "skill",
    attributes: dict[str, Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        action=action,
        resource=resource,
        attributes=attributes or {},
    )


class TestPreCheck:
    async def test_redacts_email(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Contact john@example.com for details"

        result = await guard.pre_check(_ctx(), text)

        assert result is not None
        assert "[REDACTED_EMAIL]" in result
        assert "john@example.com" not in result

    async def test_redacts_phone(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Call 555-123-4567 now"

        result = await guard.pre_check(_ctx(), text)

        assert result is not None
        assert "[REDACTED_PHONE]" in result

    async def test_redacts_ssn(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "SSN: 123-45-6789"

        result = await guard.pre_check(_ctx(), text)

        assert result is not None
        assert "[REDACTED_SSN]" in result

    async def test_redacts_card_number(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Card: 4111111111111111"

        result = await guard.pre_check(_ctx(), text)

        assert result is not None
        assert "[REDACTED_CARD]" in result

    async def test_returns_none_when_no_text(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())

        result = await guard.pre_check(_ctx(), None)

        assert result is None

    async def test_no_pii_returns_unchanged(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Hello world, no PII here"

        result = await guard.pre_check(_ctx(), text)

        assert result == text

    async def test_redacts_multiple_pii_types(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Email: jane@corp.io, Phone: 555.987.6543, SSN: 987-65-4321"

        result = await guard.pre_check(_ctx(), text)

        assert result is not None
        assert "[REDACTED_EMAIL]" in result
        assert "[REDACTED_PHONE]" in result
        assert "[REDACTED_SSN]" in result


class TestPostCheck:
    async def test_redacts_output(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "Send reply to admin@company.com"

        result = await guard.post_check(_ctx(), text)

        assert "[REDACTED_EMAIL]" in result
        assert "admin@company.com" not in result

    async def test_clean_output_unchanged(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        text = "The task is complete."

        result = await guard.post_check(_ctx(), text)

        assert result == text


class TestAudit:
    async def test_inserts_audit_log_with_dict_result(self) -> None:
        db = FakeComplianceDb()
        guard = ComplianceGuardImpl(db)

        await guard.audit(_ctx(action="invoke", resource="agent"), {"status": "ok", "tokens": 42})

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.audit_logs" in sql
        assert "tenant_id" in sql
        assert "actor_type" in sql
        assert "detail" in sql

    async def test_inserts_audit_log_with_string_result(self) -> None:
        db = FakeComplianceDb()
        guard = ComplianceGuardImpl(db)

        await guard.audit(_ctx(), "Task completed successfully")

        assert len(db.executed) == 1
        _, params = db.executed[0]
        # Find the JSON detail param
        import json

        detail_json = next(p for p in params if isinstance(p, str) and p.startswith("{"))
        detail = json.loads(detail_json)
        assert "result" in detail
        assert detail["result"] == "Task completed successfully"

    async def test_inserts_audit_log_with_object_result(self) -> None:
        db = FakeComplianceDb()
        guard = ComplianceGuardImpl(db)

        await guard.audit(_ctx(), 42)

        assert len(db.executed) == 1
        import json

        _, params = db.executed[0]
        detail_json = next(p for p in params if isinstance(p, str) and p.startswith("{"))
        detail = json.loads(detail_json)
        assert detail["result_type"] == "int"

    async def test_includes_ip_address_from_attributes(self) -> None:
        db = FakeComplianceDb()
        guard = ComplianceGuardImpl(db)
        ctx = _ctx(attributes={"ip_address": "192.168.1.1"})

        await guard.audit(ctx, "result")

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert "192.168.1.1" in params

    async def test_truncates_long_string_result(self) -> None:
        db = FakeComplianceDb()
        guard = ComplianceGuardImpl(db)
        long_text = "x" * 1000

        await guard.audit(_ctx(), long_text)

        assert len(db.executed) == 1
        import json

        _, params = db.executed[0]
        detail_json = next(p for p in params if isinstance(p, str) and p.startswith("{"))
        detail = json.loads(detail_json)
        assert len(detail["result"]) == 500


class TestSnapshotAndRollback:
    async def test_snapshot_returns_id(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())

        snapshot_id = await guard.snapshot_before(_ctx(), uuid4(), "record-123")

        assert isinstance(snapshot_id, str)
        assert len(snapshot_id) > 0

    async def test_rollback_removes_snapshot(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        ctx = _ctx(action="write_data", resource="datasource")
        snapshot_id = await guard.snapshot_before(ctx, uuid4(), "record-456")

        await guard.rollback(snapshot_id)

        # Second rollback should fail (already consumed)
        with pytest.raises(NotFoundError):
            await guard.rollback(snapshot_id)

    async def test_rollback_unknown_snapshot_raises(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())

        with pytest.raises(NotFoundError, match="not found"):
            await guard.rollback("nonexistent-id")

    async def test_snapshot_stores_context(self) -> None:
        guard = ComplianceGuardImpl(FakeComplianceDb())
        ctx = _ctx(action="write", resource="datasource")
        ds_id = uuid4()

        snapshot_id = await guard.snapshot_before(ctx, ds_id, "rec-789")

        # Verify snapshot is stored (indirectly via rollback not raising)
        await guard.rollback(snapshot_id)


class TestRedactPiiFunction:
    def test_email_redaction(self) -> None:
        assert _redact_pii("user@domain.com") == "[REDACTED_EMAIL]"

    def test_multiple_emails(self) -> None:
        text = "a@b.com and c@d.com"
        result = _redact_pii(text)
        assert result == "[REDACTED_EMAIL] and [REDACTED_EMAIL]"

    def test_phone_formats(self) -> None:
        assert "[REDACTED_PHONE]" in _redact_pii("555-123-4567")
        assert "[REDACTED_PHONE]" in _redact_pii("555.123.4567")
        assert "[REDACTED_PHONE]" in _redact_pii("5551234567")

    def test_no_false_positives_on_short_numbers(self) -> None:
        assert _redact_pii("12345") == "12345"

    def test_preserves_non_pii_text(self) -> None:
        text = "The quick brown fox jumps over the lazy dog."
        assert _redact_pii(text) == text

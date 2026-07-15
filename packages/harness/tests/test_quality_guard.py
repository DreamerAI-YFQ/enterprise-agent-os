"""Tests for QualityGuardImpl — metric recording, success-rate gating, adoption."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from eaos.core.errors import QualityViolationError
from eaos.harness.context import GuardContext
from eaos.harness.quality.guard import (
    QualityGuardImpl,
    _is_failure,
    _to_uuid,
)


class FakeQualityDb:
    """In-memory QualityDb with configurable metric rows per skill_id."""

    def __init__(self, rows: dict[UUID, dict[str, Any]] | None = None) -> None:
        self._rows = rows or {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        # params: (tenant_id, skill_id) for check_skill_quality
        skill_id = params[1]
        key = skill_id if isinstance(skill_id, UUID) else UUID(str(skill_id))
        return self._rows.get(key)

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _ctx(
    *,
    resource_id: UUID | None = None,
    attributes: dict[str, Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        resource="skill",
        resource_id=resource_id,
        attributes=attributes or {},
    )


def _metric_row(
    *,
    call_count: int = 0,
    failure_count: int = 0,
) -> dict[str, Any]:
    return {"call_count": call_count, "failure_count": failure_count}


class TestEvaluate:
    async def test_no_skill_id_returns_early(self) -> None:
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(), result={"status": "ok"})

        assert db.executed == []  # no metric recorded

    async def test_success_records_metric_with_success_count_1(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "ok"})

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.quality_metrics" in sql
        # params: tenant_id, skill_id, today, success_count=1, failure_count=0
        assert params[3] == 1  # success_count
        assert params[4] == 0  # failure_count

    async def test_failure_dict_error_status_records_failure(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "error"})

        sql, params = db.executed[0]
        assert params[3] == 0  # success_count
        assert params[4] == 1  # failure_count

    async def test_failure_dict_failed_status_records_failure(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "failed"})

        sql, _params = db.executed[0]
        assert "ON CONFLICT" in sql

    async def test_failure_exception_records_failure(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result=ValueError("boom"))

        _, params = db.executed[0]
        assert params[4] == 1  # failure_count

    async def test_failure_string_records_failure(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result="error: something broke")

        _, params = db.executed[0]
        assert params[4] == 1

    async def test_healthy_skill_does_not_raise(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(rows={skill_id: _metric_row(call_count=20, failure_count=2)})
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "ok"})

    async def test_deprecated_skill_raises_quality_violation(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=20, failure_count=10)}  # 50% > 30%
        )
        guard = QualityGuardImpl(db)

        with pytest.raises(QualityViolationError, match="deprecated"):
            await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "ok"})

    async def test_skill_with_few_samples_does_not_raise(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=5, failure_count=5)}  # 100% but < 10 samples
        )
        guard = QualityGuardImpl(db)

        await guard.evaluate(_ctx(resource_id=skill_id), result={"status": "ok"})

    async def test_skill_id_from_attributes_when_no_resource_id(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(
            _ctx(attributes={"skill_id": skill_id}),
            result={"status": "ok"},
        )

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert params[1] == skill_id

    async def test_skill_id_as_string_is_converted(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.evaluate(
            _ctx(attributes={"skill_id": str(skill_id)}),
            result={"status": "ok"},
        )

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert params[1] == skill_id  # converted to UUID


class TestCheckHallucination:
    async def test_returns_true_stub(self) -> None:
        guard = QualityGuardImpl(FakeQualityDb())

        result = await guard.check_hallucination(_ctx(), output="some output")

        assert result is True

    async def test_returns_true_even_for_empty_output(self) -> None:
        guard = QualityGuardImpl(FakeQualityDb())

        result = await guard.check_hallucination(_ctx(), output="")

        assert result is True


class TestCheckSkillQuality:
    async def test_no_data_returns_true(self) -> None:
        guard = QualityGuardImpl(FakeQualityDb(rows={}))

        result = await guard.check_skill_quality(uuid4(), uuid4())

        assert result is True

    async def test_below_threshold_returns_true(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=20, failure_count=2)}  # 10% < 30%
        )
        guard = QualityGuardImpl(db)

        result = await guard.check_skill_quality(skill_id, uuid4())

        assert result is True

    async def test_at_threshold_returns_false(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=20, failure_count=6)}  # 30% == threshold
        )
        guard = QualityGuardImpl(db)

        result = await guard.check_skill_quality(skill_id, uuid4())

        assert result is False  # failure_rate < threshold is False at ==

    async def test_above_threshold_returns_false(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=20, failure_count=10)}  # 50% > 30%
        )
        guard = QualityGuardImpl(db)

        result = await guard.check_skill_quality(skill_id, uuid4())

        assert result is False

    async def test_below_min_samples_returns_true(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=9, failure_count=9)}  # 100% but < 10 samples
        )
        guard = QualityGuardImpl(db)

        result = await guard.check_skill_quality(skill_id, uuid4())

        assert result is True

    async def test_exactly_min_samples_with_high_failure_returns_false(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb(
            rows={skill_id: _metric_row(call_count=10, failure_count=5)}  # 50% >= 10 samples
        )
        guard = QualityGuardImpl(db)

        result = await guard.check_skill_quality(skill_id, uuid4())

        assert result is False


class TestRecordAdoption:
    async def test_no_skill_id_returns_early(self) -> None:
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.record_adoption(_ctx(), output="x", user_modified_pct=0.1)

        assert db.executed == []

    async def test_updates_adoption_rate(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.record_adoption(
            _ctx(resource_id=skill_id),
            output="x",
            user_modified_pct=0.25,
        )

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "UPDATE harness.quality_metrics" in sql
        assert "adoption_rate = :p0" in sql
        assert params[0] == 0.25
        assert params[1] == params[1]  # tenant_id
        assert params[2] == skill_id

    async def test_skill_id_from_attributes(self) -> None:
        skill_id = uuid4()
        db = FakeQualityDb()
        guard = QualityGuardImpl(db)

        await guard.record_adoption(
            _ctx(attributes={"skill_id": skill_id}),
            output="x",
            user_modified_pct=0.5,
        )

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert params[2] == skill_id


class TestIsFailureHelper:
    def test_exception_is_failure(self) -> None:
        assert _is_failure(ValueError("boom")) is True

    def test_dict_error_status(self) -> None:
        assert _is_failure({"status": "error"}) is True

    def test_dict_failed_status(self) -> None:
        assert _is_failure({"status": "failed"}) is True

    def test_dict_ok_status_not_failure(self) -> None:
        assert _is_failure({"status": "ok"}) is False

    def test_dict_no_status_not_failure(self) -> None:
        assert _is_failure({"key": "value"}) is False

    def test_string_error_prefix(self) -> None:
        assert _is_failure("error: something broke") is True

    def test_string_no_error_prefix(self) -> None:
        assert _is_failure("all good") is False

    def test_other_type_not_failure(self) -> None:
        assert _is_failure(42) is False
        assert _is_failure(None) is False


class TestToUuidHelper:
    def test_passthrough_uuid(self) -> None:
        u = uuid4()
        assert _to_uuid(u) is u

    def test_converts_string(self) -> None:
        u = uuid4()
        assert _to_uuid(str(u)) == u

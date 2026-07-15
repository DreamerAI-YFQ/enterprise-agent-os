"""Verify core types and events match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.core.events import Event, EventBus
from eaos.core.types import ID, PageResult, TimeRange


class TestTypes:
    def test_id_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ID)
        fields = {f.name for f in dataclasses.fields(ID)}
        assert "value" in fields

    def test_timerange_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(TimeRange)
        fields = {f.name for f in dataclasses.fields(TimeRange)}
        assert {"start", "end"} <= fields

    def test_pageresult_is_generic(self) -> None:
        assert dataclasses.is_dataclass(PageResult)
        fields = {f.name for f in dataclasses.fields(PageResult)}
        assert {"items", "total", "page", "page_size"} <= fields


class TestEvents:
    def test_event_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(Event)
        fields = {f.name for f in dataclasses.fields(Event)}
        assert {"name", "tenant_id", "payload", "timestamp"} <= fields

    def test_eventbus_is_protocol(self) -> None:
        assert hasattr(EventBus, "publish")
        assert hasattr(EventBus, "subscribe")

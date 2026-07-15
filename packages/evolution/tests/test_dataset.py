"""Tests for PreferenceDatasetBuilderImpl — chosen/rejected pairing from feedback."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from eaos.evolution.dataset import (
    Dataset,
    PreferenceDatasetBuilderImpl,
    PreferencePair,
    _extract_output,
    _prompt_hash,
)


def _signal_row(
    *,
    prompt: str,
    signal_value: str,
    output: str,
    strength: float = 0.8,
    trace_id: Any = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "trace_id": trace_id or uuid4(),
        "span_id": uuid4(),
        "user_id": uuid4(),
        "agent_id": uuid4(),
        "signal_type": "adopted" if signal_value == "positive" else "reasked",
        "signal_value": signal_value,
        "strength": strength,
        "captured_at": datetime(2026, 1, 1),
        "prompt": prompt,
        "span_attrs": {"output": output},
    }


class TestExtractOutput:
    def test_dict_with_output(self) -> None:
        assert _extract_output({"output": "hello"}) == "hello"

    def test_dict_with_response_fallback(self) -> None:
        assert _extract_output({"response": "world"}) == "world"

    def test_json_string(self) -> None:
        assert _extract_output(json.dumps({"output": "parsed"})) == "parsed"

    def test_empty_dict(self) -> None:
        assert _extract_output({}) == ""

    def test_none(self) -> None:
        assert _extract_output(None) == ""


class TestPromptHash:
    def test_case_insensitive(self) -> None:
        assert _prompt_hash("Hello") == _prompt_hash("hello")

    def test_strips_whitespace(self) -> None:
        assert _prompt_hash("  hello  ") == _prompt_hash("hello")

    def test_different_prompts_differ(self) -> None:
        assert _prompt_hash("hello") != _prompt_hash("world")


class TestBuild:
    async def test_pairs_positive_and_negative(self) -> None:
        tenant = uuid4()
        trace_id = uuid4()
        rows = [
            _signal_row(
                prompt="查询销售报表",
                signal_value="positive",
                output="这是销售报表",
                trace_id=trace_id,
            ),
            _signal_row(
                prompt="查询销售报表",
                signal_value="negative",
                output="报表生成失败",
                trace_id=trace_id,
            ),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        dataset_id = await builder.build(tenant, name="test-ds")

        assert dataset_id is not None
        # 1 INSERT pair + 1 UPDATE pair_count
        assert db.execute.await_count == 2
        update_call = db.execute.call_args_list[-1]
        assert "UPDATE evolution.datasets SET pair_count" in update_call.args[0]
        assert update_call.args[1] == 1  # one pair

    async def test_only_positive_skipped(self) -> None:
        rows = [
            _signal_row(prompt="hello", signal_value="positive", output="hi"),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        await builder.build(uuid4(), name="empty-ds")

        # Only UPDATE pair_count=0, no pair INSERTs
        assert db.execute.await_count == 1
        assert db.execute.call_args.args[1] == 0

    async def test_only_negative_skipped(self) -> None:
        rows = [
            _signal_row(prompt="hello", signal_value="negative", output="bad"),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        await builder.build(uuid4())

        assert db.execute.await_count == 1
        assert db.execute.call_args.args[1] == 0

    async def test_empty_output_skipped(self) -> None:
        rows = [
            _signal_row(prompt="hello", signal_value="positive", output=""),
            _signal_row(prompt="hello", signal_value="negative", output="bad"),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        await builder.build(uuid4())

        assert db.execute.call_args.args[1] == 0

    async def test_different_prompts_not_paired(self) -> None:
        rows = [
            _signal_row(prompt="query A", signal_value="positive", output="a"),
            _signal_row(prompt="query B", signal_value="negative", output="b"),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        await builder.build(uuid4())

        # Different prompt hashes → no pairing
        assert db.execute.call_args.args[1] == 0

    async def test_default_name_generated(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = []
        db.fetch_one.return_value = {"id": uuid4()}

        builder = PreferenceDatasetBuilderImpl(db)
        await builder.build(uuid4())

        # fetch_one called with INSERT dataset; name param is :p1
        name_arg = db.fetch_one.call_args.args[2]
        assert name_arg.startswith("dataset-")

    async def test_fetch_one_returns_none_raises(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = []
        db.fetch_one.return_value = None

        builder = PreferenceDatasetBuilderImpl(db)
        with pytest.raises(RuntimeError, match="dataset insert returned no id"):
            await builder.build(uuid4())


class TestGetPairs:
    async def test_returns_pairs_paginated(self) -> None:
        dataset_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "id": uuid4(),
                "dataset_id": dataset_id,
                "tenant_id": uuid4(),
                "prompt": "q",
                "chosen": "good",
                "rejected": "bad",
                "source_trace_id": uuid4(),
                "created_at": datetime(2026, 1, 1),
            }
        ]

        builder = PreferenceDatasetBuilderImpl(db)
        pairs = await builder.get_pairs(dataset_id, limit=10, offset=5)

        assert len(pairs) == 1
        assert pairs[0].chosen == "good"
        sql, *params = db.fetch.call_args.args
        assert "LIMIT :p1 OFFSET :p2" in sql
        assert params[1] == 10
        assert params[2] == 5


class TestGetDataset:
    async def test_found(self) -> None:
        db = AsyncMock()
        db.fetch_one.return_value = {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "name": "ds",
            "pair_count": 5,
            "created_at": datetime(2026, 1, 1),
        }
        builder = PreferenceDatasetBuilderImpl(db)
        ds = await builder.get_dataset(uuid4())
        assert ds.pair_count == 5

    async def test_not_found_raises(self) -> None:
        db = AsyncMock()
        db.fetch_one.return_value = None
        builder = PreferenceDatasetBuilderImpl(db)
        with pytest.raises(KeyError, match="not found"):
            await builder.get_dataset(uuid4())


class TestListDatasets:
    async def test_returns_list(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "id": uuid4(),
                "tenant_id": uuid4(),
                "name": "ds1",
                "pair_count": 3,
                "created_at": datetime(2026, 1, 1),
            }
        ]
        builder = PreferenceDatasetBuilderImpl(db)
        datasets = await builder.list_datasets(uuid4())
        assert len(datasets) == 1
        assert isinstance(datasets[0], Dataset)


class TestValidatePair:
    async def test_valid_pair(self) -> None:
        builder = PreferenceDatasetBuilderImpl(AsyncMock())
        pair = PreferencePair(
            prompt="q", chosen="good", rejected="bad", confidence=0.8
        )
        assert await builder.validate_pair(pair) is True

    async def test_empty_prompt(self) -> None:
        builder = PreferenceDatasetBuilderImpl(AsyncMock())
        pair = PreferencePair(
            prompt="", chosen="good", rejected="bad", confidence=0.8
        )
        assert await builder.validate_pair(pair) is False

    async def test_low_confidence(self) -> None:
        builder = PreferenceDatasetBuilderImpl(AsyncMock())
        pair = PreferencePair(
            prompt="q", chosen="good", rejected="bad", confidence=0.3
        )
        assert await builder.validate_pair(pair) is False

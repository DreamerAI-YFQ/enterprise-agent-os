"""Unit tests for LLMReranker — mock LLMRouter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from eaos.infra.llm.base import LLMResponse, Message
from eaos.knowledge.rag.pipeline import Chunk
from eaos.knowledge.rag.reranker import LLMReranker


def _make_reranker(llm_content: str = '{"ranked": [0]}') -> tuple[LLMReranker, Any]:
    llm: Any = MagicMock()
    llm.chat = AsyncMock(return_value=LLMResponse(content=llm_content))
    return LLMReranker(llm), llm


def _make_chunk(content: str = "content") -> Chunk:
    return Chunk(
        id=uuid4(),
        document_id=uuid4(),
        tenant_id=uuid4(),
        chunk_index=0,
        content=content,
        token_count=10,
    )


class TestRerank:
    async def test_empty_chunks_returns_empty(self) -> None:
        reranker, llm = _make_reranker()
        result = await reranker.rerank("q", [], top_k=5)
        assert result == []
        llm.chat.assert_not_awaited()

    async def test_single_chunk_returned_without_llm(self) -> None:
        reranker, llm = _make_reranker()
        chunk = _make_chunk("only")
        result = await reranker.rerank("q", [chunk], top_k=5)
        assert len(result) == 1
        llm.chat.assert_not_awaited()

    async def test_reranks_by_llm_order(self) -> None:
        content = json.dumps({"ranked": [2, 0, 1]})
        reranker, _ = _make_reranker(content)
        chunks = [_make_chunk("a"), _make_chunk("b"), _make_chunk("c")]
        result = await reranker.rerank("q", chunks, top_k=3)
        assert result[0].content == "c"
        assert result[1].content == "a"
        assert result[2].content == "b"

    async def test_top_k_limits_result(self) -> None:
        content = json.dumps({"ranked": [2, 0, 1]})
        reranker, _ = _make_reranker(content)
        chunks = [_make_chunk("a"), _make_chunk("b"), _make_chunk("c")]
        result = await reranker.rerank("q", chunks, top_k=2)
        assert len(result) == 2
        assert result[0].content == "c"
        assert result[1].content == "a"

    async def test_fallback_on_invalid_json(self) -> None:
        reranker, _ = _make_reranker("not json")
        chunks = [_make_chunk("a"), _make_chunk("b")]
        result = await reranker.rerank("q", chunks, top_k=5)
        assert len(result) == 2
        assert result[0].content == "a"

    async def test_fallback_when_ranked_missing(self) -> None:
        reranker, _ = _make_reranker('{"other": [1, 0]}')
        chunks = [_make_chunk("a"), _make_chunk("b")]
        result = await reranker.rerank("q", chunks, top_k=5)
        assert len(result) == 2
        assert result[0].content == "a"

    async def test_invalid_indices_skipped(self) -> None:
        content = json.dumps({"ranked": [0, 99, 1, -1]})
        reranker, _ = _make_reranker(content)
        chunks = [_make_chunk("a"), _make_chunk("b")]
        result = await reranker.rerank("q", chunks, top_k=5)
        assert result[0].content == "a"
        assert result[1].content == "b"

    async def test_unranked_chunks_appended(self) -> None:
        content = json.dumps({"ranked": [1]})
        reranker, _ = _make_reranker(content)
        chunks = [_make_chunk("a"), _make_chunk("b"), _make_chunk("c")]
        result = await reranker.rerank("q", chunks, top_k=5)
        assert result[0].content == "b"
        assert len(result) == 3

    async def test_system_and_user_messages(self) -> None:
        reranker, llm = _make_reranker()
        chunks = [_make_chunk("alpha"), _make_chunk("beta")]
        await reranker.rerank("my query", chunks, top_k=5)
        call = llm.chat.call_args
        messages: list[Message] = call.args[0]
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert "my query" in messages[1].content
        assert "alpha" in messages[1].content

    async def test_uses_zero_temperature(self) -> None:
        reranker, llm = _make_reranker()
        chunks = [_make_chunk("a"), _make_chunk("b")]
        await reranker.rerank("q", chunks, top_k=5)
        assert llm.chat.call_args.kwargs["temperature"] == 0.0

    async def test_content_truncated_to_200_chars(self) -> None:
        reranker, llm = _make_reranker()
        long = "x" * 500
        chunks = [_make_chunk(long), _make_chunk("short")]
        await reranker.rerank("q", chunks, top_k=5)
        messages: list[Message] = llm.chat.call_args.args[0]
        user_msg = messages[1].content
        assert "x" * 200 in user_msg
        assert "x" * 201 not in user_msg

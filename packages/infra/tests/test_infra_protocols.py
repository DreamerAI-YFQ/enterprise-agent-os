"""Verify infra Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.infra.db.base import DbClient
from eaos.infra.db.redis_base import RedisClient
from eaos.infra.llm.base import LLMClient, LLMResponse, Message, ModelSelection
from eaos.infra.llm.router import LLMRouter
from eaos.infra.repository import Repository
from eaos.infra.storage.base import FileStorage
from eaos.infra.vector.base import Embedder, VectorSearchResult, VectorStore


class TestDbClient:
    def test_protocol_methods(self) -> None:
        for method in ("session", "fetch", "fetch_one", "execute"):
            assert hasattr(DbClient, method), f"DbClient missing {method}"


class TestRedisClient:
    def test_protocol_methods(self) -> None:
        for method in ("get", "set", "delete", "incrby", "exists"):
            assert hasattr(RedisClient, method), f"RedisClient missing {method}"


class TestLLMProtocols:
    def test_message_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Message)}
        assert {"role", "content", "tool_calls", "tool_call_id"} <= fields

    def test_llmresponse_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(LLMResponse)}
        assert {
            "content",
            "tool_calls",
            "prompt_tokens",
            "completion_tokens",
            "model",
            "finish_reason",
        } <= fields

    def test_modelselection_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(ModelSelection)}
        assert {"provider", "model"} <= fields

    def test_llmclient_methods(self) -> None:
        for method in ("chat", "stream", "vision"):
            assert hasattr(LLMClient, method)


class TestLLMRouter:
    def test_protocol_methods(self) -> None:
        for method in ("chat", "stream", "vision"):
            assert hasattr(LLMRouter, method)


class TestVectorStore:
    def test_protocol_methods(self) -> None:
        for method in ("search", "insert", "delete"):
            assert hasattr(VectorStore, method)

    def test_vectorsearchresult_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(VectorSearchResult)}
        assert {"id", "content", "score", "metadata"} <= fields


class TestEmbedder:
    def test_protocol_methods(self) -> None:
        for method in ("embed", "embed_batch"):
            assert hasattr(Embedder, method)
        assert hasattr(Embedder, "dimension")


class TestFileStorage:
    def test_protocol_methods(self) -> None:
        for method in ("upload", "download", "delete", "get_signed_url"):
            assert hasattr(FileStorage, method)


class TestRepository:
    def test_protocol_methods(self) -> None:
        for method in ("get", "list", "create", "update", "delete"):
            assert hasattr(Repository, method)

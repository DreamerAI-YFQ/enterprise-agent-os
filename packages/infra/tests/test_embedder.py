"""Tests for OpenAIEmbedder."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest
from eaos.core.config import EmbeddingConfig
from eaos.core.errors import LLMError
from eaos.infra.vector.embedder import OpenAIEmbedder
from httpx import Request, Response


def _make_embedder(**overrides: Any) -> tuple[OpenAIEmbedder, Any]:
    """Build an embedder with a mock AsyncOpenAI client injected.

    Returns (embedder, mock_client) so tests can configure the mock without
    mypy complaining about real AsyncOpenAI attribute types.
    """
    kwargs: dict[str, Any] = {
        "api_key": "sk-test",
        "model": "text-embedding-3-small",
        "dimensions": 1024,
    }
    kwargs.update(overrides)
    config = EmbeddingConfig(**kwargs)
    embedder = OpenAIEmbedder(config)

    mock_client: Any = MagicMock()
    mock_client.embeddings = MagicMock()
    mock_client.embeddings.create = AsyncMock()
    embedder._client = mock_client
    return embedder, mock_client


def _embedding_response(vectors: list[list[float]]) -> Any:
    """Build a mock openai embeddings response object."""
    data = [MagicMock(embedding=v, index=i) for i, v in enumerate(vectors)]
    return MagicMock(data=data)


class TestOpenAIEmbedderProperties:
    def test_dimension_returns_configured(self) -> None:
        embedder, _ = _make_embedder(dimensions=512)
        assert embedder.dimension == 512

    def test_dimension_defaults_to_1024(self) -> None:
        embedder, _ = _make_embedder()
        assert embedder.dimension == 1024

    def test_model_name_returns_configured(self) -> None:
        embedder, _ = _make_embedder(model="text-embedding-3-large")
        assert embedder.model_name == "text-embedding-3-large"


class TestOpenAIEmbedderEmbed:
    async def test_embed_returns_vector(self) -> None:
        embedder, client = _make_embedder()
        vec = [0.1] * 1024
        client.embeddings.create.return_value = _embedding_response([vec])
        result = await embedder.embed("hello")
        assert result == vec

    async def test_embed_passes_model_and_dimensions(self) -> None:
        embedder, client = _make_embedder(model="text-embedding-3-small", dimensions=1024)
        client.embeddings.create.return_value = _embedding_response([[0.0] * 1024])
        await embedder.embed("hi")
        call_kwargs = client.embeddings.create.call_args.kwargs
        assert call_kwargs["model"] == "text-embedding-3-small"
        assert call_kwargs["dimensions"] == 1024
        assert call_kwargs["input"] == "hi"


class TestOpenAIEmbedderBatch:
    async def test_empty_batch_returns_empty(self) -> None:
        embedder, client = _make_embedder()
        result = await embedder.embed_batch([])
        assert result == []
        client.embeddings.create.assert_not_called()

    async def test_batch_preserves_order(self) -> None:
        embedder, client = _make_embedder()
        v1 = [0.1] * 4
        v2 = [0.2] * 4
        v3 = [0.3] * 4
        # Return shuffled by index to verify we sort back to input order.
        shuffled = [
            MagicMock(embedding=v3, index=2),
            MagicMock(embedding=v1, index=0),
            MagicMock(embedding=v2, index=1),
        ]
        client.embeddings.create.return_value = MagicMock(data=shuffled)
        result = await embedder.embed_batch(["a", "b", "c"])
        assert result == [v1, v2, v3]


class TestOpenAIEmbedderErrors:
    async def test_embed_translates_api_error_to_llm_error(self) -> None:
        embedder, client = _make_embedder()
        client.embeddings.create.side_effect = openai.OpenAIError("boom")
        with pytest.raises(LLMError):
            await embedder.embed("x")

    async def test_batch_translates_api_error_to_llm_error(self) -> None:
        embedder, client = _make_embedder()
        client.embeddings.create.side_effect = openai.OpenAIError("boom")
        with pytest.raises(LLMError):
            await embedder.embed_batch(["x", "y"])

    async def test_unsupported_dimensions_is_cached_after_first_retry(self) -> None:
        embedder, first_client = _make_embedder()
        second_client: Any = MagicMock()
        second_client.embeddings = MagicMock()
        second_client.embeddings.create = AsyncMock(
            side_effect=[
                _embedding_response([[0.1]]),
                _embedding_response([[0.2]]),
                _embedding_response([[0.3]]),
            ]
        )
        request = Request("POST", "https://embedding.example/v1/embeddings")
        response = Response(400, request=request)
        first_client.embeddings.create.side_effect = openai.BadRequestError(
            "dimensions unsupported",
            response=response,
            body={"message": "invalid parameter"},
        )
        embedder._get_client = MagicMock(  # type: ignore[method-assign]
            side_effect=[first_client, second_client, second_client, second_client]
        )

        assert await embedder.embed("first") == [0.1]
        assert await embedder.embed("second") == [0.2]
        assert await embedder.embed("third") == [0.3]

        assert "dimensions" in first_client.embeddings.create.call_args.kwargs
        for call in second_client.embeddings.create.call_args_list:
            assert "dimensions" not in call.kwargs


class TestOpenAIEmbedderClientConstruction:
    def test_base_url_applied_for_zhipu(self) -> None:
        # Construction with a Zhipu base_url must not raise; client is lazy so
        # no network call happens here.
        config = EmbeddingConfig(
            api_key="zhipu-key",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="embedding-3",
            dimensions=1024,
        )
        embedder = OpenAIEmbedder(config)
        assert embedder.model_name == "embedding-3"
        assert embedder.dimension == 1024

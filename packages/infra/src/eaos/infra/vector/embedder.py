"""OpenAI-compatible embedding adapter (API-based, no local model).

Works with OpenAI, Zhipu/GLM (OpenAI-compatible endpoint), or any local
embeddings server exposing the OpenAI embeddings API. Dimensions default to
1024 to match the schema's vector(1024) columns.

The openai SDK is sync/async split; we use AsyncOpenAI. The Embedder protocol
is async throughout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import openai
from eaos.core.errors import LLMError

if TYPE_CHECKING:
    from eaos.core.config import EmbeddingConfig


class OpenAIEmbedder:
    """Embedder backed by an OpenAI-compatible embeddings API.

    Lazily constructs the AsyncOpenAI client on first use so importing the
    module does not require credentials. Raises LLMError on API failure.
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._client: openai.AsyncOpenAI | None = None

    @property
    def dimension(self) -> int:
        return self._config.dimensions

    @property
    def model_name(self) -> str:
        return self._config.model

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self._config.request_timeout_sec}
            if self._config.api_key is not None:
                kwargs["api_key"] = self._config.api_key
            if self._config.base_url is not None:
                kwargs["base_url"] = self._config.base_url
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    async def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns a float vector of length `dimension`."""
        client = self._get_client()
        try:
            try:
                response = await client.embeddings.create(
                    input=text,
                    model=self._config.model,
                    dimensions=self._config.dimensions,
                )
            except openai.BadRequestError:
                # Some providers (e.g. SiliconFlow BAAI/bge-m3) don't accept
                # the dimensions param — retry without it.
                response = await client.embeddings.create(
                    input=text,
                    model=self._config.model,
                )
        except openai.OpenAIError as exc:
            raise LLMError(f"embedding call failed: {exc}") from exc
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call. Order preserved."""
        if not texts:
            return []
        client = self._get_client()
        try:
            try:
                response = await client.embeddings.create(
                    input=texts,
                    model=self._config.model,
                    dimensions=self._config.dimensions,
                )
            except openai.BadRequestError:
                response = await client.embeddings.create(
                    input=texts,
                    model=self._config.model,
                )
        except openai.OpenAIError as exc:
            raise LLMError(f"embedding batch call failed: {exc}") from exc
        # API returns data sorted by index; sort defensively to preserve order.
        ordered = sorted(response.data, key=lambda d: d.index)
        return [item.embedding for item in ordered]

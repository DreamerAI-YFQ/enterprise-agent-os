"""OpenAI-compatible embedding adapter (API-based, no local model).

Works with OpenAI, Zhipu/GLM (OpenAI-compatible endpoint), or any local
embeddings server exposing the OpenAI embeddings API. Dimensions default to
1024 to match the schema's vector(1024) columns.

The openai SDK is sync/async split; we use AsyncOpenAI. The Embedder protocol
is async throughout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

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
        self._supports_dimensions: bool | None = None

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
        try:
            response = await self._create(text)
        except openai.OpenAIError as exc:
            raise LLMError(f"embedding call failed: {exc}") from exc
        return cast("list[float]", response.data[0].embedding)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call. Order preserved."""
        if not texts:
            return []
        try:
            response = await self._create(texts)
        except openai.OpenAIError as exc:
            raise LLMError(f"embedding batch call failed: {exc}") from exc
        # API returns data sorted by index; sort defensively to preserve order.
        ordered = sorted(response.data, key=lambda d: d.index)
        return [item.embedding for item in ordered]

    async def _create(self, input_value: str | list[str]) -> Any:
        """Call the provider and remember whether it accepts ``dimensions``."""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "input": input_value,
            "model": self._config.model,
        }
        if self._supports_dimensions is not False:
            kwargs["dimensions"] = self._config.dimensions
        try:
            response = await client.embeddings.create(**kwargs)
        except openai.BadRequestError:
            if "dimensions" not in kwargs:
                raise
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "Embedding provider rejected dimensions=%s for model=%s; "
                "retrying once without dimensions and caching that capability.",
                self._config.dimensions,
                self._config.model,
            )
            self._supports_dimensions = False
            self._client = None
            client = self._get_client()
            return await client.embeddings.create(
                input=input_value,
                model=self._config.model,
            )
        else:
            # A successful request only proves ``dimensions`` support when
            # that argument was actually sent.  Once a provider has rejected
            # it, later fallback successes must not re-enable the argument and
            # cause every other embedding request to repeat the same 400.
            if "dimensions" in kwargs:
                self._supports_dimensions = True
            return response

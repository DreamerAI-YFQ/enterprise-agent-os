"""LLM client protocol and message types.

Adapters (OpenAI, Anthropic, GLM) implement LLMClient. LLMRouter selects the
adapter per call based on model hint and tenant routing config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True)
class Attachment:
    """A multimodal attachment on a Message (image or file).

    For images, ``data_url`` carries the base64-inline data URL
    (``data:image/png;base64,...``) consumed directly by OpenAI-compatible
    vision APIs. For text-extractable files (PDF/txt/md/csv), ``text_content``
    holds the extracted text that the LLM adapter appends to the text part
    of the multimodal content.
    """

    type: str  # image | file
    mime_type: str
    name: str
    data_url: str | None = None  # for image: data URL
    text_content: str | None = None  # for file: extracted text


@dataclass(frozen=True)
class Message:
    """A single chat message."""

    role: str  # system/user/assistant/tool
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # for role=tool, the tool name
    attachments: list[Attachment] | None = None  # multimodal attachments


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM chat call."""

    content: str
    tool_calls: list[ToolCall] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    finish_reason: str = "stop"  # stop/length/tool_calls/content_filter

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ModelSelection:
    """Which provider+model the router picked for a call."""

    provider: str  # openai/anthropic/glm
    model: str
    estimated_cost_usd_per_1k: float = 0.0


class LLMClient(Protocol):
    """LLM provider adapter protocol."""

    provider: str

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        """Single-shot chat completion."""
        ...

    def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        """Stream completion tokens as an async iterator of strings.

        Declared as a regular (non-async) method returning an async iterator,
        so implementations can be async generators (``async def`` + ``yield``).
        """
        ...

    async def vision(self, image: bytes, prompt: str, *, model: str) -> str:
        """Multimodal image understanding."""
        ...

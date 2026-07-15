"""Multi-model router.

Selects provider+model per call based on: explicit hint > tenant routing
config > default. Enables cost optimization (cheap models for simple tasks)
and vendor independence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.infra.llm.base import LLMClient, LLMResponse, Message


@dataclass(frozen=True)
class TenantModelRouting:
    """Per-tenant model routing preferences.

    Maps task type -> model id. Loaded from tenant settings at request time.
    """

    routing: dict[str, str]  # {"default": "gpt-4o-mini", "coding": "claude-sonnet"}
    fallback: str = "gpt-4o-mini"


class LLMRouter(Protocol):
    """Multi-model router facade over multiple LLMClient adapters."""

    async def chat(
        self,
        messages: list[Message],
        *,
        model_hint: str | None = None,
        task_type: str | None = None,
        tenant_routing: TenantModelRouting | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Route a chat call to the appropriate provider+model."""
        ...

    def stream(
        self,
        messages: list[Message],
        *,
        model_hint: str | None = None,
        task_type: str | None = None,
        tenant_routing: TenantModelRouting | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream tokens from the routed model."""
        ...

    async def vision(self, image: bytes, prompt: str) -> str:
        """Multimodal image understanding via a vision-capable model."""
        ...

    def register_adapter(self, adapter: LLMClient) -> None:
        """Register a provider adapter (openai/anthropic/glm)."""
        ...

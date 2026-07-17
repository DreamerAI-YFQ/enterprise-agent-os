"""LLMRouter implementation: routes calls to provider adapters.

Routing priority (highest wins):
  1. model_hint (format "provider:model"; bare model uses default provider)
  2. tenant_routing.routing[task_type] (value parsed as "provider:model")
  3. tenant_routing.fallback (parsed as "provider:model")
  4. (default_provider, default_model)

If a resolved provider has no registered adapter, falls through to the next
level. vision() picks the first available vision-capable provider in priority
order: anthropic > openai > glm.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from eaos.core.errors import LLMError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from eaos.infra.llm.base import LLMClient, LLMResponse, Message
    from eaos.infra.llm.router import TenantModelRouting


@dataclass(frozen=True)
class LLMUsageRecord:
    """One routed LLM call captured for run-level usage evidence."""

    provider: str
    model: str
    task_type: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    success: bool
    error_type: str | None = None


class LLMRouterImpl:
    """Concrete LLMRouter: dispatches to registered provider adapters."""

    _VISION_PRIORITY: tuple[str, ...] = ("anthropic", "openai", "glm")
    _VISION_MODELS: dict[str, str] = {
        "anthropic": "claude-3-5-sonnet-20241022",
        "openai": "gpt-4o",
        "glm": "glm-4v",
    }

    def __init__(
        self,
        default_provider: str = "openai",
        default_model: str = "gpt-4o-mini",
        vision_model_override: str | None = None,
    ) -> None:
        self._adapters: dict[str, LLMClient] = {}
        self._default_provider = default_provider
        self._default_model = default_model
        self._vision_model_override = vision_model_override
        self._usage_capture: contextvars.ContextVar[list[LLMUsageRecord] | None] = (
            contextvars.ContextVar("eaos_llm_usage_capture", default=None)
        )

    def register_adapter(self, adapter: LLMClient) -> None:
        self._adapters[adapter.provider] = adapter

    @asynccontextmanager
    async def capture_usage(self) -> AsyncGenerator[list[LLMUsageRecord], None]:
        """Capture all chat calls made in the current async execution context.

        A mutable list is intentionally shared with child tasks created inside
        the context so fan-out orchestration contributes to the same run total.
        Nested captures remain isolated and context state is always restored.
        """

        records: list[LLMUsageRecord] = []
        token = self._usage_capture.set(records)
        try:
            yield records
        finally:
            self._usage_capture.reset(token)

    def _record_usage(self, record: LLMUsageRecord) -> None:
        capture = self._usage_capture.get()
        if capture is not None:
            capture.append(record)

    @staticmethod
    def _parse_hint(hint: str) -> tuple[str, str]:
        """Parse 'provider:model' or bare 'model' into (provider, model)."""
        if ":" in hint:
            provider, model = hint.split(":", 1)
            return provider, model
        # Bare model name: assume default provider (caller fills in).
        return "", hint

    def _resolve(
        self,
        model_hint: str | None,
        task_type: str | None,
        tenant_routing: TenantModelRouting | None,
    ) -> tuple[LLMClient, str]:
        candidates: list[tuple[str, str]] = []
        if model_hint is not None:
            candidates.append(self._parse_hint(model_hint))
        if tenant_routing is not None and task_type is not None:
            routed = tenant_routing.routing.get(task_type)
            if routed is not None:
                candidates.append(self._parse_hint(routed))
        if tenant_routing is not None:
            candidates.append(self._parse_hint(tenant_routing.fallback))
        candidates.append((self._default_provider, self._default_model))

        for provider, model in candidates:
            adapter = self._adapters.get(provider) if provider else None
            if adapter is None:
                # Bare model with no provider -> use default provider's adapter.
                adapter = self._adapters.get(self._default_provider)
            if adapter is not None:
                return adapter, model

        if not self._adapters:
            raise LLMError("no LLM adapters registered")
        adapter = next(iter(self._adapters.values()))
        return adapter, self._default_model

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
        adapter, model = self._resolve(model_hint, task_type, tenant_routing)
        started = time.perf_counter()
        try:
            response = await adapter.chat(
                messages, model=model, temperature=temperature, tools=tools
            )
        except Exception as exc:
            self._record_usage(
                LLMUsageRecord(
                    provider=adapter.provider,
                    model=model,
                    task_type=task_type,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    success=False,
                    error_type=type(exc).__name__,
                )
            )
            raise
        self._record_usage(
            LLMUsageRecord(
                provider=adapter.provider,
                model=response.model or model,
                task_type=task_type,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000),
                success=True,
            )
        )
        return response

    async def stream(
        self,
        messages: list[Message],
        *,
        model_hint: str | None = None,
        task_type: str | None = None,
        tenant_routing: TenantModelRouting | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        adapter, model = self._resolve(model_hint, task_type, tenant_routing)
        async for token in adapter.stream(messages, model=model, temperature=temperature):
            yield token

    async def vision(self, image: bytes, prompt: str) -> str:
        for provider in self._VISION_PRIORITY:
            adapter = self._adapters.get(provider)
            if adapter is not None:
                model = self._vision_model_override or self._VISION_MODELS[provider]
                return await adapter.vision(image, prompt, model=model)
        raise LLMError("no vision-capable adapter registered")

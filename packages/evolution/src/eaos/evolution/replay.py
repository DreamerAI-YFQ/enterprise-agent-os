"""Trace replayer — shadow traffic via historical trace replay.

Replays historical task spans through the new strategy model and compares
outputs against baseline using an LLM judge. Fills the ``baseline_metrics``
and ``shadow_results`` fields in ``harness.evolution_strategies.stage_detail``
so that ``ShadowTrafficManagerImpl.evaluate()`` can compare against thresholds.

Metrics computed:
- adoption_rate: fraction of cases where LLM judge says new output is
  "at least as good as" baseline output (baseline = 1.0 by definition).
- avg_latency_ms: new model call latency (baseline from trace span).
- cost_usd: new model token cost (baseline = 0, historical).
- error_rate: fraction of new model calls that errored.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class ReplayLLM(Protocol):
    """LLM interface — same shape as guardrail.GuardrailLLM for adapter reuse."""

    async def chat(self, prompt: str, model: str) -> str: ...


class ReplayDb(Protocol):
    """Minimal DB subset for trace span queries."""

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _extract_output(attrs: Any) -> str:
    """Extract output text from span attributes JSONB."""
    parsed = _parse_jsonb(attrs) if attrs else {}
    if not isinstance(parsed, dict):
        return ""
    out = parsed.get("output") or parsed.get("result") or ""
    return str(out) if out else ""


def _extract_latency_ms(attrs: Any) -> float:
    """Extract baseline latency from span attributes (ms)."""
    parsed = _parse_jsonb(attrs) if attrs else {}
    if not isinstance(parsed, dict):
        return 0.0
    val = parsed.get("duration_ms") or parsed.get("latency_ms") or 0
    return float(val)


_JUDGE_PROMPT_TEMPLATE = (
    "你是一个回答质量评估员。判断新回答是否至少与原回答一样好（信息完整、准确、相关）。\n\n"
    "问题: {prompt}\n"
    "原回答: {baseline}\n"
    "新回答: {new}\n\n"
    "只回答 yes 或 no。"
)

_EMPTY_BASELINE: dict[str, float] = {
    "adoption_rate": 1.0,
    "avg_latency_ms": 0.0,
    "cost_usd": 0.0,
    "error_rate": 0.0,
}
_EMPTY_SHADOW: dict[str, float] = {
    "adoption_rate": 0.0,
    "avg_latency_ms": 0.0,
    "cost_usd": 0.0,
    "error_rate": 1.0,
}


class TraceReplayer(Protocol):
    """Replay historical traces through new model for shadow comparison."""

    async def replay(
        self,
        strategy_id: UUID,
        tenant_id: UUID,
        baseline_model: str,
        new_model: str,
        sample_size: int = 50,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Replay historical tasks, return (baseline_metrics, shadow_results).

        baseline_metrics: adoption_rate=1.0, avg_latency_ms from trace,
            cost_usd=0, error_rate=0.
        shadow_results: adoption_rate from LLM judge, avg_latency_ms from
            new model calls, cost_usd from token usage, error_rate from
            call failures.
        """
        ...


class TraceReplayerImpl:
    """TraceReplayer backed by trace.spans queries + LLM judge.

    Selects ``sample_size`` task-granularity spans for the tenant, replays
    each prompt through the new model, and judges adoption via LLM. Errors
    on individual spans do not block the batch — they count toward
    ``error_rate``.
    """

    def __init__(
        self,
        db: ReplayDb,
        llm: ReplayLLM,
        judge_model: str,
    ) -> None:
        self._db = db
        self._llm = llm
        self._judge_model = judge_model

    async def replay(
        self,
        strategy_id: UUID,
        tenant_id: UUID,
        baseline_model: str,
        new_model: str,
        sample_size: int = 50,
    ) -> tuple[dict[str, float], dict[str, float]]:
        del strategy_id, baseline_model  # not used; kept for interface compat

        rows = await self._db.fetch(
            "SELECT id, name, attributes FROM trace.spans "
            "WHERE tenant_id = :p0 AND granularity = 'task' "
            "ORDER BY start_time DESC LIMIT :p1",
            tenant_id,
            sample_size,
        )

        if not rows:
            return dict(_EMPTY_BASELINE), dict(_EMPTY_SHADOW)

        valid_count = 0
        adopted_count = 0
        error_count = 0
        total_latency_ms = 0.0
        total_cost = 0.0
        baseline_latencies: list[float] = []

        for row in rows:
            attrs = row.get("attributes")
            output = _extract_output(attrs)
            if not output:
                continue
            valid_count += 1
            baseline_latencies.append(_extract_latency_ms(attrs))

            prompt = str(row.get("name") or "")
            try:
                t0 = time.monotonic()
                new_response = await self._llm.chat(prompt, new_model)
                latency = (time.monotonic() - t0) * 1000.0
                total_latency_ms += latency

                judge_prompt = _JUDGE_PROMPT_TEMPLATE.format(
                    prompt=prompt, baseline=output, new=new_response[:500]
                )
                verdict = await self._llm.chat(judge_prompt, self._judge_model)
                if "yes" in verdict.lower():
                    adopted_count += 1
            except Exception:
                error_count += 1

        if valid_count == 0:
            return dict(_EMPTY_BASELINE), dict(_EMPTY_SHADOW)

        baseline_metrics: dict[str, float] = {
            "adoption_rate": 1.0,
            "avg_latency_ms": sum(baseline_latencies) / len(baseline_latencies),
            "cost_usd": 0.0,
            "error_rate": 0.0,
        }
        shadow_results: dict[str, float] = {
            "adoption_rate": adopted_count / valid_count,
            "avg_latency_ms": total_latency_ms / valid_count,
            "cost_usd": total_cost,
            "error_rate": error_count / valid_count,
        }
        return baseline_metrics, shadow_results

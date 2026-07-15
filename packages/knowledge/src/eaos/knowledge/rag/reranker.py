"""LLM-based reranker — re-orders retrieved chunks by query relevance.

Asks the LLM to rank candidate chunks by relevance to the query. The LLM
returns a JSON object ``{"ranked": [index, ...]}`` referencing the candidate
list positions. On parse failure or invalid indices, falls back to the
original order.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from eaos.infra.llm.base import LLMResponse, Message

if TYPE_CHECKING:
    from eaos.infra.llm.router import LLMRouter
    from eaos.knowledge.rag.pipeline import Chunk

_SYSTEM_PROMPT = (
    "你是相关性排序专家。根据用户查询对候选文档片段按相关性从高到低排序。"
    "只返回 JSON: {\"ranked\": [索引, ...]}，索引是候选文档的序号（从0开始）。"
)


class LLMReranker:
    """Reranker backed by an LLMRouter call."""

    def __init__(self, llm: LLMRouter, max_candidates: int = 20) -> None:
        self._llm = llm
        self._max_candidates = max_candidates

    async def rerank(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 5,
    ) -> list[Chunk]:
        if not chunks:
            return []
        if len(chunks) == 1:
            return chunks[:top_k]

        candidates = chunks[: self._max_candidates]
        user_content = self._build_user_prompt(query, candidates)
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]
        response = await self._llm.chat(messages, temperature=0.0, task_type="rerank")
        ranked_indices = self._parse_ranked(response)
        if ranked_indices is None:
            return candidates[:top_k]

        result: list[Chunk] = []
        seen: set[int] = set()
        for idx in ranked_indices:
            if idx in seen or idx < 0 or idx >= len(candidates):
                continue
            seen.add(idx)
            result.append(candidates[idx])
        for i, c in enumerate(candidates):
            if i not in seen:
                result.append(c)
        return result[:top_k]

    @staticmethod
    def _build_user_prompt(query: str, chunks: list[Chunk]) -> str:
        lines = [f"查询: {query}", "候选文档:"]
        for i, chunk in enumerate(chunks):
            preview = chunk.content[:200].replace("\n", " ")
            lines.append(f"[{i}] {preview}")
        return "\n".join(lines)

    @staticmethod
    def _parse_ranked(response: LLMResponse) -> list[int] | None:
        content = response.content.strip()
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data: Any = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
        ranked = data.get("ranked") if isinstance(data, dict) else None
        if not isinstance(ranked, list):
            return None
        return [i for i in ranked if isinstance(i, int)]

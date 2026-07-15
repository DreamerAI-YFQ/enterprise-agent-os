"""Query rewriter — uses ontology to expand/clarify user queries.

Disambiguates entities ("张三" -> employee.name=张三 OR contact.name=张三)
and expands relations ("张三的项目" -> project.lead=张三 OR project.member=张三).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.llm.base import LLMResponse
    from eaos.infra.llm.router import LLMRouter
    from eaos.knowledge.ontology.model import OntologyNode
    from eaos.knowledge.ontology.repository import OntologyRepository


@dataclass(frozen=True)
class RewrittenQuery:
    """Result of ontology-driven query rewriting."""

    original: str
    rewritten: str
    entities: list[dict[str, Any]]  # extracted + disambiguated entities
    ontology_refs: list[UUID]  # nodes used for disambiguation
    expansion_notes: str | None = None  # why the rewrite happened


class QueryRewriter(Protocol):
    """Ontology-driven query rewriter."""

    async def rewrite(self, query: str, tenant_id: UUID) -> RewrittenQuery:
        """Rewrite a natural language query using ontology."""
        ...


class OntologyQueryRewriter:
    """QueryRewriter backed by OntologyRepository + LLMRouter.

    Flow: search ontology nodes → build LLM prompt → parse JSON → assemble.
    """

    SYSTEM_PROMPT = (
        "你是企业本体专家。根据本体节点重写用户查询。\n"
        "规则:\n"
        "1. 消歧：如果查询中的实体在本体中有多个匹配，选择最相关的\n"
        "2. 关系扩展：根据 Relation 节点扩展查询范围\n"
        '3. 返回 JSON: {"rewritten": "重写后的查询", '
        '"entities": [{"name": "...", "type": "...", "node_id": "..."}], '
        '"notes": "改写原因"}'
    )

    def __init__(
        self,
        ontology_repo: OntologyRepository,
        llm: LLMRouter,
        top_k: int = 10,
    ) -> None:
        self._repo = ontology_repo
        self._llm = llm
        self._top_k = top_k

    def _build_user_prompt(
        self, query: str, nodes: list[OntologyNode]
    ) -> str:
        """Build the user message with ontology context."""
        nodes_json = json.dumps(
            [
                {
                    "id": str(n.id),
                    "type": n.node_type.value,
                    "name": n.name,
                    "properties": n.properties,
                }
                for n in nodes
            ],
            ensure_ascii=False,
            indent=2,
        )
        return f"用户查询: {query}\n相关本体节点:\n{nodes_json}"

    @staticmethod
    def _parse_llm_response(
        response: LLMResponse, original: str, node_ids: list[UUID]
    ) -> RewrittenQuery:
        """Parse LLM JSON response into RewrittenQuery. Falls back to original on error."""
        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            return RewrittenQuery(
                original=original,
                rewritten=original,
                entities=[],
                ontology_refs=node_ids,
                expansion_notes="LLM response was not valid JSON; using original query",
            )

        entities = data.get("entities", [])
        if not isinstance(entities, list):
            entities = []

        return RewrittenQuery(
            original=original,
            rewritten=data.get("rewritten", original),
            entities=entities,
            ontology_refs=node_ids,
            expansion_notes=data.get("notes"),
        )

    async def rewrite(self, query: str, tenant_id: UUID) -> RewrittenQuery:
        """Rewrite a natural language query using ontology context."""
        from eaos.infra.llm.base import Message

        nodes = await self._repo.search_nodes(tenant_id, query, top_k=self._top_k)
        node_ids = [n.id for n in nodes]

        messages = [
            Message(role="system", content=self.SYSTEM_PROMPT),
            Message(role="user", content=self._build_user_prompt(query, nodes)),
        ]
        response = await self._llm.chat(
            messages, task_type="query_rewrite", temperature=0.3
        )
        return self._parse_llm_response(response, query, node_ids)

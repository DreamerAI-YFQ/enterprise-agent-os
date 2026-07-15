"""Unit tests for OntologyQueryRewriter.

Mocks OntologyRepository + LLMRouter. Verifies prompt construction, LLM call,
JSON parsing, and fallback behavior.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from eaos.infra.llm.base import LLMResponse, Message
from eaos.infra.llm.router import LLMRouter
from eaos.knowledge.ontology.model import NodeType, OntologyNode
from eaos.knowledge.ontology.query_rewrite import OntologyQueryRewriter
from eaos.knowledge.ontology.repository import OntologyRepository


def _make_rewriter(
    llm_response_content: str = '{"rewritten": "q", "entities": [], "notes": "n"}',
) -> tuple[OntologyQueryRewriter, Any, Any]:
    """Build a rewriter with mocked repo + LLM."""
    repo: Any = MagicMock(spec=OntologyRepository)
    repo.search_nodes = AsyncMock()
    llm: Any = MagicMock(spec=LLMRouter)
    llm.chat = AsyncMock(return_value=LLMResponse(content=llm_response_content))
    rewriter = OntologyQueryRewriter(repo, llm)
    return rewriter, repo, llm


def _make_node(node_type: NodeType = NodeType.OBJECT, name: str = "Customer") -> OntologyNode:
    return OntologyNode(
        id=uuid4(), ontology_id=uuid4(), tenant_id=uuid4(),
        node_type=node_type, name=name, properties={"table": "erp.customers"},
    )


class TestRewrite:
    async def test_calls_search_then_llm(self) -> None:
        rewriter, repo, llm = _make_rewriter()
        repo.search_nodes.return_value = [_make_node()]
        await rewriter.rewrite("查询客户", uuid4())
        repo.search_nodes.assert_awaited_once()
        llm.chat.assert_awaited_once()

    async def test_returns_parsed_result(self) -> None:
        llm_content = json.dumps({
            "rewritten": "查询所有客户的名称和信用额度",
            "entities": [{"name": "Customer", "type": "object", "node_id": "abc"}],
            "notes": "expanded to include credit_limit",
        })
        rewriter, repo, _ = _make_rewriter(llm_content)
        node = _make_node()
        repo.search_nodes.return_value = [node]
        result = await rewriter.rewrite("客户", uuid4())
        assert result.original == "客户"
        assert result.rewritten == "查询所有客户的名称和信用额度"
        assert len(result.entities) == 1
        assert result.entities[0]["name"] == "Customer"
        assert result.expansion_notes == "expanded to include credit_limit"
        assert node.id in result.ontology_refs

    async def test_fallback_on_invalid_json(self) -> None:
        rewriter, repo, _ = _make_rewriter("not json at all")
        repo.search_nodes.return_value = [_make_node()]
        result = await rewriter.rewrite("query", uuid4())
        assert result.rewritten == "query"
        assert result.entities == []
        assert "not valid JSON" in (result.expansion_notes or "")

    async def test_fallback_when_rewritten_missing(self) -> None:
        rewriter, repo, _ = _make_rewriter('{"entities": []}')
        repo.search_nodes.return_value = []
        result = await rewriter.rewrite("original query", uuid4())
        assert result.rewritten == "original query"

    async def test_entities_not_list_defaults_to_empty(self) -> None:
        rewriter, repo, _ = _make_rewriter('{"rewritten": "q", "entities": "not a list"}')
        repo.search_nodes.return_value = []
        result = await rewriter.rewrite("q", uuid4())
        assert result.entities == []

    async def test_llm_receives_system_and_user_messages(self) -> None:
        rewriter, repo, llm = _make_rewriter()
        repo.search_nodes.return_value = [_make_node()]
        await rewriter.rewrite("test", uuid4())
        call = llm.chat.call_args
        messages: list[Message] = call.args[0]
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert "test" in messages[1].content
        assert "Customer" in messages[1].content

    async def test_llm_uses_low_temperature(self) -> None:
        rewriter, repo, llm = _make_rewriter()
        repo.search_nodes.return_value = []
        await rewriter.rewrite("test", uuid4())
        assert llm.chat.call_args.kwargs["temperature"] == 0.3

    async def test_empty_nodes_still_calls_llm(self) -> None:
        rewriter, repo, llm = _make_rewriter()
        repo.search_nodes.return_value = []
        await rewriter.rewrite("test", uuid4())
        llm.chat.assert_awaited_once()

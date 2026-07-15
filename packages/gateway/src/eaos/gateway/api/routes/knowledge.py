"""Knowledge search API — semantic retrieval across RAG + memory + ontology.

``POST /knowledge/search`` delegates to ``KnowledgeEngine.search`` which merges
RAG chunk retrieval, memory recall, and ontology lookup into ranked results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_knowledge_engine, get_principal
from fastapi import APIRouter, Depends  # noqa: TC002
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from eaos.knowledge.engine import KnowledgeEngine

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class SearchRequest(BaseModel):
    """Request body for POST /knowledge/search."""

    query: str
    top_k: int = Field(5, ge=1, le=50, description="Max results to return")


@router.post("/search", status_code=200)
async def search(
    body: SearchRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    engine: KnowledgeEngine = Depends(get_knowledge_engine),  # noqa: B008
) -> list[dict[str, Any]]:
    """Search the knowledge base (RAG + memory + ontology)."""
    results = await engine.search(
        body.query, principal.tenant_id, top_k=body.top_k, user_id=principal.user_id
    )
    return [
        {
            "content": r.content,
            "score": r.score,
            "source": r.source,
            "metadata": r.metadata,
        }
        for r in results
    ]

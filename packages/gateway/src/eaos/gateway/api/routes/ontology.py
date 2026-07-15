"""Ontology term management API — CRUD for ontology nodes + gap detection.

Admin routes (``/admin/ontology``):
- ``GET /terms`` — list nodes (optionally filtered by node_type)
- ``POST /terms`` — create a node
- ``PUT /terms/{id}`` — update a node's name/properties/parent
- ``DELETE /terms/{id}`` — delete a node
- ``GET /gaps`` — list nodes with empty properties (knowledge gaps to enrich)

Listing and creation use ``OntologyRepository``; update and deletion query the
DB directly because the repository exposes no update/delete methods. If no
``ontology_id`` is provided, the active ontology for the tenant is used.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4  # noqa: TC003

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_ontology_repo
from eaos.gateway.api.routes.admin import require_admin
from eaos.knowledge.ontology.model import NodeType, OntologyNode  # noqa: TC002
from eaos.knowledge.ontology.repository import OntologyRepository  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/admin/ontology", tags=["ontology"])


# -- Request models -----------------------------------------------------------


class TermCreate(BaseModel):
    """Request body for POST /terms."""

    ontology_id: UUID | None = None
    node_type: str  # object/attribute/relation/rule/code
    name: str
    parent_id: UUID | None = None
    properties: dict[str, Any] = {}


class TermUpdate(BaseModel):
    """Request body for PUT /terms/{id}."""

    name: str | None = None
    parent_id: UUID | None = None
    properties: dict[str, Any] | None = None


# -- Helpers ------------------------------------------------------------------


def _parse_node_type(value: str) -> NodeType:
    normalized = value.lower()
    for member in NodeType:
        if member.value == normalized or member.name.lower() == normalized:
            return member
    raise HTTPException(
        status_code=422, detail=f"invalid node_type: {value}"
    )


def _node_to_dict(node: OntologyNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "ontology_id": str(node.ontology_id),
        "tenant_id": str(node.tenant_id),
        "node_type": node.node_type.value,
        "name": node.name,
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "properties": dict(node.properties),
    }


async def _resolve_ontology_id(
    repo: OntologyRepository, principal: Principal, ontology_id: UUID | None
) -> UUID:
    if ontology_id is not None:
        return ontology_id
    active = await repo.get_active(principal.tenant_id)
    if active is None:
        raise HTTPException(
            status_code=404,
            detail="no active ontology for this tenant — create one first",
        )
    return active.id


# -- Routes -------------------------------------------------------------------


@router.get("/terms", status_code=200)
async def list_terms(
    principal: Principal = Depends(require_admin),  # noqa: B008
    repo: OntologyRepository = Depends(get_ontology_repo),  # noqa: B008
    ontology_id: UUID | None = Query(None),  # noqa: B008
    node_type: str | None = Query(None),  # noqa: B008
) -> list[dict[str, Any]]:
    """List ontology nodes (admin only)."""
    ont_id = await _resolve_ontology_id(repo, principal, ontology_id)
    nt = _parse_node_type(node_type) if node_type else None
    nodes = await repo.get_nodes(principal.tenant_id, ont_id, nt)
    return [_node_to_dict(n) for n in nodes]


@router.post("/terms", status_code=201)
async def create_term(
    body: TermCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    repo: OntologyRepository = Depends(get_ontology_repo),  # noqa: B008
) -> dict[str, Any]:
    """Create an ontology node (admin only)."""
    ont_id = await _resolve_ontology_id(repo, principal, body.ontology_id)
    node = OntologyNode(
        id=uuid4(),
        ontology_id=ont_id,
        tenant_id=principal.tenant_id,
        node_type=_parse_node_type(body.node_type),
        name=body.name,
        parent_id=body.parent_id,
        properties=body.properties,
    )
    created = await repo.create_node(principal.tenant_id, node)
    return _node_to_dict(created)


@router.put("/terms/{term_id}", status_code=200)
async def update_term(
    term_id: UUID,
    body: TermUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update an ontology node's name/parent/properties (admin only)."""
    row = await db.fetch_one(
        "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
        "FROM knowledge.ontology_nodes WHERE id = :p0 AND tenant_id = :p1",
        term_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="term not found")

    if body.name is not None:
        await db.execute(
            "UPDATE knowledge.ontology_nodes SET name = :p0 "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.name,
            term_id,
            principal.tenant_id,
        )
    if body.parent_id is not None:
        await db.execute(
            "UPDATE knowledge.ontology_nodes SET parent_id = :p0 "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.parent_id,
            term_id,
            principal.tenant_id,
        )
    if body.properties is not None:
        await db.execute(
            "UPDATE knowledge.ontology_nodes SET properties = CAST(:p0 AS jsonb) "
            "WHERE id = :p1 AND tenant_id = :p2",
            json.dumps(body.properties, ensure_ascii=False),
            term_id,
            principal.tenant_id,
        )

    updated = await db.fetch_one(
        "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
        "FROM knowledge.ontology_nodes WHERE id = :p0 AND tenant_id = :p1",
        term_id,
        principal.tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="term not found after update")
    props = updated["properties"]
    return {
        "id": str(updated["id"]),
        "ontology_id": str(updated["ontology_id"]),
        "tenant_id": str(updated["tenant_id"]),
        "node_type": updated["node_type"],
        "name": updated["name"],
        "parent_id": str(updated["parent_id"]) if updated["parent_id"] else None,
        "properties": props if isinstance(props, dict)
        else json.loads(props or "{}"),
    }


@router.delete("/terms/{term_id}", status_code=204)
async def delete_term(
    term_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete an ontology node (admin only)."""
    row = await db.fetch_one(
        "SELECT id FROM knowledge.ontology_nodes WHERE id = :p0 AND tenant_id = :p1",
        term_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="term not found")
    await db.execute(
        "DELETE FROM knowledge.ontology_nodes WHERE id = :p0 AND tenant_id = :p1",
        term_id,
        principal.tenant_id,
    )


@router.get("/gaps", status_code=200)
async def list_gaps(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    ontology_id: UUID | None = Query(None),  # noqa: B008
) -> list[dict[str, Any]]:
    """List ontology nodes with empty properties — terms needing enrichment.

    These represent knowledge gaps: concepts defined in the ontology but not
    yet connected to schema mappings or business rules.
    """
    if ontology_id is not None:
        rows = await db.fetch(
            "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :p0 AND ontology_id = :p1 "
            "AND properties = '{}'::jsonb "
            "ORDER BY node_type, name",
            principal.tenant_id,
            ontology_id,
        )
    else:
        rows = await db.fetch(
            "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :p0 AND properties = '{}'::jsonb "
            "ORDER BY node_type, name",
            principal.tenant_id,
        )
    return [
        {
            "id": str(r["id"]),
            "ontology_id": str(r["ontology_id"]),
            "node_type": r["node_type"],
            "name": r["name"],
            "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
        }
        for r in rows
    ]


@router.get("/graph", status_code=200)
async def get_ontology_graph(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    ontology_id: UUID | None = Query(None),  # noqa: B008
) -> dict[str, Any]:
    """Return ontology as a graph {nodes, edges} for visualization.

    Nodes are ontology nodes with id/name/node_type/properties; edges are
    derived from parent_id relationships (parent → child). Useful for the
    admin UI to render an interactive ontology graph.
    """
    if ontology_id is not None:
        rows = await db.fetch(
            "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :p0 AND ontology_id = :p1 "
            "ORDER BY node_type, name",
            principal.tenant_id,
            ontology_id,
        )
    else:
        rows = await db.fetch(
            "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :p0 ORDER BY node_type, name",
            principal.tenant_id,
        )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for r in rows:
        node_id = str(r["id"])
        props = r.get("properties")
        if isinstance(props, str):
            props = json.loads(props or "{}")
        nodes.append(
            {
                "id": node_id,
                "ontology_id": str(r["ontology_id"]),
                "node_type": r["node_type"],
                "name": r["name"],
                "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                "properties": props or {},
            }
        )
        if r["parent_id"] is not None:
            edges.append(
                {
                    "id": f"e-{r['parent_id']}-{node_id}",
                    "source": str(r["parent_id"]),
                    "target": node_id,
                    "label": "父子",
                }
            )

    return {"nodes": nodes, "edges": edges}

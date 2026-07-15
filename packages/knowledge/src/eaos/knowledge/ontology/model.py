"""Enterprise ontology model — explicit knowledge modeling (core differentiator).

Five node types: object, attribute, relation, rule, code. This explicit
modeling (vs Claude Tag's implicit memory) is the platform's core barrier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


class NodeType(StrEnum):
    """Ontology node types."""

    OBJECT = "object"  # business object: customer, order, product
    ATTRIBUTE = "attribute"  # property: customer.name, order.amount
    RELATION = "relation"  # link: customer—places—order
    RULE = "rule"  # constraint: order > 100k requires director approval
    CODE = "code"  # coding system: customer code format, SKU rules


@dataclass(frozen=True)
class Ontology:
    """An ontology definition for a tenant."""

    id: UUID
    tenant_id: UUID
    name: str
    version: str = "1.0.0"
    status: str = "active"  # active/draft/deprecated


@dataclass(frozen=True)
class OntologyNode:
    """A single node in the ontology graph."""

    id: UUID
    ontology_id: UUID
    tenant_id: UUID
    node_type: NodeType
    name: str
    parent_id: UUID | None = None  # hierarchy
    properties: dict[str, Any] = field(default_factory=dict)

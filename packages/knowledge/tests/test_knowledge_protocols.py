"""Verify knowledge engine Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.knowledge.engine import KnowledgeEngine, SearchResult
from eaos.knowledge.memory.consolidator import MemoryConsolidator
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryStore, MemoryType
from eaos.knowledge.ontology.model import NodeType, Ontology, OntologyNode
from eaos.knowledge.ontology.query_rewrite import QueryRewriter, RewrittenQuery
from eaos.knowledge.ontology.repository import OntologyRepository
from eaos.knowledge.rag.pipeline import Chunk, Document, RAGPipeline


class TestKnowledgeEngine:
    def test_searchresult_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SearchResult)}
        assert {"content", "score", "source", "metadata"} <= fields

    def test_document_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Document)}
        assert {"source_type", "source_uri", "title", "content", "metadata"} <= fields

    def test_protocol_methods(self) -> None:
        for method in ("search", "ingest_document", "recall_memory", "rewrite_query"):
            assert hasattr(KnowledgeEngine, method)


class TestOntology:
    def test_nodetype_values(self) -> None:
        assert NodeType.OBJECT.value == "object"
        assert NodeType.ATTRIBUTE.value == "attribute"
        assert NodeType.RELATION.value == "relation"
        assert NodeType.RULE.value == "rule"
        assert NodeType.CODE.value == "code"

    def test_ontologynode_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(OntologyNode)}
        assert {"id", "ontology_id", "node_type", "name", "parent_id", "properties"} <= fields

    def test_ontology_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Ontology)}
        assert {"id", "tenant_id", "name", "version"} <= fields


class TestOntologyRepository:
    def test_protocol_methods(self) -> None:
        for method in ("get", "get_nodes", "create_node", "get_schema_mapping"):
            assert hasattr(OntologyRepository, method)


class TestQueryRewriter:
    def test_rewrittenquery_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(RewrittenQuery)}
        assert {"original", "rewritten", "entities", "ontology_refs"} <= fields

    def test_protocol_methods(self) -> None:
        assert hasattr(QueryRewriter, "rewrite")


class TestRAGPipeline:
    def test_chunk_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Chunk)}
        assert {
            "id",
            "document_id",
            "tenant_id",
            "chunk_index",
            "content",
            "token_count",
            "embedding",
            "metadata",
        } <= fields

    def test_protocol_methods(self) -> None:
        for method in ("ingest", "retrieve", "delete_document"):
            assert hasattr(RAGPipeline, method)


class TestMemoryStore:
    def test_memoryscope_values(self) -> None:
        assert MemoryScope.PERSONAL.value == "personal"
        assert MemoryScope.DEPARTMENT.value == "department"
        assert MemoryScope.ENTERPRISE.value == "enterprise"

    def test_memorytype_values(self) -> None:
        assert MemoryType.PREFERENCE.value == "preference"
        assert MemoryType.FACT.value == "fact"
        assert MemoryType.PROCEDURE.value == "procedure"
        assert MemoryType.FEEDBACK.value == "feedback"

    def test_memory_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Memory)}
        assert {
            "id",
            "tenant_id",
            "scope",
            "owner_id",
            "memory_type",
            "content",
            "confidence",
            "source",
            "created_at",
            "last_accessed",
            "access_count",
        } <= fields

    def test_protocol_methods(self) -> None:
        for method in ("store", "recall", "promote_scope", "delete"):
            assert hasattr(MemoryStore, method)


class TestMemoryConsolidator:
    def test_protocol_methods(self) -> None:
        assert hasattr(MemoryConsolidator, "consolidate_session")

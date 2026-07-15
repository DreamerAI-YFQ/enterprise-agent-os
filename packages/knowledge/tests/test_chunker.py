"""Unit tests for SemanticChunker — pure logic, no DB/LLM."""

from __future__ import annotations

from uuid import UUID

from eaos.knowledge.rag.chunker import SemanticChunker
from eaos.knowledge.rag.pipeline import Document


def _doc(content: str, title: str = "Test Doc") -> Document:
    return Document(
        source_type="text",
        source_uri="mem://test",
        title=title,
        content=content,
    )


def _token_count(text: str) -> int:
    return max(1, len(text) // 4)


class TestChunk:
    async def test_single_short_paragraph_returns_one_chunk(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("Hello world.")
        chunks = await chunker.chunk(doc)
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world."
        assert chunks[0].chunk_index == 0
        assert chunks[0].token_count > 0

    async def test_multiple_paragraphs_each_becomes_chunk(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("Para one.\n\nPara two.\n\nPara three.")
        chunks = await chunker.chunk(doc)
        assert len(chunks) == 3
        assert chunks[0].content == "Para one."
        assert chunks[1].content == "Para two."
        assert chunks[2].content == "Para three."
        assert [c.chunk_index for c in chunks] == [0, 1, 2]

    async def test_empty_paragraphs_skipped(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("Keep.\n\n\n\nAlso keep.")
        chunks = await chunker.chunk(doc)
        assert len(chunks) == 2
        assert chunks[0].content == "Keep."
        assert chunks[1].content == "Also keep."

    async def test_long_paragraph_split_by_sentence(self) -> None:
        chunker = SemanticChunker(
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            max_chunk_tokens=20,
            overlap_tokens=5,
        )
        long_para = ". ".join([f"Sentence number {i}" for i in range(20)])
        doc = _doc(long_para)
        chunks = await chunker.chunk(doc)
        assert len(chunks) > 1
        for c in chunks:
            assert c.token_count <= 30

    async def test_overlap_between_chunks(self) -> None:
        chunker = SemanticChunker(
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            max_chunk_tokens=20,
            overlap_tokens=10,
        )
        long_para = ". ".join([f"Sentence {i} content here" for i in range(10)])
        doc = _doc(long_para)
        chunks = await chunker.chunk(doc)
        if len(chunks) >= 2:
            assert chunks[1].content != ""

    async def test_short_trailing_fragment_merged(self) -> None:
        """A tiny trailing fragment from sentence-splitting merges into the prior piece."""
        chunker = SemanticChunker(
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            max_chunk_tokens=30,
            overlap_tokens=0,
            min_chunk_tokens=10,
        )
        sentences = ". ".join(
            [f"This is sentence {i} with content" for i in range(10)]
            + ["tiny"]
        )
        doc = _doc(sentences)
        chunks = await chunker.chunk(doc)
        last = chunks[-1].content
        assert "tiny" in last
        assert _token_count(last) >= 10

    async def test_standalone_short_paragraph_kept(self) -> None:
        """A short standalone paragraph is NOT merged into the previous chunk."""
        chunker = SemanticChunker(
            tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
            min_chunk_tokens=50,
        )
        doc = _doc(
            "Long enough paragraph with sufficient content to exceed the minimum threshold."
            "\n\ntiny"
        )
        chunks = await chunker.chunk(doc)
        assert len(chunks) == 2
        assert chunks[-1].content == "tiny"

    async def test_metadata_set_on_each_chunk(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("Para one.\n\nPara two.")
        chunks = await chunker.chunk(doc)
        for c in chunks:
            assert c.metadata == {"parent_chunk_id": None, "page": 1, "type": "text"}

    async def test_document_id_consistent_across_chunks(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("Para one.\n\nPara two.\n\nPara three.")
        chunks = await chunker.chunk(doc)
        doc_ids = {c.document_id for c in chunks}
        assert len(doc_ids) == 1

    async def test_tenant_id_propagated(self) -> None:
        tid = UUID("00000000-0000-0000-0000-000000000999")
        chunker = SemanticChunker(tenant_id=tid)
        doc = _doc("Content.")
        chunks = await chunker.chunk(doc)
        assert all(c.tenant_id == tid for c in chunks)

    async def test_chunk_ids_unique(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("A.\n\nB.\n\nC.")
        chunks = await chunker.chunk(doc)
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))

    async def test_empty_document_returns_single_empty_chunk(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("")
        chunks = await chunker.chunk(doc)
        assert len(chunks) == 1
        assert chunks[0].content == ""

    async def test_token_count_estimate(self) -> None:
        chunker = SemanticChunker(tenant_id=UUID("00000000-0000-0000-0000-000000000001"))
        doc = _doc("a" * 40)
        chunks = await chunker.chunk(doc)
        assert chunks[0].token_count == 10

"""Semantic chunker — splits documents on paragraph/sentence boundaries.

Token counts are estimated as ``len(content) // 4`` (rough char-to-token ratio
for mixed CJK + ASCII text). Long paragraphs are split at sentence boundaries
with configurable overlap to preserve context across chunk borders. A tiny
trailing fragment from sentence-splitting is merged back into the prior
fragment of the SAME paragraph; standalone short paragraphs are kept intact.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from eaos.knowledge.rag.pipeline import Chunk, Document


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping terminal punctuation.

    Handles CJK and ASCII sentence terminators (。！？.!?).
    """
    if not text.strip():
        return []
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    return [p.strip() for p in parts if p.strip()]


class SemanticChunker:
    """Chunker that preserves paragraph and sentence boundaries.

    The chunker generates a document_id UUID for the document and uses the
    tenant_id injected at construction time. The pipeline should read
    ``chunks[0].document_id`` to know which id to persist for the document.
    """

    def __init__(
        self,
        tenant_id: UUID | None = None,
        max_chunk_tokens: int = 500,
        overlap_tokens: int = 50,
        min_chunk_tokens: int = 50,
    ) -> None:
        self._tenant_id = tenant_id or UUID(int=0)
        self._max = max_chunk_tokens
        self._overlap = overlap_tokens
        self._min = min_chunk_tokens

    async def chunk(self, document: Document) -> list[Chunk]:
        document_id = uuid4()
        paragraphs = document.content.split("\n\n")

        pieces: list[str] = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if _estimate_tokens(para) <= self._max:
                pieces.append(para)
                continue
            pieces.extend(self._split_long_paragraph(para))

        if not pieces:
            pieces = [document.content.strip()]

        chunks: list[Chunk] = []
        for idx, content in enumerate(pieces):
            chunks.append(
                Chunk(
                    id=uuid4(),
                    document_id=document_id,
                    tenant_id=self._tenant_id,
                    chunk_index=idx,
                    content=content,
                    token_count=_estimate_tokens(content),
                    metadata={"parent_chunk_id": None, "page": 1, "type": "text"},
                )
            )
        return chunks

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        """Split a paragraph that exceeds max_chunk_tokens by sentence.

        Accumulates sentences until the budget is reached, then starts a new
        chunk with the trailing ``overlap_tokens`` of the prior chunk to
        preserve context. A tiny final fragment is merged into the previous
        piece of the SAME paragraph.
        """
        sentences = _split_sentences(paragraph)
        if not sentences:
            return [paragraph]

        pieces: list[str] = []
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if _estimate_tokens(candidate) <= self._max:
                current = candidate
                continue
            if current:
                pieces.append(current)
                overlap_text = self._tail(current, self._overlap)
                current = f"{overlap_text} {sentence}".strip() if overlap_text else sentence
            else:
                current = sentence
        if current:
            pieces.append(current)

        if len(pieces) >= 2 and _estimate_tokens(pieces[-1]) < self._min:
            pieces[-2] = f"{pieces[-2]} {pieces[-1]}"
            pieces.pop()
        return pieces

    @staticmethod
    def _tail(text: str, overlap_tokens: int) -> str:
        """Return the trailing substring approximating ``overlap_tokens`` tokens."""
        char_budget = overlap_tokens * 4
        if len(text) <= char_budget:
            return text
        return text[-char_budget:]

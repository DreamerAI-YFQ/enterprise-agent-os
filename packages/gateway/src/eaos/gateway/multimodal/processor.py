"""Multimodal input processor — images, voice, files.

Users can send images (screenshots for diagnosis), voice (ASR), files (PDF/
Excel for analysis). This processor normalizes all to text parts that the
agent's understand node can consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from eaos.core.context import TenantContext
    from eaos.gateway.im.message import Attachment


@dataclass(frozen=True)
class RawInput:
    """Raw user input with text and attachments."""

    text: str
    attachments: list[Attachment] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessedPart:
    """A processed input part."""

    type: str  # text/image_desc/voice_transcript/file_content
    content: str
    source: str | None = None  # attachment url


@dataclass(frozen=True)
class ProcessedInput:
    """Normalized multimodal input for agent consumption."""

    parts: list[ProcessedPart] = field(default_factory=list)


class MultimodalProcessor(Protocol):
    """Multimodal input normalizer."""

    async def process(
        self,
        raw_input: RawInput,
        ctx: TenantContext,
    ) -> ProcessedInput:
        """Process attachments and text into normalized parts.

        - images: vision model description + optional OCR
        - voice: ASR transcription
        - files: parse to text (PDF/Excel/PPT)
        """
        ...

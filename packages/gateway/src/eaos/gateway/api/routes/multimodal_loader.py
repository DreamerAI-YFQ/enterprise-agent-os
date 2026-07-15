"""Multimodal attachment loader.

Converts an ``AttachmentRef`` (file_id + url returned by the upload endpoint)
into an ``Attachment`` ready to attach to a ``Message``. Images are inlined
as base64 data URLs; PDFs and text files have their text extracted.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from eaos.infra.llm.base import Attachment

if TYPE_CHECKING:
    from eaos.core.auth import Principal

logger = logging.getLogger(__name__)

_IMAGE_MIME_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

_TEXT_MIME_TYPES: set[str] = {
    "text/plain",
    "text/markdown",
    "text/csv",
}


def _classify(mime_type: str) -> str:
    """Return 'image' for image MIME types, else 'file'."""
    if mime_type in _IMAGE_MIME_TYPES:
        return "image"
    return "file"


async def load_attachment(
    file_path: str,
    *,
    mime_type: str,
    name: str,
    base_dir: str = "uploads",
) -> Attachment:
    """Load a stored upload file and build an Attachment.

    For images, the file is base64-encoded into a ``data:`` URL so the LLM
    adapter can pass it directly to the vision API. For PDF/text files, the
    text content is extracted and stored in ``text_content``.

    Raises FileNotFoundError if the file is missing on disk.
    """
    # upload_url returns paths like "/uploads/{tenant}/{file_id}.{ext}";
    # base_dir already points to the uploads root, so strip the "uploads/"
    # prefix before joining.
    relative_path = file_path.lstrip("/")
    if relative_path.startswith("uploads/"):
        relative_path = relative_path[len("uploads/"):]
    full_path = Path(base_dir) / relative_path
    if not full_path.exists():
        raise FileNotFoundError(f"attachment not found: {full_path}")

    att_type = _classify(mime_type)
    if att_type == "image":
        data = full_path.read_bytes()
        b64 = base64.b64encode(data).decode()
        return Attachment(
            type="image",
            mime_type=mime_type,
            name=name,
            data_url=f"data:{mime_type};base64,{b64}",
        )

    text = await _extract_text(full_path, mime_type)
    return Attachment(
        type="file",
        mime_type=mime_type,
        name=name,
        text_content=text,
    )


async def _extract_text(path: Path, mime_type: str) -> str:
    """Extract text content from a file based on its MIME type."""
    if mime_type == "application/pdf":
        return _extract_pdf_text(path)
    if mime_type in _TEXT_MIME_TYPES:
        return path.read_text(encoding="utf-8", errors="replace")
    # Fallback: try reading as text.
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        logger.warning("pypdf not installed — PDF text extraction unavailable")
        raise RuntimeError("pypdf not installed") from exc

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages)


def resolve_upload_path(
    file_id: str,
    *,
    tenant_id: str,
    extension: str,
    base_dir: str = "uploads",
) -> Path:
    """Build the canonical upload path: ``{base_dir}/{tenant_id}/{file_id}.{ext}``."""
    return Path(base_dir) / tenant_id / f"{file_id}.{extension}"


def upload_url(
    file_id: str,
    *,
    tenant_id: str,
    extension: str,
) -> str:
    """Build the public URL for an uploaded file."""
    return f"/uploads/{tenant_id}/{file_id}.{extension}"


def extension_for_mime(mime_type: str) -> str:
    """Return the file extension for a MIME type."""
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "application/pdf": "pdf",
        "text/plain": "txt",
        "text/markdown": "md",
        "text/csv": "csv",
    }.get(mime_type, "bin")

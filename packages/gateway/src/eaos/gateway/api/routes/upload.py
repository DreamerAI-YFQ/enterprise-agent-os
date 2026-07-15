"""File upload endpoint for multimodal attachments.

POST /upload — accepts a single file (multipart/form-data), validates its
MIME type and size, stores it under ``uploads/{tenant_id}/{file_id}.{ext}``,
and returns the file_id + URL that the client can reference in a subsequent
POST /invoke ``attachments`` field.

Available to all authenticated users (employees + admins); authorization is
the JWT principal injected by ``get_principal``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_principal
from eaos.gateway.api.routes.multimodal_loader import (
    extension_for_mime,
    resolve_upload_path,
    upload_url,
)
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.core.config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])


class UploadResponse(BaseModel):
    """Response body for POST /upload."""

    file_id: str
    url: str
    type: str  # image | file
    name: str
    mime_type: str
    size_bytes: int


def _allowed_mime_types(config: AppConfig) -> set[str]:
    return {m.strip() for m in config.uploads.allowed_mime_types.split(",") if m.strip()}


def _uploads_dir(config: AppConfig) -> Path:
    return Path(config.uploads.dir)


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile,  # noqa: B008
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> UploadResponse:
    """Upload a single file (image, PDF, or text) for multimodal use.

    Returns the file_id and URL that can be referenced in POST /invoke
    ``attachments``.
    """
    config: AppConfig = request.app.state.config
    allowed = _allowed_mime_types(config)
    max_bytes = config.uploads.max_size_mb * 1024 * 1024

    if file.content_type is None or file.content_type not in allowed:
        raise HTTPException(
            status_code=415,
            detail=(
                f"unsupported file type: {file.content_type}. "
                f"Allowed: {sorted(allowed)}"
            ),
        )

    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file too large: {len(content)} bytes "
                f"(max {config.uploads.max_size_mb} MB)"
            ),
        )

    file_id = str(uuid4())
    extension = extension_for_mime(file.content_type)
    tenant_str = str(principal.tenant_id)
    file_path = resolve_upload_path(
        file_id,
        tenant_id=tenant_str,
        extension=extension,
        base_dir=config.uploads.dir,
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)

    url = upload_url(file_id, tenant_id=tenant_str, extension=extension)
    original_name = file.filename or f"{file_id}.{extension}"
    att_type = "image" if file.content_type.startswith("image/") else "file"

    logger.info(
        "uploaded file_id=%s tenant=%s type=%s size=%d name=%s",
        file_id,
        tenant_str,
        att_type,
        len(content),
        original_name,
    )

    return UploadResponse(
        file_id=file_id,
        url=url,
        type=att_type,
        name=original_name,
        mime_type=file.content_type,
        size_bytes=len(content),
    )

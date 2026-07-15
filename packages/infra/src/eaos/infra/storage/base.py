"""File storage protocol for sandbox artifacts, uploaded documents, model weights."""

from __future__ import annotations

from typing import Protocol


class FileStorage(Protocol):
    """Blob storage protocol. Implementations: local FS (dev), S3 (prod)."""

    async def upload(self, key: str, data: bytes) -> str:
        """Upload bytes, return the storage URI (e.g. s3://bucket/key)."""
        ...

    async def download(self, key: str) -> bytes:
        """Download bytes by key."""
        ...

    async def delete(self, key: str) -> None:
        """Delete by key."""
        ...

    async def get_signed_url(self, key: str, expires: int = 3600) -> str:
        """Generate a time-limited download URL."""
        ...

    async def exists(self, key: str) -> bool:
        """Check existence."""
        ...

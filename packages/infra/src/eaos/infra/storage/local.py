"""LocalFileStorage: filesystem-backed FileStorage for dev environments.

Implements the FileStorage protocol against the local filesystem. Suitable for
development; production should use S3-backed storage. get_signed_url returns a
file:// URL (no real signing — dev only).

Path traversal protection: all keys are resolved under the configured root and
rejected if they escape it (e.g. ``../etc/passwd``).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from eaos.core.errors import DataError, NotFoundError


class LocalFileStorage:
    """FileStorage backed by the local filesystem."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        """Resolve `key` under root, rejecting path traversal."""
        root = self._root.resolve()
        target = (root / key).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise DataError(f"path traversal detected: {key!r}") from exc
        return target

    async def upload(self, key: str, data: bytes) -> str:
        path = self._resolve_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{path.as_posix()}"

    async def download(self, key: str) -> bytes:
        path = self._resolve_path(key)
        if not path.exists():
            raise NotFoundError(f"file not found: {key}")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        path = self._resolve_path(key)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    async def get_signed_url(self, key: str, expires: int = 3600) -> str:
        # Dev-only: returns file:// URL. Real signing requires S3 in prod.
        path = self._resolve_path(key)
        return f"file://{path.as_posix()}"

    async def exists(self, key: str) -> bool:
        path = self._resolve_path(key)
        return path.exists()

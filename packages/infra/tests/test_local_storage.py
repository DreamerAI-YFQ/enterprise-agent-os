"""Unit tests for LocalFileStorage.

Uses pytest's tmp_path fixture for isolation. No live services required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from eaos.core.errors import DataError, NotFoundError
from eaos.infra.storage.local import LocalFileStorage

if TYPE_CHECKING:
    from pathlib import Path


class TestUploadDownload:
    async def test_upload_returns_file_uri(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        uri = await storage.upload("docs/readme.txt", b"hello")
        assert uri.startswith("file://")
        assert "docs/readme.txt" in uri

    async def test_download_roundtrip(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        await storage.upload("data.bin", b"\x00\x01\x02")
        data = await storage.download("data.bin")
        assert data == b"\x00\x01\x02"

    async def test_download_missing_raises(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        with pytest.raises(NotFoundError, match="file not found"):
            await storage.download("nonexistent.txt")

    async def test_upload_creates_nested_dirs(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        await storage.upload("a/b/c/deep.txt", b"deep")
        data = await storage.download("a/b/c/deep.txt")
        assert data == b"deep"


class TestExists:
    async def test_exists_true_after_upload(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        await storage.upload("f.txt", b"x")
        assert await storage.exists("f.txt") is True

    async def test_exists_false_for_missing(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        assert await storage.exists("missing.txt") is False


class TestDelete:
    async def test_delete_removes_file(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        await storage.upload("to_delete.txt", b"x")
        await storage.delete("to_delete.txt")
        assert await storage.exists("to_delete.txt") is False

    async def test_delete_missing_is_noop(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        # Should not raise
        await storage.delete("never_existed.txt")


class TestGetSignedUrl:
    async def test_returns_file_url(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        url = await storage.get_signed_url("some/file.txt")
        assert url.startswith("file://")

    async def test_ignores_expires(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        url1 = await storage.get_signed_url("f.txt", expires=60)
        url2 = await storage.get_signed_url("f.txt", expires=3600)
        # Dev mode: expires is ignored, URLs identical
        assert url1 == url2


class TestPathTraversal:
    async def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        with pytest.raises(DataError, match="path traversal"):
            await storage.upload("../etc/passwd", b"evil")

    async def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        with pytest.raises(DataError, match="path traversal"):
            await storage.upload("/etc/passwd", b"evil")

    async def test_rejects_nested_traversal(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        with pytest.raises(DataError, match="path traversal"):
            await storage.download("valid/../../../etc/shadow")

    async def test_allows_normal_nested_keys(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(tmp_path)
        await storage.upload("knowledge/docs/chunk_1.txt", b"ok")
        assert await storage.exists("knowledge/docs/chunk_1.txt") is True


class TestInit:
    def test_creates_root_if_missing(self, tmp_path: Path) -> None:
        root = tmp_path / "storage_root"
        assert not root.exists()
        LocalFileStorage(root)
        assert root.exists()

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        storage = LocalFileStorage(str(tmp_path))
        assert storage._root == tmp_path

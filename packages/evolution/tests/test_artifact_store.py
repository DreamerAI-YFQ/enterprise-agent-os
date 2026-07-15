"""Tests for ArtifactStore — LocalArtifactStore + build_artifact_store factory.

S3ArtifactStore and OssArtifactStore require boto3/oss2 and cloud credentials;
they are not unit-tested here. The factory's backend selection is tested
with config-only assertions (no network).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from eaos.core.config import ArtifactConfig
from eaos.evolution.artifact_store import (
    LocalArtifactStore,
    S3ArtifactStore,
    build_artifact_store,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestLocalArtifactStore:
    async def test_save_copies_directory_and_returns_file_uri(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "model.bin").write_bytes(b"weights")
        (src / "config.json").write_text('{"model": "dpo"}')

        base_dir = tmp_path / "artifacts"
        config = ArtifactConfig(backend="local", base_dir=str(base_dir))
        store = LocalArtifactStore(config)

        run_id = uuid4()
        uri = await store.save(run_id, src)

        assert uri.startswith("file://")
        assert str(run_id) in uri
        dest = base_dir / str(run_id)
        assert (dest / "model.bin").read_bytes() == b"weights"
        assert (dest / "config.json").read_text() == '{"model": "dpo"}'

    async def test_load_returns_path_for_existing_artifact(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "model.bin").write_bytes(b"weights")

        base_dir = tmp_path / "artifacts"
        config = ArtifactConfig(backend="local", base_dir=str(base_dir))
        store = LocalArtifactStore(config)

        run_id = uuid4()
        await store.save(run_id, src)

        loaded = await store.load(run_id)
        assert loaded.exists()
        assert (loaded / "model.bin").read_bytes() == b"weights"

    async def test_load_raises_for_missing_artifact(self, tmp_path: Path) -> None:
        config = ArtifactConfig(backend="local", base_dir=str(tmp_path))
        store = LocalArtifactStore(config)
        with pytest.raises(FileNotFoundError):
            await store.load(uuid4())

    async def test_save_overwrites_existing_dest(self, tmp_path: Path) -> None:
        src1 = tmp_path / "src1"
        src1.mkdir()
        (src1 / "v1.txt").write_text("v1")

        src2 = tmp_path / "src2"
        src2.mkdir()
        (src2 / "v2.txt").write_text("v2")

        base_dir = tmp_path / "artifacts"
        config = ArtifactConfig(backend="local", base_dir=str(base_dir))
        store = LocalArtifactStore(config)

        run_id = uuid4()
        await store.save(run_id, src1)
        await store.save(run_id, src2)

        dest = base_dir / str(run_id)
        assert not (dest / "v1.txt").exists()
        assert (dest / "v2.txt").read_text() == "v2"


class TestBuildArtifactStore:
    def test_local_backend(self) -> None:
        config = ArtifactConfig(backend="local")
        store = build_artifact_store(config)
        assert isinstance(store, LocalArtifactStore)

    def test_s3_backend(self) -> None:
        config = ArtifactConfig(backend="s3", s3_bucket="my-bucket")
        store = build_artifact_store(config)
        assert isinstance(store, S3ArtifactStore)

    def test_s3_backend_requires_bucket(self) -> None:
        config = ArtifactConfig(backend="s3", s3_bucket=None)
        with pytest.raises(ValueError, match="S3_BUCKET"):
            build_artifact_store(config)

    def test_oss_backend(self) -> None:
        from eaos.evolution.artifact_store import OssArtifactStore

        config = ArtifactConfig(backend="oss", oss_bucket="my-bucket")
        store = build_artifact_store(config)
        assert isinstance(store, OssArtifactStore)

    def test_oss_backend_requires_bucket(self) -> None:
        config = ArtifactConfig(backend="oss", oss_bucket=None)
        with pytest.raises(ValueError, match="OSS_BUCKET"):
            build_artifact_store(config)

    def test_unknown_backend_raises(self) -> None:
        config = ArtifactConfig(backend="gcs")
        with pytest.raises(ValueError, match="unknown artifact backend"):
            build_artifact_store(config)

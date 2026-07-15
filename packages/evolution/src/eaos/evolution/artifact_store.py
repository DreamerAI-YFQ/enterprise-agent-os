"""Artifact store — abstracts model artifact persistence across backends.

DPO training produces a model artifact directory (weights + tokenizer +
config). ``ArtifactStore`` abstracts where that artifact lives after training:
local filesystem (default), AWS S3, or Alibaba OSS. The trainer's background
worker calls ``save(run_id, local_path)`` to upload the artifact and stores
the returned URI in ``evolution.training_runs.model_artifact_path``.

Backend selection is config-driven (``EAOS_ARTIFACT__BACKEND=local|s3|oss``).
S3 (boto3) and OSS (oss2) are lazily imported so the core packages don't
require the heavy cloud SDKs unless that backend is actually configured.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.core.config import ArtifactConfig


class ArtifactStore(Protocol):
    """Persist and retrieve model artifacts by training run ID."""

    async def save(self, run_id: UUID, local_path: Path) -> str:
        """Upload ``local_path`` for ``run_id``; return a backend-specific URI."""
        ...

    async def load(self, run_id: UUID) -> Path:
        """Download the artifact for ``run_id`` to a local temp path."""
        ...


class LocalArtifactStore:
    """Filesystem-backed store — copies artifact into a structured base dir.

    Default backend. Mirrors the pre-T13 behavior where artifacts stayed in
    ``EAOS_MODEL_ARTIFACT_DIR/<run_id>/``. Returns a ``file://`` URI.
    """

    def __init__(self, config: ArtifactConfig) -> None:
        self._base_dir = Path(config.base_dir)

    async def save(self, run_id: UUID, local_path: Path) -> str:
        dest = self._base_dir / str(run_id)
        await asyncio.to_thread(self._copy_tree, local_path, dest)
        return dest.as_uri()

    async def load(self, run_id: UUID) -> Path:
        path = self._base_dir / str(run_id)
        if not path.exists():
            raise FileNotFoundError(f"artifact not found for run {run_id}")
        return path

    @staticmethod
    def _copy_tree(src: Path, dest: Path) -> None:
        if src == dest:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)


class S3ArtifactStore:
    """S3-backed store via boto3. Lazily imports boto3 on first use."""

    def __init__(self, config: ArtifactConfig) -> None:
        if config.s3_bucket is None:
            raise ValueError("S3ArtifactStore requires EAOS_ARTIFACT__S3_BUCKET")
        self._bucket = config.s3_bucket
        self._prefix = config.s3_prefix.rstrip("/")
        self._config = config

    async def save(self, run_id: UUID, local_path: Path) -> str:
        key = f"{self._prefix}/{run_id}/"
        await asyncio.to_thread(self._upload_dir, local_path, key)
        return f"s3://{self._bucket}/{key}"

    async def load(self, run_id: UUID) -> Path:
        import tempfile

        key = f"{self._prefix}/{run_id}/"
        dest = Path(tempfile.mkdtemp()) / str(run_id)
        await asyncio.to_thread(self._download_dir, key, dest)
        return dest

    def _client(self) -> Any:
        import boto3  # type: ignore[import-not-found]

        return boto3.client(
            "s3",
            region_name=self._config.s3_region or None,
            endpoint_url=self._config.s3_endpoint_url or None,
            aws_access_key_id=self._config.s3_access_key_id or None,
            aws_secret_access_key=self._config.s3_secret_access_key or None,
        )

    def _upload_dir(self, local_path: Path, key_prefix: str) -> None:
        client = self._client()
        for file_path in local_path.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_path).as_posix()
                key = f"{key_prefix}{relative}"
                client.upload_file(str(file_path), self._bucket, key)

    def _download_dir(self, key_prefix: str, dest: Path) -> None:
        client = self._client()
        dest.mkdir(parents=True, exist_ok=True)
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                relative = key[len(key_prefix):]
                if not relative:
                    continue
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                client.download_file(self._bucket, key, str(target))


class OssArtifactStore:
    """Alibaba OSS-backed store via oss2. Lazily imports oss2 on first use."""

    def __init__(self, config: ArtifactConfig) -> None:
        if config.oss_bucket is None:
            raise ValueError("OssArtifactStore requires EAOS_ARTIFACT__OSS_BUCKET")
        self._bucket_name = config.oss_bucket
        self._prefix = config.oss_prefix.rstrip("/")
        self._config = config

    async def save(self, run_id: UUID, local_path: Path) -> str:
        key = f"{self._prefix}/{run_id}/"
        await asyncio.to_thread(self._upload_dir, local_path, key)
        return f"oss://{self._bucket_name}/{key}"

    async def load(self, run_id: UUID) -> Path:
        import tempfile

        key = f"{self._prefix}/{run_id}/"
        dest = Path(tempfile.mkdtemp()) / str(run_id)
        await asyncio.to_thread(self._download_dir, key, dest)
        return dest

    def _bucket(self) -> Any:
        import oss2  # type: ignore[import-not-found]

        auth = oss2.Auth(
            self._config.oss_access_key_id or "",
            self._config.oss_access_key_secret or "",
        )
        return oss2.Bucket(
            auth, self._config.oss_endpoint or "", self._bucket_name
        )

    def _upload_dir(self, local_path: Path, key_prefix: str) -> None:
        bucket = self._bucket()
        for file_path in local_path.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(local_path).as_posix()
                key = f"{key_prefix}{relative}"
                bucket.put_object_from_file(key, str(file_path))

    def _download_dir(self, key_prefix: str, dest: Path) -> None:
        bucket = self._bucket()
        dest.mkdir(parents=True, exist_ok=True)
        for obj in bucket.list_objects(prefix=key_prefix).object_list:
            key = obj.key
            relative = key[len(key_prefix):]
            if not relative:
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            bucket.get_object_to_file(key, str(target))


def build_artifact_store(config: ArtifactConfig) -> ArtifactStore:
    """Construct the configured ArtifactStore implementation."""
    backend = config.backend.lower()
    if backend == "local":
        return LocalArtifactStore(config)
    if backend == "s3":
        return S3ArtifactStore(config)
    if backend == "oss":
        return OssArtifactStore(config)
    raise ValueError(f"unknown artifact backend: {config.backend}")

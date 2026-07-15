"""Application configuration via Pydantic Settings.

Layered loading: defaults -> env vars -> config file -> runtime override.
Sensitive values (API keys, DB passwords) MUST come from env vars, never from
committed config files.

Env var convention: ``EAOS_{SECTION}__{FIELD}`` (double underscore separator).
Example: ``EAOS_DB__URL``, ``EAOS_LLM__OPENAI_API_KEY``, ``EAOS_APP__SECRET_KEY``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """PostgreSQL connection configuration."""

    url: str = "postgresql+asyncpg://eaos:eaos@localhost:5432/eaos"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False

    model_config = SettingsConfigDict(env_prefix="EAOS_DB__", extra="ignore")


class RedisConfig(BaseSettings):
    """Redis connection configuration."""

    url: str = "redis://localhost:6379/0"
    max_connections: int = 50

    model_config = SettingsConfigDict(env_prefix="EAOS_REDIS__", extra="ignore")


class LLMConfig(BaseSettings):
    """LLM provider credentials and defaults."""

    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI-compatible base URL (DashScope, etc.)
    anthropic_api_key: str | None = None
    glm_api_key: str | None = None
    default_model: str = "gpt-4o-mini"
    vision_model: str | None = None  # override default vision model (e.g. qwen3-omni-flash)
    request_timeout_sec: int = 60

    model_config = SettingsConfigDict(env_prefix="EAOS_LLM__", extra="ignore")


class SlackConfig(BaseSettings):
    """Slack bot credentials."""

    bot_token: str | None = None
    signing_secret: str | None = None

    model_config = SettingsConfigDict(env_prefix="EAOS_SLACK__", extra="ignore")


class DingTalkConfig(BaseSettings):
    """DingTalk bot credentials."""

    app_key: str | None = None
    app_secret: str | None = None
    robot_code: str | None = None

    model_config = SettingsConfigDict(env_prefix="EAOS_DINGTALK__", extra="ignore")


class WeComConfig(BaseSettings):
    """WeCom (企业微信) bot credentials."""

    corp_id: str | None = None
    agent_id: str | None = None
    secret: str | None = None
    token: str | None = None  # callback verification token
    encoding_aes_key: str | None = None  # Phase 3: unused (no AES decryption)

    model_config = SettingsConfigDict(env_prefix="EAOS_WECOM__", extra="ignore")


class FeishuConfig(BaseSettings):
    """Feishu (飞书) app credentials."""

    app_id: str | None = None
    app_secret: str | None = None
    verification_token: str | None = None  # callback verification
    encrypt_key: str | None = None  # signature computation

    model_config = SettingsConfigDict(env_prefix="EAOS_FEISHU__", extra="ignore")


class OTelConfig(BaseSettings):
    """OpenTelemetry exporter configuration."""

    endpoint: str | None = None
    service_name: str = "eaos-api"

    model_config = SettingsConfigDict(env_prefix="EAOS_OTEL__", extra="ignore")


class EmbeddingConfig(BaseSettings):
    """Embedding API configuration.

    API-based (no local model). Works with any OpenAI-compatible embeddings
    endpoint (OpenAI, Zhipu/GLM, local). Dimensions default to 1024 to match
    the schema's vector(1024) columns (bge-m3 footprint).
    """

    api_key: str | None = None
    base_url: str | None = None  # None -> OpenAI default; set for Zhipu/local
    model: str = "text-embedding-3-small"
    dimensions: int = 1024
    request_timeout_sec: int = 30

    model_config = SettingsConfigDict(env_prefix="EAOS_EMBEDDING__", extra="ignore")


class ArtifactConfig(BaseSettings):
    """Model artifact storage configuration.

    ``backend`` selects the storage strategy: ``local`` (filesystem, default),
    ``s3`` (AWS S3 via boto3), or ``oss`` (Alibaba OSS via oss2). S3/OSS
    clients are lazily imported so the core packages don't require the heavy
    cloud SDKs unless that backend is actually configured.
    """

    backend: str = "local"  # local | s3 | oss
    # local
    base_dir: str = "/tmp/eaos/models"
    # s3
    s3_bucket: str | None = None
    s3_prefix: str = "eaos-models"
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_endpoint_url: str | None = None  # MinIO/R2/etc.
    # oss
    oss_bucket: str | None = None
    oss_prefix: str = "eaos-models"
    oss_endpoint: str | None = None
    oss_access_key_id: str | None = None
    oss_access_key_secret: str | None = None

    model_config = SettingsConfigDict(env_prefix="EAOS_ARTIFACT__", extra="ignore")


class UploadsConfig(BaseSettings):
    """Uploads configuration for multimodal file attachments.

    Files are stored on the local filesystem under ``dir`` (relative to CWD
    or absolute). ``max_size_mb`` caps per-file size; ``allowed_mime_types``
    is the whitelist consulted by the upload endpoint.
    """

    dir: str = "uploads"
    max_size_mb: int = 10
    allowed_mime_types: str = (
        "image/jpeg,image/png,image/webp,image/gif,"
        "application/pdf,text/plain,text/markdown,text/csv"
    )

    model_config = SettingsConfigDict(env_prefix="EAOS_UPLOADS__", extra="ignore")


class AppConfig(BaseSettings):
    """Root application configuration aggregating all subsystem configs.

    Each subsystem is a ``BaseSettings`` subclass with its own
    ``EAOS_<SECTION>__`` prefix (e.g. ``EAOS_EMBEDDING__API_KEY``). To keep
    the root ``env_prefix`` from being prepended to those nested prefixes,
    the root prefix is empty and every nested config uses
    ``Field(default_factory=...)``. Top-level fields keep their legacy
    ``EAOS_APP__*`` aliases.
    """

    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    wecom: WeComConfig = Field(default_factory=WeComConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    otel: OTelConfig = Field(default_factory=OTelConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    artifact: ArtifactConfig = Field(default_factory=ArtifactConfig)
    uploads: UploadsConfig = Field(default_factory=UploadsConfig)

    secret_key: str = Field(
        default="dev-secret-change-in-prod", alias="EAOS_APP__SECRET_KEY"
    )
    debug: bool = Field(default=False, alias="EAOS_APP__DEBUG")
    environment: str = Field(default="local", alias="EAOS_APP__ENVIRONMENT")
    model_artifact_dir: Path = Field(
        default=Path("/tmp/eaos/models"), alias="EAOS_APP__MODEL_ARTIFACT_DIR"
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def load_config(cls, *, env_file: str | None = ".env") -> AppConfig:
        """Load AppConfig from environment variables and optional .env file.

        The .env file uses flat ``EAOS_<SECTION>__<FIELD>`` keys (e.g.
        ``EAOS_EMBEDDING__API_KEY``). ``python-dotenv`` loads them into
        ``os.environ`` so nested ``BaseSettings`` configs can read their own
        prefixed values. ``env_file=None`` skips file loading (used by tests
        that drive config purely through env vars).
        """
        if env_file is not None:
            load_dotenv(env_file, override=True)
        # Backward compatibility: tests and some deployments use the nested
        # ``EAOS_APP__<SECTION>__<FIELD>`` form (e.g. ``EAOS_APP__DB__URL``).
        # Mirror those values to the flat ``EAOS_<SECTION>__<FIELD>`` form so
        # subsystem configs still pick them up.
        for key in list(os.environ.keys()):
            if not key.startswith("EAOS_APP__"):
                continue
            rest = key[len("EAOS_APP__") :]
            if "__" not in rest:
                continue
            section, _ = rest.split("__", 1)
            flat_key = f"EAOS_{section}__{rest[len(section) + 2:]}"
            if flat_key not in os.environ:
                os.environ[flat_key] = os.environ[key]
        return cls()

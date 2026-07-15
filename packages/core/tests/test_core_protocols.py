"""Verify core Protocols and value objects match Phase 0 contract."""

from __future__ import annotations

import dataclasses

import pytest
from eaos.core.config import AppConfig, DatabaseConfig, LLMConfig, RedisConfig
from eaos.core.context import TenantContext
from eaos.core.errors import (
    CollaborationError,
    DataError,
    EaosError,
    HarnessViolationError,
    LLMError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    SkillExecutionError,
    ValidationError,
)


class TestTenantContext:
    def test_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(TenantContext)
        fields = {f.name for f in dataclasses.fields(TenantContext)}
        assert {
            "tenant_id",
            "user_id",
            "agent_id",
            "agent_scope",
            "session_id",
            "department_ids",
        } <= fields

    def test_thread_id_format_personal(self) -> None:
        from uuid import uuid4

        ctx = TenantContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="personal",
        )
        tid = ctx.thread_id
        assert tid.count(":") == 2
        assert ":shared" not in tid

    def test_thread_id_format_department_uses_shared(self) -> None:
        from uuid import uuid4

        ctx = TenantContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="department",
        )
        assert ctx.thread_id.endswith(":shared")

    def test_for_agent_derives_new_context(self) -> None:
        from uuid import uuid4

        ctx = TenantContext(
            tenant_id=uuid4(),
            user_id=uuid4(),
            agent_id=uuid4(),
            agent_scope="personal",
        )
        new_agent = uuid4()
        derived = ctx.for_agent(new_agent, scope="department")
        assert derived.agent_id == new_agent
        assert derived.agent_scope == "department"
        assert derived.tenant_id == ctx.tenant_id


class TestAppConfig:
    def test_has_required_subconfigs(self) -> None:
        cfg = AppConfig()
        assert isinstance(cfg.db, DatabaseConfig)
        assert isinstance(cfg.redis, RedisConfig)
        assert isinstance(cfg.llm, LLMConfig)

    def test_db_config_env_prefix(self) -> None:
        assert DatabaseConfig().model_config.get("env_prefix") == "EAOS_DB__"

    def test_llm_config_env_prefix(self) -> None:
        assert LLMConfig().model_config.get("env_prefix") == "EAOS_LLM__"

    def test_model_artifact_dir_default(self) -> None:
        cfg = AppConfig()
        assert cfg.model_artifact_dir.as_posix() == "/tmp/eaos/models"

    def test_load_config_picks_up_app_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EAOS_APP__SECRET_KEY", "from-env-secret")
        monkeypatch.setenv("EAOS_APP__MODEL_ARTIFACT_DIR", "/var/eaos/models")
        cfg = AppConfig.load_config(env_file=None)
        assert cfg.secret_key == "from-env-secret"
        assert cfg.model_artifact_dir.as_posix() == "/var/eaos/models"

    def test_load_config_nested_delimiter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EAOS_APP__DB__URL exercises the env_nested_delimiter path."""
        monkeypatch.setenv("EAOS_APP__DB__URL", "postgresql+asyncpg://u:p@db:5432/eaos")
        cfg = AppConfig.load_config(env_file=None)
        assert cfg.db.url == "postgresql+asyncpg://u:p@db:5432/eaos"


class TestErrors:
    @pytest.mark.parametrize(
        "exc_cls,expected_code",
        [
            (NotFoundError, "NOT_FOUND"),
            (PermissionDeniedError, "PERMISSION_DENIED"),
            (QuotaExceededError, "QUOTA_EXCEEDED"),
            (ValidationError, "VALIDATION_ERROR"),
            (SkillExecutionError, "SKILL_EXECUTION_ERROR"),
            (HarnessViolationError, "HARNESS_VIOLATION"),
            (DataError, "DATA_ERROR"),
            (LLMError, "LLM_ERROR"),
            (CollaborationError, "COLLABORATION_ERROR"),
        ],
    )
    def test_error_codes(self, exc_cls: type[EaosError], expected_code: str) -> None:
        exc = exc_cls()
        assert exc.code == expected_code
        assert isinstance(exc, EaosError)

    def test_error_message_override(self) -> None:
        exc = NotFoundError("custom message")
        assert exc.message == "custom message"

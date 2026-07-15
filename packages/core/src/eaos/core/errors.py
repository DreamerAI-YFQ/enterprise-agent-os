"""Unified error hierarchy for EAOS.

All business errors inherit from EaosError. Infrastructure exceptions are
translated into business errors at adapter boundaries.
"""

from __future__ import annotations


class EaosError(Exception):
    """Base class for all EAOS business errors."""

    code: str = "EAOS_ERROR"
    message: str = "An error occurred"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code


class NotFoundError(EaosError):
    """Resource not found."""

    code = "NOT_FOUND"
    message = "Resource not found"


class PermissionDeniedError(EaosError):
    """User/agent lacks permission for the action."""

    code = "PERMISSION_DENIED"
    message = "Permission denied"


class QuotaExceededError(EaosError):
    """Token or cost quota exceeded."""

    code = "QUOTA_EXCEEDED"
    message = "Quota exceeded"


class ValidationError(EaosError):
    """Input validation failed."""

    code = "VALIDATION_ERROR"
    message = "Validation failed"


class SkillExecutionError(EaosError):
    """Skill execution failed."""

    code = "SKILL_EXECUTION_ERROR"
    message = "Skill execution failed"


class HarnessViolationError(EaosError):
    """Harness policy violation (capability boundary, permission, compliance)."""

    code = "HARNESS_VIOLATION"
    message = "Harness policy violation"


class QualityViolationError(EaosError):
    """Quality gate violation (hallucination, degraded skill, low success rate)."""

    code = "QUALITY_VIOLATION"
    message = "Quality gate violation"


class DataError(EaosError):
    """Data layer error (connector, SQL, sandbox)."""

    code = "DATA_ERROR"
    message = "Data operation failed"


class LLMError(EaosError):
    """LLM API error."""

    code = "LLM_ERROR"
    message = "LLM call failed"


class CollaborationError(EaosError):
    """Multi-agent collaboration error."""

    code = "COLLABORATION_ERROR"
    message = "Collaboration failed"


class SandboxError(EaosError):
    """Code sandbox execution error."""

    code = "SANDBOX_ERROR"
    message = "Sandbox execution failed"


class EvolutionError(EaosError):
    """Agentic RL pipeline error."""

    code = "EVOLUTION_ERROR"
    message = "Evolution pipeline failed"

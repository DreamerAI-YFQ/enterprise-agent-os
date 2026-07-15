"""Skill specification — nine categories, three visibility scopes, guardrails.

Categories follow Anthropic's three-tier nine-cell structure, redefined for
all-employee scenarios. Third-tier (production) skills require guardrail config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


class SkillCategory(StrEnum):
    """Nine skill categories across three tiers."""

    # Tier 1: augment capabilities
    KNOWLEDGE_API = "knowledge_api"  # enterprise knowledge & API docs
    VERIFICATION = "verification"  # business rule verification
    DATA_ANALYSIS = "data_analysis"  # data query & analysis
    # Tier 2: daily workflows
    PROCESS_AUTOMATION = "process_automation"
    DOCUMENT_TEMPLATE = "document_template"
    QUALITY_REVIEW = "quality_review"
    # Tier 3: production operations
    SYSTEM_OPERATION = "system_operation"
    RUNBOOK = "runbook"
    INFRA_OPS = "infra_ops"


class SkillScope(StrEnum):
    """Three visibility scopes."""

    PERSONAL = "personal"
    DEPARTMENT = "department"
    COMPANY = "company"


class RiskLevel(StrEnum):
    """Risk level determines guardrail requirements."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Categories that require mandatory guardrail (production-tier)
PRODUCTION_CATEGORIES = frozenset(
    {
        SkillCategory.SYSTEM_OPERATION,
        SkillCategory.RUNBOOK,
        SkillCategory.INFRA_OPS,
    }
)


@dataclass(frozen=True)
class GuardrailConfig:
    """Guardrail for production-tier skills (three-step: notify, confirm, execute)."""

    confirm_required: bool  # human confirmation before execution
    auto_confirm_conditions: list[str] = field(default_factory=list)
    notify_channels: list[str] = field(default_factory=list)
    rollback_enabled: bool = False


@dataclass(frozen=True)
class ToolBinding:
    """Binds a skill to an MCP tool with parameter mapping.

    A skill may bind multiple tools; they execute sequentially in declaration
    order. ``param_mapping`` translates skill input keys to tool argument keys
    (simple 1:1 rename; no expression support to avoid injection risk).
    """

    tool_name: str  # fully-qualified "{server}.{tool}" or "{connector}_{op}"
    param_mapping: dict[str, str] = field(default_factory=dict)
    required: bool = True
    description: str | None = None


@dataclass(frozen=True)
class SkillSpec:
    """Complete skill specification."""

    id: UUID
    tenant_id: UUID
    scope: SkillScope
    owner_id: UUID | None  # personal: user_id; dept: dept_id; company: None
    name: str
    display_name: str
    description: str
    category: SkillCategory
    risk_level: RiskLevel
    instructions: str  # natural language usage for the model
    tools: list[str] = field(default_factory=list)  # required tool names
    tool_bindings: list[ToolBinding] = field(default_factory=list)
    guardrail: GuardrailConfig | None = None
    version: str = "0.1.0"
    status: str = "draft"  # draft/published/deprecated

    @property
    def requires_guardrail(self) -> bool:
        return self.category in PRODUCTION_CATEGORIES


@dataclass(frozen=True)
class SkillResult:
    """Result of skill execution."""

    success: bool
    output: str
    cost_tokens: int = 0
    error: str | None = None
    metadata: dict[str, Any] | None = None

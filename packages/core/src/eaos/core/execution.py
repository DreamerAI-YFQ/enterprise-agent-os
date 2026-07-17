"""Unified execution context and event contract (C01).

All tool invocations — read, write, skill, agent, knowledge — must use
``ToolExecutionContext`` and ``ToolInvocation``. This replaces the ad-hoc
``tenant_id``-only pattern with a full identity/governance/trace context.

Write paths require the full context; missing user/agent/session/trace
causes ``fail_closed()`` to raise, preventing unattributed writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

# -- C01-01: Action and resource vocabulary ----------------------------------


class Action(StrEnum):
    """Canonical action verbs used across Harness, audit, and evaluation."""

    AGENT_EXECUTE = "agent.execute"
    AGENT_COLLABORATE = "agent.collaborate"
    SKILL_EXECUTE = "skill.execute"
    KNOWLEDGE_READ = "knowledge.read"
    DATASOURCE_READ = "datasource.read"
    DATASOURCE_WRITE = "datasource.write"


# Standard resource namespaces (stable, not invented per-component)
# Examples: "erp.orders", "erp.products", "crm.leads", "knowledge.documents"
RESOURCE_SEP = "."


class RiskLevel(StrEnum):
    """Risk levels for governance decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# -- C01-02: ToolExecutionContext ---------------------------------------------


@dataclass(frozen=True)
class ToolExecutionContext:
    """Full execution context carried through every tool/skill/agent call.

    Replaces the old ``call_tool(tool, args, tenant_id)`` pattern.
    Write paths require all identity fields; ``fail_closed()`` enforces this.
    """

    tenant_id: UUID
    user_id: UUID
    agent_id: UUID
    session_id: UUID
    agent_scope: str  # personal | department | company
    department_ids: list[UUID] = field(default_factory=list)
    trace_id: UUID | None = None
    run_id: str | None = None  # evaluation run identifier
    case_id: str | None = None  # evaluation case identifier
    model_context: dict[str, Any] = field(default_factory=dict)
    policy_context: dict[str, Any] = field(default_factory=dict)

    def fail_closed(self, *, is_write: bool = False) -> None:
        """Validate context completeness. Write paths must have all fields.

        Raises ``ValueError`` if required fields are missing.
        Read paths only require tenant_id (backward compat).
        """
        if not is_write:
            if self.tenant_id is None:
                raise ValueError("ToolExecutionContext: tenant_id is required")
            return

        # Write path: all identity fields required
        missing: list[str] = []
        if self.tenant_id is None:
            missing.append("tenant_id")
        if self.user_id is None:
            missing.append("user_id")
        if self.agent_id is None:
            missing.append("agent_id")
        if self.session_id is None:
            missing.append("session_id")
        if self.trace_id is None:
            missing.append("trace_id")
        if missing:
            raise ValueError(
                f"ToolExecutionContext.fail_closed: write path missing required fields: "
                f"{', '.join(missing)}. Write operations must not proceed without "
                f"full identity/governance context."
            )
        # department_ids may be empty for personal scope, but must be explicit
        if self.agent_scope == "department" and not self.department_ids:
            raise ValueError(
                "ToolExecutionContext.fail_closed: department scope requires "
                "non-empty department_ids"
            )

    @property
    def thread_id(self) -> str:
        """LangGraph thread_id: tenant:agent:session."""
        return f"{self.tenant_id}:{self.agent_id}:{self.session_id}"

    def to_tenant_context(self) -> Any:
        """Adapter: convert to legacy TenantContext for backward compat."""
        from eaos.core.context import TenantContext

        return TenantContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            agent_scope=self.agent_scope,
            session_id=self.session_id,
            department_ids=list(self.department_ids),
        )

    def to_guard_context(
        self,
        action: str = "",
        resource: str = "",
        risk_level: str = "low",
    ) -> Any:
        """Adapter: convert to Harness GuardContext."""
        from eaos.harness.context import GuardContext

        return GuardContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            agent_scope=self.agent_scope,
            department_ids=list(self.department_ids),
            action=action,
            resource=resource,
            risk_level=risk_level,
            attributes={
                "session_id": self.session_id,
                "trace_id": self.trace_id,
                "run_id": self.run_id,
                "case_id": self.case_id,
            },
        )


# -- C01-02: ToolInvocation ---------------------------------------------------


@dataclass(frozen=True)
class ToolInvocation:
    """A single tool invocation request with governance metadata.

    Canonical arguments are the normalized, sorted key-value pairs that
    produce a stable hash for idempotency and audit.
    """

    tool_name: str
    resource: str  # e.g. "erp.orders", "knowledge.documents"
    operation: str  # create | read | update | delete | execute | list | describe
    canonical_arguments: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"  # low | medium | high
    idempotency_key: str | None = None
    action: str = ""  # Action enum value, set by caller

    def intent_digest(self) -> str:
        """Stable hash of tool+resource+operation+canonical_arguments.

        Used for approval binding and idempotency. Same digest = same intent.
        """
        import hashlib
        import json

        payload = json.dumps(
            {
                "tool": self.tool_name,
                "resource": self.resource,
                "operation": self.operation,
                "args": _canonicalize(self.canonical_arguments),
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_write_intent(self, ctx: ToolExecutionContext) -> Any:
        """Convert to WriteIntent for WritePipeline (write operations only)."""
        from eaos.harness.write_pipeline import WriteIntent

        return WriteIntent(
            tenant_id=ctx.tenant_id,
            principal_id=ctx.user_id,
            agent_id=ctx.agent_id,
            tool_name=self.tool_name,
            resource=self.resource,
            operation=self.operation,
            data=dict(self.canonical_arguments),
            agent_scope=ctx.agent_scope,
            trace_id=ctx.trace_id,
            session_id=ctx.session_id,
            risk_level=self.risk_level,
            department_ids=list(ctx.department_ids),
        )


# -- C01-02: Standardized result events --------------------------------------


@dataclass(frozen=True)
class ToolEvent:
    """Standardized tool execution event for tracing and audit.

    Events form a lifecycle:
        tool_started → tool_completed | tool_failed
        approval_required → (admin decision) → tool_completed | tool_failed
        guard_denied → (terminal)
        write_audited → rollback_completed | rollback_failed
    """

    type: str  # see ToolEventType
    tool_name: str
    resource: str
    operation: str
    trace_id: UUID | None = None
    session_id: UUID | None = None
    user_id: UUID | None = None
    agent_id: UUID | None = None
    approval_id: UUID | None = None
    audit_id: UUID | None = None
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class ToolEventType:
    """Standard event types for tool/skill/agent lifecycle."""

    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    APPROVAL_REQUIRED = "approval_required"
    GUARD_DENIED = "guard_denied"
    WRITE_AUDITED = "write_audited"
    ROLLBACK_COMPLETED = "rollback_completed"
    ROLLBACK_FAILED = "rollback_failed"


# -- Helpers ------------------------------------------------------------------


def _canonicalize(args: dict[str, Any]) -> dict[str, Any]:
    """Sort dict recursively for stable hashing."""
    result: dict[str, Any] = {}
    for k in sorted(args.keys()):
        v = args[k]
        if isinstance(v, dict):
            result[k] = _canonicalize(v)
        elif isinstance(v, list):
            result[k] = sorted(v, key=str) if all(not isinstance(x, dict) for x in v) else v
        else:
            result[k] = v
    return result


def build_idempotency_key(
    ctx: ToolExecutionContext,
    invocation: ToolInvocation,
) -> str:
    """Build a stable idempotency key from context + invocation.

    Same session + same intent = same key. Retries within the same session
    reuse the key, preventing duplicate writes.
    """
    import hashlib

    raw = f"{ctx.tenant_id}:{ctx.session_id}:{invocation.intent_digest()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

"""EAOS shared kernel package."""

__version__ = "0.1.0"

# C01: Unified execution contract
from eaos.core.execution import (
    Action,
    RiskLevel,
    ToolEvent,
    ToolEventType,
    ToolExecutionContext,
    ToolInvocation,
    build_idempotency_key,
)

"""Shared, framework-neutral contracts for AgentFlow.

This package owns the frozen public data models and Protocol interfaces:
events, provider requests/responses, tools, prompt/context snapshots and
stores. Later phases implement these contracts; they do not redefine them.
"""

from packages.core.errors import AgentFlowError, EventOrderError, StoreError
from packages.core.events import SCHEMA_VERSION, AgentEvent, AgentEventType
from packages.core.provider import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    ToolCallRequest,
    ToolSpec,
)
from packages.core.redaction import REDACTED_PLACEHOLDER, redact_payload
from packages.core.snapshots import (
    PROMPT_SECTION_ORDER,
    ContextSnapshot,
    PromptSection,
    PromptSnapshot,
)
from packages.core.stores import EventStore, SessionStore, SnapshotStore
from packages.core.tools import Tool, ToolContext, ToolResult

__all__ = [
    "PROMPT_SECTION_ORDER",
    "REDACTED_PLACEHOLDER",
    "SCHEMA_VERSION",
    "AgentEvent",
    "AgentEventType",
    "AgentFlowError",
    "ContextSnapshot",
    "EventOrderError",
    "EventStore",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "PromptSection",
    "PromptSnapshot",
    "SessionStore",
    "SnapshotStore",
    "StoreError",
    "TokenUsage",
    "Tool",
    "ToolCallRequest",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "redact_payload",
]

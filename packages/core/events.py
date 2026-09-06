"""AgentEvent model: the integration boundary (ADR-002).

Every lifecycle transition is emitted as a versioned, ordered,
JSON-serializable event. Consumers sort by ``sequence``, never by wall-clock
timestamp. Payloads must be redacted before persistence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class AgentEventType(str, Enum):
    """Lifecycle event types required by the event model contract."""

    SESSION_STARTED = "SessionStarted"
    PROMPT_BUILT = "PromptBuilt"
    CONTEXT_BUILT = "ContextBuilt"
    LLM_CALL_STARTED = "LLMCallStarted"
    LLM_CALL_FINISHED = "LLMCallFinished"
    TOOL_CALL_STARTED = "ToolCallStarted"
    TOOL_CALL_FINISHED = "ToolCallFinished"
    CONTEXT_COMPACTION_STARTED = "ContextCompactionStarted"
    CONTEXT_COMPACTION_FINISHED = "ContextCompactionFinished"
    AGENT_FINISHED = "AgentFinished"
    AGENT_FAILED = "AgentFailed"


class AgentEvent(BaseModel):
    """A single, immutable, append-only lifecycle event."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    timestamp: datetime
    event_type: AgentEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

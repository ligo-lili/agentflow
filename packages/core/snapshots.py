"""Prompt and context snapshot contracts (ADR-003).

Snapshots are first-class: before every model call the runtime persists a
PromptSnapshot and a ContextSnapshot so the question "what did the model see"
stays answerable after the fact. Every snapshot records the token estimator
name so results are comparable across runs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.core.provider import ModelMessage


class PromptSection(str, Enum):
    """Fixed prompt sections; the order below is frozen by contract."""

    BASE_SYSTEM = "base_system"
    AGENT_ROLE = "agent_role"
    PROJECT_CONTEXT = "project_context"
    RELEVANT_MEMORY = "relevant_memory"
    RELEVANT_SKILLS = "relevant_skills"
    CURRENT_TASK = "current_task"
    RUNTIME_CONTEXT = "runtime_context"
    RECENT_MESSAGES = "recent_messages"
    TOOL_RESULTS = "tool_results"


PROMPT_SECTION_ORDER: tuple[PromptSection, ...] = tuple(PromptSection)


class PromptSnapshot(BaseModel):
    """Immutable record of the assembled prompt at one point in time."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    created_at: datetime
    sections: tuple[PromptSection, ...]
    section_content: dict[PromptSection, str]
    estimator: str
    token_counts: dict[str, int]
    total_tokens: int = Field(..., ge=0)


class ContextSnapshot(BaseModel):
    """Immutable record of the model-bound context before one model call."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    created_at: datetime
    messages: tuple[ModelMessage, ...]
    component_tokens: dict[str, int]
    total_tokens: int = Field(..., ge=0)
    reserved_output_tokens: int = Field(..., ge=0)
    budget_limit: int | None = None
    compaction_state: str = "none"
    estimator: str
    metadata: dict[str, Any] = Field(default_factory=dict)

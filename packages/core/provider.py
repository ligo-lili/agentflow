"""Model provider contract (ADR-001): small, typed, replaceable.

The runtime depends on this Protocol only; concrete providers (FakeModelProvider
by default, OpenAI-compatible adapter optional) are injected explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ModelMessage(BaseModel):
    """One message in the conversation as seen by the model."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolSpec(BaseModel):
    """Tool description passed to the provider so it can request tool calls."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    """Everything a provider needs to produce one completion."""

    model_config = ConfigDict(frozen=True)

    model: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    temperature: float = 0.0
    max_output_tokens: int | None = None


class ToolCallRequest(BaseModel):
    """A tool invocation requested by the model."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    arguments: Mapping[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    """Reported token usage for a single model call."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ModelResponse(BaseModel):
    """The provider's answer for one request."""

    model_config = ConfigDict(frozen=True)

    message: ModelMessage
    finish_reason: Literal["stop", "tool_calls"]
    tool_calls: tuple[ToolCallRequest, ...] = ()
    usage: TokenUsage = TokenUsage()


@runtime_checkable
class ModelProvider(Protocol):
    """A replaceable chat-completion provider."""

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Execute one model call. Implementations must never raise silently."""
        ...

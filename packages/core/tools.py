"""Tool contract: typed tools executed by the runtime's ToolRuntime (T1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ToolContext(BaseModel):
    """Execution context handed to every tool run."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    trace_id: str
    step_index: int = Field(..., ge=0)


class ToolResult(BaseModel):
    """Structured result of one tool execution."""

    model_config = ConfigDict(frozen=True)

    name: str
    ok: bool
    value: Any = None
    error: str | None = None


@runtime_checkable
class Tool(Protocol):
    """A single named tool with a stable schema."""

    name: str
    description: str

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool. Failures must be reported, never swallowed."""
        ...

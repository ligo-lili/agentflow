"""Typed request/response contracts for the Inspector API.

Every route declares a ``response_model`` and documents its error statuses
with the shared :class:`ErrorResponse` envelope:

```json
{"error": {"code": "SESSION_NOT_FOUND", "message": "...", "details": {...}}}
```

Error codes are stable identifiers (review plan R4):
``SESSION_NOT_FOUND``, ``REPLAY_INVALID``, ``STORE_UNAVAILABLE``,
``VALIDATION_ERROR``, ``NOT_FOUND``, ``INTERNAL_ERROR``. Internal diagnostics
are logged redacted server-side; clients only ever receive safe messages.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: Maximum accepted task length for ``POST /api/runs``.
MAX_TASK_LENGTH = 2000
#: Maximum number of named tools per task run.
MAX_TOOLS_PER_RUN = 16


class RunRequest(BaseModel):
    """Options for one run; two mutually exclusive modes.

    - ``scenario`` (offline demo): canned deterministic runs on the scripted
      ``FakeModelProvider`` — unchanged MVP behavior, fully offline;
    - ``task`` (custom run): the provider configured on the server
      (``AGENTFLOW_PROVIDER=openai-compat``) executes the submitted task,
      optionally calling named tools from the server-declared tool registry
      (``AGENTFLOW_TOOLS_MODULE``).
    """

    scenario: Literal["simple", "compaction"] | None = None
    task: str | None = Field(default=None, max_length=MAX_TASK_LENGTH)
    tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS_PER_RUN)
    system_prompt: str | None = Field(default=None, max_length=MAX_TASK_LENGTH)
    max_steps: int | None = Field(default=None, ge=1, le=32)

    @model_validator(mode="after")
    def _check_mode(self) -> RunRequest:
        if self.scenario is not None and self.task is not None:
            raise ValueError("provide either scenario or task, not both")
        if self.scenario is None and self.task is None:
            raise ValueError("provide either scenario or task")
        if self.scenario is not None and (
            self.tools or self.system_prompt is not None or self.max_steps is not None
        ):
            raise ValueError("tools, system_prompt and max_steps require task mode (omit scenario)")
        return self


class RunResponse(BaseModel):
    """Outcome of one synchronous offline run."""

    session_id: str
    scenario: str
    status: str
    answer: str | None = None
    steps: int = 0
    event_count: int = 0


class SessionSummary(BaseModel):
    """One row of the session list."""

    session_id: str
    task: str = ""
    model: str = ""
    status: str = "unknown"
    event_count: int = 0
    created_at: str | None = None


class SessionDetail(SessionSummary):
    """Session metadata plus per-type event counts."""

    event_type_counts: dict[str, int] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    """Stable-code error description."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The only error shape the API returns."""

    error: ErrorBody


__all__ = [
    "MAX_TASK_LENGTH",
    "MAX_TOOLS_PER_RUN",
    "ErrorBody",
    "ErrorResponse",
    "RunRequest",
    "RunResponse",
    "SessionDetail",
    "SessionSummary",
]

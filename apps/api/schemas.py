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

from pydantic import BaseModel, Field

#: Maximum accepted task length for ``POST /api/runs`` (MVP bound).
MAX_TASK_LENGTH = 2000


class RunRequest(BaseModel):
    """Options for one offline run; both scenarios are fully deterministic.

    The run executes synchronously inside the request using the offline
    ``FakeModelProvider`` — no network, no API key, no background jobs.
    """

    scenario: Literal["simple", "compaction"] = "simple"
    task: str | None = Field(default=None, max_length=MAX_TASK_LENGTH)


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
    "ErrorBody",
    "ErrorResponse",
    "RunRequest",
    "RunResponse",
    "SessionDetail",
    "SessionSummary",
]

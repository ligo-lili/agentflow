"""Store Protocols: the only persistence surface consumers may depend on.

Implementations (in-memory now, SQLite in T2) must stay append-only for
events and must never surface SQLite details through these interfaces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from packages.core.events import AgentEvent
from packages.core.snapshots import ContextSnapshot, PromptSnapshot


class SessionRecord(BaseModel):
    """Minimal session metadata persisted at session start."""

    model_config = ConfigDict(frozen=True)

    session_id: str = Field(..., min_length=1)
    created_at: datetime
    task: str
    model: str
    status: str = "running"


@runtime_checkable
class EventStore(Protocol):
    """Append-only event persistence."""

    def append(self, event: AgentEvent) -> None:
        """Persist one event; duplicates must raise ``StoreError``."""
        ...

    def get_session_events(self, session_id: str) -> list[AgentEvent]:
        """Return the session's events sorted by ``sequence``."""
        ...


@runtime_checkable
class SnapshotStore(Protocol):
    """Persistence for prompt and context snapshots.

    Snapshots are immutable evidence: implementations must reject a duplicate
    snapshot id with ``StoreError`` instead of silently replacing the record.
    """

    def save_prompt_snapshot(self, snapshot: PromptSnapshot) -> None: ...

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None: ...

    def get_prompt_snapshots(self, session_id: str) -> list[PromptSnapshot]: ...

    def get_context_snapshots(self, session_id: str) -> list[ContextSnapshot]: ...


@runtime_checkable
class SessionStore(Protocol):
    """Persistence for session metadata."""

    def save_session(self, record: SessionRecord) -> None: ...

    def get_session(self, session_id: str) -> SessionRecord | None: ...

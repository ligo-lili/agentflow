"""In-memory reference stores.

These are the deterministic offline stores used by tests and demos. The
SQLite stores delivered by T2 implement the same Protocols from
``packages.core.stores`` and must keep events append-only.
"""

from __future__ import annotations

from packages.core.errors import StoreError
from packages.core.events import AgentEvent
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.stores import SessionRecord


class InMemoryEventStore:
    """Append-only event store keyed by session, read back in sequence order."""

    def __init__(self) -> None:
        self._events: list[AgentEvent] = []
        self._seen_ids: set[str] = set()

    def append(self, event: AgentEvent) -> None:
        if event.event_id in self._seen_ids:
            raise StoreError(f"duplicate event_id {event.event_id!r}")
        self._seen_ids.add(event.event_id)
        self._events.append(event)

    def get_session_events(self, session_id: str) -> list[AgentEvent]:
        return sorted(
            (e for e in self._events if e.session_id == session_id),
            key=lambda e: e.sequence,
        )


class InMemorySnapshotStore:
    """Snapshot persistence for prompt and context snapshots.

    Snapshots are immutable evidence: saving a snapshot id that already
    exists raises ``StoreError`` instead of replacing the recorded snapshot.
    """

    def __init__(self) -> None:
        self._prompt: list[PromptSnapshot] = []
        self._context: list[ContextSnapshot] = []
        self._prompt_ids: set[str] = set()
        self._context_ids: set[str] = set()

    def save_prompt_snapshot(self, snapshot: PromptSnapshot) -> None:
        if snapshot.snapshot_id in self._prompt_ids:
            raise StoreError(
                f"duplicate snapshot_id {snapshot.snapshot_id!r} in prompt_snapshots"
            )
        self._prompt_ids.add(snapshot.snapshot_id)
        self._prompt.append(snapshot)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        if snapshot.snapshot_id in self._context_ids:
            raise StoreError(
                f"duplicate snapshot_id {snapshot.snapshot_id!r} in context_snapshots"
            )
        self._context_ids.add(snapshot.snapshot_id)
        self._context.append(snapshot)

    def get_prompt_snapshots(self, session_id: str) -> list[PromptSnapshot]:
        return sorted(
            (s for s in self._prompt if s.session_id == session_id),
            key=lambda s: s.sequence,
        )

    def get_context_snapshots(self, session_id: str) -> list[ContextSnapshot]:
        return sorted(
            (s for s in self._context if s.session_id == session_id),
            key=lambda s: s.sequence,
        )


class InMemorySessionStore:
    """Session metadata store; saving the same session twice overwrites it."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def save_session(self, record: SessionRecord) -> None:
        self._sessions[record.session_id] = record

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

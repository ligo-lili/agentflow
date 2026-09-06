"""SQLite persistence: durable stores and the event-driven projection.

The stores implement the Protocols from ``packages.core.stores``. Storage is
append-only for events; payloads round-trip as JSON so every field survives a
restart. ``SqlitePersistence`` is a pure event consumer: it subscribes to the
EventBus and persists session records and events — it never touches the
Agent Loop (ADR-002).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from packages.core.errors import AgentFlowError, StoreError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.stores import SessionRecord
from packages.observability.eventbus import EventBus

DEFAULT_DB_PATH = Path(".agentflow") / "agentflow.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sequence   INTEGER NOT NULL,
    data       TEXT NOT NULL,
    UNIQUE (session_id, sequence)
);
CREATE TABLE IF NOT EXISTS prompt_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    task       TEXT NOT NULL,
    model      TEXT NOT NULL,
    status     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id, sequence);
"""


class SessionNotFoundError(AgentFlowError):
    """Raised when a session id is unknown to a store."""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # The API layer serves sync endpoints from a threadpool, so one
        # connection may be used from several threads; every access is
        # serialized through the owning store's lock.
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn
    except sqlite3.Error as exc:
        raise StoreError(f"cannot initialize SQLite database at {path}: {exc}") from exc


class SqliteEventStore:
    """Append-only event store; reads return events ordered by sequence."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._conn = _connect(db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def append(self, event: AgentEvent) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO events (event_id, session_id, sequence, data) VALUES (?, ?, ?, ?)",
                    (event.event_id, event.session_id, event.sequence, event.model_dump_json()),
                )
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"duplicate event_id {event.event_id!r}") from exc
        except sqlite3.Error as exc:
            raise StoreError(f"failed to append event {event.event_id!r}: {exc}") from exc

    def get_session_events(self, session_id: str) -> list[AgentEvent]:
        rows = self._rows("events", session_id)
        return [AgentEvent.model_validate_json(row[0]) for row in rows]

    def require_session_events(self, session_id: str) -> list[AgentEvent]:
        """Like ``get_session_events`` but raises for session ids without events."""
        events = self.get_session_events(session_id)
        if not events:
            raise SessionNotFoundError(f"no events recorded for session {session_id!r}")
        return events

    def session_ids(self) -> list[str]:
        """All session ids that have events, ordered by first event sequence."""
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT session_id FROM events GROUP BY session_id ORDER BY MIN(sequence)"
                ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to list session ids: {exc}") from exc
        return [str(row[0]) for row in rows]

    def _rows(self, table: str, session_id: str) -> list[tuple[str]]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT data FROM {table} WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to load rows from {table} for {session_id!r}: {exc}") from exc
        return rows


class SqliteSnapshotStore:
    """Persistence for prompt and context snapshots."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._conn = _connect(db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_prompt_snapshot(self, snapshot: PromptSnapshot) -> None:
        self._save("prompt_snapshots", snapshot)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self._save("context_snapshots", snapshot)

    def _save(self, table: str, snapshot: PromptSnapshot | ContextSnapshot) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {table} "
                    "(snapshot_id, session_id, sequence, data) VALUES (?, ?, ?, ?)",
                    (snapshot.snapshot_id, snapshot.session_id, snapshot.sequence,
                     snapshot.model_dump_json()),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to save snapshot {snapshot.snapshot_id!r}: {exc}") from exc

    def get_prompt_snapshots(self, session_id: str) -> list[PromptSnapshot]:
        return [PromptSnapshot.model_validate_json(r[0])
                for r in self._rows("prompt_snapshots", session_id)]

    def get_context_snapshots(self, session_id: str) -> list[ContextSnapshot]:
        return [ContextSnapshot.model_validate_json(r[0])
                for r in self._rows("context_snapshots", session_id)]

    def _rows(self, table: str, session_id: str) -> list[tuple[str]]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT data FROM {table} WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to load rows from {table} for {session_id!r}: {exc}") from exc
        return rows


class SqliteSessionStore:
    """Session metadata store; saving the same session twice overwrites it."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._conn = _connect(db_path)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_session(self, record: SessionRecord) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, created_at, task, model, status) VALUES (?, ?, ?, ?, ?)",
                    (
                        record.session_id,
                        record.created_at.isoformat(),
                        record.task,
                        record.model,
                        record.status,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to save session {record.session_id!r}: {exc}") from exc

    def get_session(self, session_id: str) -> SessionRecord | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT session_id, created_at, task, model, status FROM sessions "
                    "WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to load session {session_id!r}: {exc}") from exc
        if row is None:
            return None
        return SessionRecord(
            session_id=row[0],
            created_at=datetime.fromisoformat(row[1]),
            task=row[2],
            model=row[3],
            status=row[4],
        )

    def require_session(self, session_id: str) -> SessionRecord:
        """Like ``get_session`` but raises ``SessionNotFoundError`` when missing."""
        record = self.get_session(session_id)
        if record is None:
            raise SessionNotFoundError(f"session {session_id!r} not found")
        return record


class SqlitePersistence:
    """Event-bus consumer that keeps SQLite in sync with the runtime.

    Subscribing this object is the only wiring a durable session needs: the
    SessionStarted event materializes the session record, terminal events
    update its status, and every event is appended to the event store.
    """

    def __init__(self, bus: EventBus, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.bus = bus
        self.event_store = SqliteEventStore(db_path)
        self.session_store = SqliteSessionStore(db_path)
        bus.subscribe(self._on_event)

    def close(self) -> None:
        self.event_store.close()
        self.session_store.close()

    def _on_event(self, event: AgentEvent) -> None:
        if event.event_type == AgentEventType.SESSION_STARTED:
            self.session_store.save_session(
                SessionRecord(
                    session_id=event.session_id,
                    created_at=event.timestamp,
                    task=str(event.payload.get("task", "")),
                    model=str(event.payload.get("model", "")),
                    status="running",
                )
            )
        elif event.event_type in (AgentEventType.AGENT_FINISHED, AgentEventType.AGENT_FAILED):
            status = "finished" if event.event_type == AgentEventType.AGENT_FINISHED else "failed"
            record = self.session_store.get_session(event.session_id)
            if record is None:
                raise StoreError(f"cannot update status of unknown session {event.session_id!r}")
            self.session_store.save_session(
                SessionRecord(
                    session_id=record.session_id,
                    created_at=record.created_at,
                    task=record.task,
                    model=record.model,
                    status=status,
                )
            )
        self.event_store.append(event)

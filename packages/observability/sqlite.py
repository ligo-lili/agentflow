"""SQLite persistence: durable stores and the event-driven projection.

Layout and guarantees (docs/architecture/persistence.md):

- The schema is versioned in the ``schema_metadata`` table; every database is
  brought to :data:`SCHEMA_VERSION` by the single entry point
  :func:`apply_migrations` (legacy databases without metadata start at 0).
- Events are append-only. Duplicate event ids and duplicate snapshot ids are
  rejected with ``StoreError`` — recorded evidence is never silently replaced.
- ``SqlitePersistence`` writes the session projection and the event row in
  one transaction on one shared connection, so a terminal status can never
  exist without its terminal event. ``rebuild_projections`` deterministically
  re-derives the projection from the append-only event log.
- PRAGMAs: ``foreign_keys = ON`` (verified; no cross-table FKs are declared
  because stores are independently usable), ``journal_mode = WAL`` and
  ``busy_timeout`` for the read-heavy local API workload.
- Concurrency boundary: local single process. One connection is shared safely
  across threads via the owning lock; multi-process writers are out of scope.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from packages.core.errors import AgentFlowError, StoreError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.stores import SessionRecord
from packages.observability.eventbus import EventBus

DEFAULT_DB_PATH = Path(".agentflow") / "agentflow.db"

#: Current schema version; every database is migrated to it on connect.
SCHEMA_VERSION = 2

#: Versioned migration steps: version -> DDL. Step 1 is the original layout;
#: step 2 adds schema metadata and the denormalized ``events.event_type``
#: column (backfilled from the JSON payload on upgrade).
_MIGRATIONS: dict[int, str] = {
    1: """
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
    """,
    2: """
    CREATE TABLE IF NOT EXISTS schema_metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    ALTER TABLE events ADD COLUMN event_type TEXT NOT NULL DEFAULT '';
    """,
}


class SessionNotFoundError(AgentFlowError):
    """Raised when a session id is unknown to a store."""


def _statements(script: str) -> Iterator[str]:
    for statement in script.split(";"):
        if statement.strip():
            yield statement.strip()


def _schema_version(conn: sqlite3.Connection) -> int:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
    ).fetchone()
    if table is None:
        return 0
    row = conn.execute(
        "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _backfill_event_types(conn: sqlite3.Connection) -> None:
    """Populate ``events.event_type`` from the JSON payload on upgrade to v2."""
    rows = conn.execute("SELECT event_id, data FROM events").fetchall()
    for event_id, data in rows:
        try:
            payload = json.loads(str(data))
        except json.JSONDecodeError as exc:
            raise StoreError(f"cannot migrate event {event_id!r}: corrupt payload") from exc
        conn.execute(
            "UPDATE events SET event_type = ? WHERE event_id = ?",
            (str(payload.get("event_type", "")), event_id),
        )


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Bring ``conn``'s database to :data:`SCHEMA_VERSION`; return the version.

    Single migration entry point: all steps run in one immediate transaction,
    so a failed upgrade leaves the previous schema untouched. Legacy
    databases (tables without ``schema_metadata``) start at version 0.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _schema_version(conn)
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _statements(_MIGRATIONS[version]):
                    conn.execute(statement)
                if version == 2:
                    _backfill_event_types(conn)
            if current < SCHEMA_VERSION:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_metadata (key, value) "
                    "VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            conn.execute("COMMIT")
            return _schema_version(conn)
        except StoreError:
            conn.execute("ROLLBACK")
            raise
        except (sqlite3.Error, ValueError) as exc:
            conn.execute("ROLLBACK")
            raise StoreError(f"schema migration failed: {exc}") from exc
    except sqlite3.Error as exc:
        raise StoreError(f"schema migration failed: {exc}") from exc


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # The API layer serves sync endpoints from a threadpool, so one
        # connection may be used from several threads; every access is
        # serialized through the owning database's lock. Transactions are
        # managed explicitly (implicit transaction handling disabled).
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    except sqlite3.Error as exc:
        raise StoreError(f"cannot open SQLite database at {path}: {exc}") from exc
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        # WAL: the local API workload is read-heavy with short single-writer
        # transactions; readers never block the writer. synchronous=NORMAL is
        # the documented WAL pairing (durable across app crashes).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        apply_migrations(conn)
    except StoreError:
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise StoreError(f"cannot initialize SQLite database at {path}: {exc}") from exc
    return conn


class SqliteDatabase:
    """One configured SQLite connection, optionally shared by several stores.

    Stores create their own database by default; passing one shared instance
    lets several stores participate in the same transaction (the wiring used
    by :class:`SqlitePersistence`).
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._conn = _connect(db_path)
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def pragma(self, name: str) -> str | int | None:
        """Read one PRAGMA value (e.g. ``foreign_keys``, ``journal_mode``)."""
        with self._lock:
            row = self._ensure_open().execute(f"PRAGMA {name}").fetchone()
        return row[0] if row is not None else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction; rolls back on any error.

        The lock is held for the whole transaction, so code inside the
        ``with`` block must not call back into this database object.
        """
        with self._lock:
            conn = self._ensure_open()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._ensure_open().execute(sql, params).fetchall()

    def _ensure_open(self) -> sqlite3.Connection:
        if self._closed:
            raise StoreError("database connection is closed")
        return self._conn


class SqliteEventStore:
    """Append-only event store; reads return events ordered by sequence."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        database: SqliteDatabase | None = None,
    ) -> None:
        self._db = database if database is not None else SqliteDatabase(db_path)

    def close(self) -> None:
        self._db.close()

    def append(self, event: AgentEvent) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO events (event_id, session_id, sequence, event_type, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.session_id,
                        event.sequence,
                        event.event_type.value,
                        event.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"duplicate event_id {event.event_id!r}") from exc
        except sqlite3.Error as exc:
            raise StoreError(f"failed to append event {event.event_id!r}: {exc}") from exc

    def get_session_events(self, session_id: str) -> list[AgentEvent]:
        rows = self._rows(session_id)
        return [AgentEvent.model_validate_json(row[0]) for row in rows]

    def require_session_events(self, session_id: str) -> list[AgentEvent]:
        """Like ``get_session_events`` but raises for session ids without events."""
        events = self.get_session_events(session_id)
        if not events:
            raise SessionNotFoundError(f"no events recorded for session {session_id!r}")
        return events

    def session_ids(self) -> list[str]:
        """All session ids that have events, ordered by first event sequence."""
        rows = self._db.fetchall(
            "SELECT session_id FROM events GROUP BY session_id ORDER BY MIN(sequence)"
        )
        return [str(row[0]) for row in rows]

    def event_type_counts(self, session_id: str) -> dict[str, int]:
        """Per-type event counts read from the denormalized column."""
        rows = self._db.fetchall(
            "SELECT event_type, COUNT(*) FROM events WHERE session_id = ? GROUP BY event_type",
            (session_id,),
        )
        return {str(row[0]): int(row[1]) for row in rows}

    def _rows(self, session_id: str) -> list[tuple[Any, ...]]:
        return self._db.fetchall(
            "SELECT data FROM events WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )


class SqliteSnapshotStore:
    """Persistence for prompt and context snapshots.

    Snapshots are immutable evidence: saving a snapshot id that already
    exists raises ``StoreError`` instead of replacing the recorded row.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        database: SqliteDatabase | None = None,
    ) -> None:
        self._db = database if database is not None else SqliteDatabase(db_path)

    def close(self) -> None:
        self._db.close()

    def save_prompt_snapshot(self, snapshot: PromptSnapshot) -> None:
        self._save("prompt_snapshots", snapshot)

    def save_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        self._save("context_snapshots", snapshot)

    def _save(self, table: str, snapshot: PromptSnapshot | ContextSnapshot) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    f"INSERT INTO {table} "
                    "(snapshot_id, session_id, sequence, data) VALUES (?, ?, ?, ?)",
                    (
                        snapshot.snapshot_id,
                        snapshot.session_id,
                        snapshot.sequence,
                        snapshot.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError(
                f"duplicate snapshot_id {snapshot.snapshot_id!r} in {table}"
            ) from exc
        except sqlite3.Error as exc:
            raise StoreError(f"failed to save snapshot {snapshot.snapshot_id!r}: {exc}") from exc

    def get_prompt_snapshots(self, session_id: str) -> list[PromptSnapshot]:
        return [
            PromptSnapshot.model_validate_json(r[0])
            for r in self._rows("prompt_snapshots", session_id)
        ]

    def get_context_snapshots(self, session_id: str) -> list[ContextSnapshot]:
        return [
            ContextSnapshot.model_validate_json(r[0])
            for r in self._rows("context_snapshots", session_id)
        ]

    def _rows(self, table: str, session_id: str) -> list[tuple[Any, ...]]:
        return self._db.fetchall(
            f"SELECT data FROM {table} WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )


class SqliteSessionStore:
    """Session metadata store; saving the same session twice overwrites it.

    Unlike events and snapshots, session rows are a projection of the event
    log and may legitimately be updated (status changes) or rebuilt.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        database: SqliteDatabase | None = None,
    ) -> None:
        self._db = database if database is not None else SqliteDatabase(db_path)

    def close(self) -> None:
        self._db.close()

    def save_session(self, record: SessionRecord) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO sessions "
                    "(session_id, created_at, task, model, status) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET created_at = excluded.created_at, "
                    "task = excluded.task, model = excluded.model, status = excluded.status",
                    (
                        record.session_id,
                        record.created_at.isoformat(),
                        record.task,
                        record.model,
                        record.status,
                    ),
                )
        except sqlite3.Error as exc:
            raise StoreError(f"failed to save session {record.session_id!r}: {exc}") from exc

    def get_session(self, session_id: str) -> SessionRecord | None:
        rows = self._db.fetchall(
            "SELECT session_id, created_at, task, model, status FROM sessions "
            "WHERE session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return SessionRecord(
            session_id=row[0],
            created_at=datetime.fromisoformat(str(row[1])),
            task=str(row[2]),
            model=str(row[3]),
            status=str(row[4]),
        )

    def require_session(self, session_id: str) -> SessionRecord:
        """Like ``get_session`` but raises ``SessionNotFoundError`` when missing."""
        record = self.get_session(session_id)
        if record is None:
            raise SessionNotFoundError(f"session {session_id!r} not found")
        return record


class SqlitePersistence:
    """Event-bus consumer that keeps SQLite in sync with the runtime.

    Subscribing this object is the only wiring a durable session needs. The
    session projection and the event row are written in one transaction on
    one shared connection: a write failure rolls both back, so a terminal
    session status can never exist without its terminal event.
    """

    def __init__(self, bus: EventBus, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.bus = bus
        self._db = SqliteDatabase(db_path)
        self.event_store = SqliteEventStore(database=self._db)
        self.session_store = SqliteSessionStore(database=self._db)
        bus.subscribe(self._on_event)

    def close(self) -> None:
        self._db.close()

    def rebuild_projections(self) -> int:
        """Re-derive every session row from the event log; return the count.

        Deterministic recovery path for out-of-band corruption: the created
        time, task and model come from ``SessionStarted`` (or the first
        event), and the status from the last terminal event (``running`` when
        none exists). The rebuild is itself atomic.
        """
        rebuilt = 0
        try:
            with self._db.transaction() as conn:
                rows = conn.execute(
                    "SELECT session_id, data FROM events ORDER BY session_id, sequence"
                ).fetchall()
                projections: dict[str, SessionRecord] = {}
                for row_session_id, data in rows:
                    event = AgentEvent.model_validate_json(str(data))
                    session_id = str(row_session_id)
                    record = projections.get(session_id) or SessionRecord(
                        session_id=session_id,
                        created_at=event.timestamp,
                        task="",
                        model="",
                        status="running",
                    )
                    if event.event_type == AgentEventType.SESSION_STARTED:
                        record = record.model_copy(
                            update={
                                "created_at": event.timestamp,
                                "task": str(event.payload.get("task", "")),
                                "model": str(event.payload.get("model", "")),
                            }
                        )
                    elif event.event_type == AgentEventType.AGENT_FINISHED:
                        record = record.model_copy(update={"status": "finished"})
                    elif event.event_type == AgentEventType.AGENT_FAILED:
                        record = record.model_copy(update={"status": "failed"})
                    projections[session_id] = record
                for record in projections.values():
                    conn.execute(
                        "INSERT INTO sessions "
                        "(session_id, created_at, task, model, status) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(session_id) DO UPDATE SET created_at = excluded.created_at, "
                        "task = excluded.task, model = excluded.model, status = excluded.status",
                        (
                            record.session_id,
                            record.created_at.isoformat(),
                            record.task,
                            record.model,
                            record.status,
                        ),
                    )
                rebuilt = len(projections)
        except sqlite3.Error as exc:
            raise StoreError(f"failed to rebuild session projections: {exc}") from exc
        return rebuilt

    def _on_event(self, event: AgentEvent) -> None:
        try:
            with self._db.transaction() as conn:
                if event.event_type == AgentEventType.SESSION_STARTED:
                    conn.execute(
                        "INSERT INTO sessions "
                        "(session_id, created_at, task, model, status) VALUES (?, ?, ?, ?, "
                        "'running') ON CONFLICT(session_id) DO UPDATE SET task = excluded.task, "
                        "model = excluded.model, status = 'running'",
                        (
                            event.session_id,
                            event.timestamp.isoformat(),
                            str(event.payload.get("task", "")),
                            str(event.payload.get("model", "")),
                        ),
                    )
                elif event.event_type in (AgentEventType.AGENT_FINISHED, AgentEventType.AGENT_FAILED):
                    status = (
                        "finished"
                        if event.event_type == AgentEventType.AGENT_FINISHED
                        else "failed"
                    )
                    updated = conn.execute(
                        "UPDATE sessions SET status = ? WHERE session_id = ?",
                        (status, event.session_id),
                    ).rowcount
                    if updated == 0:
                        raise StoreError(
                            f"cannot update status of unknown session {event.session_id!r}"
                        )
                conn.execute(
                    "INSERT INTO events (event_id, session_id, sequence, event_type, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.session_id,
                        event.sequence,
                        event.event_type.value,
                        event.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"duplicate event_id {event.event_id!r}") from exc
        except sqlite3.Error as exc:
            raise StoreError(f"failed to persist event {event.event_id!r}: {exc}") from exc

"""SQLite integrity hardening: migrations, atomic projection, immutability.

Review plan R2: schema metadata + single migration entry point, upgrade from
the pre-metadata layout, injected write failure atomicity, projection
rebuild, duplicate snapshot rejection and PRAGMA verification.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.errors import StoreError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.provider import ModelMessage, ModelResponse
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemorySnapshotStore
from packages.observability.sqlite import (
    SCHEMA_VERSION,
    SqliteDatabase,
    SqliteEventStore,
    SqlitePersistence,
    SqliteSnapshotStore,
    apply_migrations,
)
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession
from tests.helpers import FIXED_TIME, make_event

#: The original (pre-metadata) layout, exactly as shipped before review R2.
_LEGACY_SCHEMA = """
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


def final_response(content: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=content),
        finish_reason="stop",
    )


def make_session(db_path: Path, session_id: str, bus: EventBus) -> AgentSession:
    return AgentSession(
        task="Count the words of the report.",
        provider=FakeModelProvider([final_response("The report has 9 words.")]),
        config=AgentLoopConfig(model="fake-model"),
        bus=bus,
        session_id=session_id,
    )


def read_schema_version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "schema_metadata row missing"
    return int(row[0])


def test_fresh_database_is_stamped_with_current_schema_version(
    tmp_path: Path,
) -> None:
    db = tmp_path / "agentflow.db"
    store = SqliteEventStore(db)
    store.close()
    assert read_schema_version(db) == SCHEMA_VERSION


def test_upgrade_from_legacy_schema_preserves_events_and_backfills(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.db"
    legacy_data = make_event(event_type=AgentEventType.LLM_CALL_STARTED, sequence=0).model_dump_json()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO events (event_id, session_id, sequence, data) VALUES (?, ?, ?, ?)",
            ("ev-0000", "s-legacy", 0, legacy_data),
        )
        conn.commit()
    finally:
        conn.close()

    store = SqliteEventStore(db)
    try:
        events = store.get_session_events("s-legacy")
        assert len(events) == 1
        assert events[0].event_type == AgentEventType.LLM_CALL_STARTED
        assert store.event_type_counts("s-legacy") == {"LLMCallStarted": 1}
        assert read_schema_version(db) == SCHEMA_VERSION
    finally:
        store.close()

    # Reopening a migrated database is a no-op migration and keeps working.
    again = SqliteEventStore(db)
    try:
        assert len(again.get_session_events("s-legacy")) == 1
    finally:
        again.close()


def test_foreign_keys_and_wal_are_enabled(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "agentflow.db")
    try:
        assert db.pragma("foreign_keys") == 1
        assert str(db.pragma("journal_mode")) == "wal"
    finally:
        db.close()


def test_duplicate_snapshot_ids_are_rejected_not_replaced(tmp_path: Path) -> None:
    from packages.core.snapshots import PromptSection, PromptSnapshot

    db = tmp_path / "agentflow.db"
    store = SqliteSnapshotStore(db)
    snapshot = PromptSnapshot(
        snapshot_id="ps-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=0,
        created_at=FIXED_TIME,
        sections=(PromptSection.BASE_SYSTEM,),
        section_content={PromptSection.BASE_SYSTEM: "You are AgentFlow."},
        estimator="deterministic-fallback",
        token_counts={"base_system": 4},
        total_tokens=4,
    )
    store.save_prompt_snapshot(snapshot)
    with pytest.raises(StoreError, match="duplicate snapshot_id"):
        store.save_prompt_snapshot(snapshot)
    assert len(store.get_prompt_snapshots("s-1")) == 1  # original evidence intact
    store.close()


def test_duplicate_snapshot_ids_are_rejected_in_memory() -> None:
    from packages.core.snapshots import PromptSection, PromptSnapshot

    store = InMemorySnapshotStore()
    snapshot = PromptSnapshot(
        snapshot_id="ps-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=0,
        created_at=FIXED_TIME,
        sections=(PromptSection.BASE_SYSTEM,),
        section_content={PromptSection.BASE_SYSTEM: "You are AgentFlow."},
        estimator="deterministic-fallback",
        token_counts={"base_system": 4},
        total_tokens=4,
    )
    store.save_prompt_snapshot(snapshot)
    with pytest.raises(StoreError, match="duplicate snapshot_id"):
        store.save_prompt_snapshot(snapshot)
    assert len(store.get_prompt_snapshots("s-1")) == 1


def test_injected_write_failure_cannot_leave_terminal_session_without_event(
    tmp_path: Path,
) -> None:
    """The projection update and the event insert share one transaction: a
    failing event insert must roll the status update back."""
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    persistence = SqlitePersistence(bus, db)
    try:
        bus.emit(make_event(AgentEventType.SESSION_STARTED, sequence=0, session_id="s-atomic"))
        # Out-of-band row that will collide with the terminal event's insert.
        collision = make_event(AgentEventType.LLM_CALL_STARTED, sequence=1, session_id="s-atomic")
        persistence.event_store.append(collision)

        conflicting = AgentEvent(
            event_id=collision.event_id,  # duplicate id -> IntegrityError
            session_id="s-atomic",
            trace_id="tr-1",
            sequence=1,
            timestamp=FIXED_TIME,
            event_type=AgentEventType.AGENT_FAILED,
            payload={"phase": "provider", "error": "boom"},
        )
        with pytest.raises(StoreError, match="duplicate event_id"):
            bus.emit(conflicting)

        record = persistence.session_store.require_session("s-atomic")
        assert record.status == "running"  # not "failed" without its event
        events = persistence.event_store.get_session_events("s-atomic")
        assert [e.sequence for e in events] == [0, 1]
        assert events[-1].event_type == AgentEventType.LLM_CALL_STARTED
    finally:
        persistence.close()


def test_rebuild_projections_restores_corrupted_session_rows(tmp_path: Path) -> None:
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    persistence = SqlitePersistence(bus, db)
    session = make_session(db, "s-rebuild", bus)
    result = session.run()
    persistence.close()

    assert result.status == "finished"
    # Out-of-band corruption: the projection row loses the terminal status.
    persistence = SqlitePersistence(EventBus(), db)
    try:
        persistence.session_store.save_session(
            persistence.session_store.require_session("s-rebuild").model_copy(
                update={"status": "running", "task": "corrupted", "model": "gone"}
            )
        )
        assert persistence.rebuild_projections() >= 1

        record = persistence.session_store.require_session("s-rebuild")
        assert record.status == "finished"
        assert record.task == "Count the words of the report."
        assert record.model == "fake-model"
    finally:
        persistence.close()


def test_rebuild_projections_recreates_missing_rows(tmp_path: Path) -> None:
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    persistence = SqlitePersistence(bus, db)
    make_session(db, "s-missing", bus).run()
    # Drop the projection row entirely (out-of-band deletion).
    raw = sqlite3.connect(db)
    try:
        raw.execute("DELETE FROM sessions WHERE session_id = 's-missing'")
        raw.commit()
    finally:
        raw.close()
    try:
        assert persistence.session_store.get_session("s-missing") is None
        persistence.rebuild_projections()
        record = persistence.session_store.require_session("s-missing")
        assert record.status == "finished"
    finally:
        persistence.close()


def test_integrity_check_passes_after_a_full_session(tmp_path: Path) -> None:
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    persistence = SqlitePersistence(bus, db)
    snapshots = SqliteSnapshotStore(db)
    session = make_session(db, "s-check", bus)
    session.run()
    persistence.close()
    snapshots.close()

    conn = sqlite3.connect(db)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchall()
        assert result == [("ok",)]
    finally:
        conn.close()


def test_apply_migrations_is_the_single_entry_point(tmp_path: Path) -> None:
    db = tmp_path / "direct.db"
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        assert apply_migrations(conn) == SCHEMA_VERSION
        assert apply_migrations(conn) == SCHEMA_VERSION  # idempotent
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"events", "prompt_snapshots", "context_snapshots", "sessions",
                "schema_metadata"} <= tables
    finally:
        conn.close()

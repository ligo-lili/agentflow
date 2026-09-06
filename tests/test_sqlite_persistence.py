"""SQLite persistence: round-trip after restart, ordering, typed not-found."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.core.errors import AgentFlowError, StoreError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.provider import ModelMessage, ModelResponse
from packages.core.snapshots import ContextSnapshot, PromptSection, PromptSnapshot
from packages.core.stores import (
    EventStore,
    SessionRecord,
    SessionStore,
    SnapshotStore,
)
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemoryEventStore
from packages.observability.sqlite import (
    SessionNotFoundError,
    SqliteEventStore,
    SqlitePersistence,
    SqliteSessionStore,
    SqliteSnapshotStore,
)
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def make_event(sequence: int, session_id: str = "s-1") -> AgentEvent:
    return AgentEvent(
        event_id=f"ev-{sequence:04d}",
        session_id=session_id,
        trace_id="tr-1",
        sequence=sequence,
        timestamp=FIXED_TIME,
        event_type=AgentEventType.LLM_CALL_STARTED,
        payload={"step": sequence, "nested": {"items": [1, "two", None]}},
    )


def make_record(session_id: str = "s-1", status: str = "running") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        created_at=FIXED_TIME,
        task="demo task",
        model="fake-model",
        status=status,
    )


def test_sqlite_stores_satisfy_the_t0_protocols(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "agentflow.db"
    event_store: EventStore = SqliteEventStore(db)
    snapshot_store: SnapshotStore = SqliteSnapshotStore(db)
    session_store: SessionStore = SqliteSessionStore(db)
    assert isinstance(event_store, EventStore)
    assert isinstance(snapshot_store, SnapshotStore)
    assert isinstance(session_store, SessionStore)
    event_store.close()
    snapshot_store.close()
    session_store.close()


def test_database_file_is_created_at_the_given_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "subdir" / "agentflow.db"
    store = SqliteEventStore(db)
    store.close()
    assert db.exists()


def test_events_roundtrip_after_reopen_with_payload_fidelity(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db = tmp_path / "agentflow.db"
    original_events = [make_event(0), make_event(1), make_event(2)]

    writer = SqliteEventStore(db)
    for event in original_events:
        writer.append(event)
    writer.close()  # restart boundary

    reader = SqliteEventStore(db)
    reloaded = reader.get_session_events("s-1")
    reader.close()
    assert reloaded == original_events
    assert reloaded[0].payload["nested"] == {"items": [1, "two", None]}


def test_event_order_is_preserved_by_sequence_not_insertion_order(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db = tmp_path / "agentflow.db"
    store = SqliteEventStore(db)
    for sequence in (3, 0, 2, 1):
        store.append(make_event(sequence))
    reloaded = store.get_session_events("s-1")
    store.close()
    assert [e.sequence for e in reloaded] == [0, 1, 2, 3]


def test_duplicate_event_id_is_rejected_as_store_error(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    store = SqliteEventStore(tmp_path / "agentflow.db")
    store.append(make_event(0))
    with pytest.raises(StoreError, match="duplicate event_id"):
        store.append(make_event(0))
    store.close()


def test_session_roundtrip_and_status_update_across_restart(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db = tmp_path / "agentflow.db"
    writer = SqliteSessionStore(db)
    writer.save_session(make_record())
    writer.save_session(make_record(status="finished"))  # overwrite by id
    writer.close()

    reader = SqliteSessionStore(db)
    record = reader.get_session("s-1")
    assert record == make_record(status="finished")
    assert record is not None and record.created_at == FIXED_TIME
    reader.close()


def test_missing_session_raises_typed_not_found_error(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db = tmp_path / "agentflow.db"
    session_store = SqliteSessionStore(db)
    event_store = SqliteEventStore(db)
    try:
        with pytest.raises(SessionNotFoundError) as exc_info:
            session_store.require_session("missing")
        assert isinstance(exc_info.value, AgentFlowError)

        with pytest.raises(SessionNotFoundError):
            event_store.require_session_events("missing")

        assert session_store.get_session("missing") is None
        assert event_store.get_session_events("missing") == []
    finally:
        session_store.close()
        event_store.close()


def test_snapshots_roundtrip_after_reopen(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "agentflow.db"
    prompt = PromptSnapshot(
        snapshot_id="ps-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=1,
        created_at=FIXED_TIME,
        sections=(PromptSection.BASE_SYSTEM, PromptSection.CURRENT_TASK),
        section_content={
            PromptSection.BASE_SYSTEM: "You are AgentFlow.",
            PromptSection.CURRENT_TASK: "Summarize a file.",
        },
        estimator="deterministic-fallback",
        token_counts={"base_system": 4, "current_task": 4},
        total_tokens=8,
    )
    context = ContextSnapshot(
        snapshot_id="cs-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=2,
        created_at=FIXED_TIME,
        messages=(ModelMessage(role="user", content="Summarize a file."),),
        component_tokens={"recent_messages": 6},
        total_tokens=6,
        reserved_output_tokens=256,
        budget_limit=4096,
        estimator="deterministic-fallback",
    )

    writer = SqliteSnapshotStore(db)
    writer.save_prompt_snapshot(prompt)
    writer.save_context_snapshot(context)
    writer.close()

    reader = SqliteSnapshotStore(db)
    assert reader.get_prompt_snapshots("s-1") == [prompt]
    assert reader.get_context_snapshots("s-1") == [context]
    reader.close()


def test_sqlite_persistence_projects_sessions_and_events_from_the_bus(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    in_memory = InMemoryEventStore()
    bus.subscribe(in_memory.append)
    persistence = SqlitePersistence(bus, db)

    session = AgentSession(
        task="demo task",
        provider=FakeModelProvider(
            [
                ModelResponse(
                    message=ModelMessage(role="assistant", content="done"),
                    finish_reason="stop",
                )
            ]
        ),
        config=AgentLoopConfig(model="fake-model"),
        bus=bus,
        session_id="projected-session",
    )
    result = session.run()
    persistence.close()  # restart boundary

    assert result.status == "finished"

    event_store = SqliteEventStore(db)
    session_store = SqliteSessionStore(db)
    try:
        events = event_store.get_session_events("projected-session")
        assert [e.sequence for e in events] == list(range(4))
        assert [e.event_type for e in events] == [
            AgentEventType.SESSION_STARTED,
            AgentEventType.LLM_CALL_STARTED,
            AgentEventType.LLM_CALL_FINISHED,
            AgentEventType.AGENT_FINISHED,
        ]
        assert in_memory.get_session_events("projected-session") == events
        record = session_store.require_session("projected-session")
        assert record.status == "finished"
        assert record.task == "demo task"
        assert record.model == "fake-model"
    finally:
        event_store.close()
        session_store.close()


def test_persistence_marks_failed_sessions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "agentflow.db"
    bus = EventBus()
    persistence = SqlitePersistence(bus, db)
    session = AgentSession(
        task="doomed task",
        provider=FakeModelProvider([RuntimeError("provider offline")]),
        bus=bus,
        session_id="failed-session",
    )
    session.run()
    persistence.close()

    session_store = SqliteSessionStore(db)
    try:
        assert session_store.require_session("failed-session").status == "failed"
    finally:
        session_store.close()

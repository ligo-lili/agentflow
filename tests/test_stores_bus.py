"""Store and event bus contracts: append-only, sequence-sorted, fan-out."""

from __future__ import annotations

import pytest

from packages.core.errors import EventOrderError, StoreError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.stores import EventStore, SessionRecord, SessionStore, SnapshotStore
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import (
    InMemoryEventStore,
    InMemorySessionStore,
    InMemorySnapshotStore,
)
from tests.helpers import FIXED_TIME, make_event


def test_in_memory_event_store_satisfies_protocol_and_sorts() -> None:
    store: EventStore = InMemoryEventStore()
    for seq in (2, 0, 1):
        store.append(make_event(sequence=seq))
    events = store.get_session_events("s-1")
    assert [e.sequence for e in events] == [0, 1, 2]


def test_event_store_rejects_duplicate_event_ids() -> None:
    store = InMemoryEventStore()
    store.append(make_event(sequence=0))
    with pytest.raises(StoreError):
        store.append(make_event(sequence=0))


def test_snapshot_store_satisfies_protocol_and_filters_by_session() -> None:
    store: SnapshotStore = InMemorySnapshotStore()
    prompt = PromptStubBuilder.prompt(session_id="s-1")
    other = PromptStubBuilder.prompt(session_id="s-2")
    store.save_prompt_snapshot(prompt)
    store.save_prompt_snapshot(other)
    assert store.get_prompt_snapshots("s-1") == [prompt]


def test_session_store_roundtrip() -> None:
    store: SessionStore = InMemorySessionStore()
    record = SessionRecord(
        session_id="s-1", created_at=FIXED_TIME, task="demo", model="fake-model"
    )
    assert store.get_session("s-1") is None
    store.save_session(record)
    assert store.get_session("s-1") == record


def test_event_bus_fans_out_to_subscribers_in_order() -> None:
    bus = EventBus()
    store = InMemoryEventStore()
    received: list[AgentEvent] = []
    bus.subscribe(received.append)
    bus.subscribe(store.append)
    for event_type, seq in (
        (AgentEventType.SESSION_STARTED, 0),
        (AgentEventType.LLM_CALL_STARTED, 1),
        (AgentEventType.AGENT_FINISHED, 2),
    ):
        bus.emit(make_event(event_type=event_type, sequence=seq))
    assert [e.sequence for e in received] == [0, 1, 2]
    assert [e.event_type for e in store.get_session_events("s-1")] == [
        AgentEventType.SESSION_STARTED,
        AgentEventType.LLM_CALL_STARTED,
        AgentEventType.AGENT_FINISHED,
    ]


def test_event_bus_rejects_sequence_gaps() -> None:
    bus = EventBus()
    bus.emit(make_event(sequence=0))
    with pytest.raises(EventOrderError):
        bus.emit(make_event(sequence=2))


def test_event_bus_tracks_sessions_independently() -> None:
    bus = EventBus()
    bus.emit(make_event(sequence=0, session_id="s-1"))
    bus.emit(make_event(sequence=0, session_id="s-2"))
    bus.emit(make_event(sequence=1, session_id="s-1"))


class PromptStubBuilder:
    """Local factory so this module does not import the snapshot test module."""

    @staticmethod
    def prompt(session_id: str):
        from packages.core.snapshots import PromptSection, PromptSnapshot

        return PromptSnapshot(
            snapshot_id=f"ps-{session_id}",
            session_id=session_id,
            trace_id="tr-1",
            sequence=0,
            created_at=FIXED_TIME,
            sections=(PromptSection.BASE_SYSTEM,),
            section_content={PromptSection.BASE_SYSTEM: "hi"},
            estimator="deterministic-fallback",
            token_counts={"base_system": 1},
            total_tokens=1,
        )

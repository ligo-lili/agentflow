"""Replay integrity: valid, running, truncated and corrupted traces are
formally distinguished (review plan R3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.events import AgentEvent, AgentEventType
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.inmemory import InMemoryEventStore, InMemorySnapshotStore
from packages.observability.replay import ReplayError, SessionReplayer
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession
from tests.helpers import FIXED_TIME

ESTIMATOR = DeterministicEstimator()


class LongTool:
    name = "long_tool"
    description = "Returns a long observation."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value="x" * 1500)


def finished_session(session_id: str) -> AgentSession:
    """Minimal finished session: one model call, no tools."""
    return AgentSession(
        task="t",
        provider=FakeModelProvider(
            [
                ModelResponse(
                    message=ModelMessage(role="assistant", content="done"),
                    finish_reason="stop",
                )
            ]
        ),
        session_id=session_id,
    )


def compacting_session(session_id: str) -> AgentSession:
    """Finished session whose long tool output triggers one compaction."""
    budget = TokenBudgetManager(
        BudgetConfig(max_context_tokens=500, reserved_output_tokens=50)
    )
    snapshot_store = InMemorySnapshotStore()
    return AgentSession(
        task="Digest the long report.",
        provider=FakeModelProvider(
            [
                ModelResponse(
                    message=ModelMessage(role="assistant", content=""),
                    finish_reason="tool_calls",
                    tool_calls=(ToolCallRequest(call_id="c1", name="long_tool", arguments={}),),
                ),
                ModelResponse(
                    message=ModelMessage(role="assistant", content="Report digested."),
                    finish_reason="stop",
                ),
            ]
        ),
        tools=[LongTool()],
        config=AgentLoopConfig(max_steps=4, system_prompt="You are AgentFlow."),
        prompt_builder=PromptBuilder(ESTIMATOR, snapshot_store),
        context_manager=ContextManager(
            ESTIMATOR,
            budget,
            snapshot_store,
            compaction_engine=CompactionEngine(
                ESTIMATOR, budget, CompactionConfig(strategy="semantic_state")
            ),
        ),
        session_id=session_id,
    )


def without_events(events: list[AgentEvent], *dropped: AgentEvent) -> InMemoryEventStore:
    removed = {e.event_id for e in dropped}
    store = InMemoryEventStore()
    for event in events:
        if event.event_id not in removed:
            store.append(event)
    return store


def load_into_error(store: InMemoryEventStore, session_id: str) -> ReplayError:
    with pytest.raises(ReplayError) as exc_info:
        SessionReplayer(store).load(session_id)
    return exc_info.value


def error_codes(error: ReplayError) -> list[str]:
    return [issue.code for issue in error.integrity.errors]


def test_valid_trace_reports_integrity_ok_with_range() -> None:
    session = finished_session("ri-ok")
    session.run()
    replay = SessionReplayer(session.store).load("ri-ok")
    integrity = replay.integrity
    assert integrity.valid is True
    assert integrity.errors == ()
    assert integrity.event_range == (0, len(session.events()) - 1)
    assert integrity.schema_versions == ("1.0",)


def test_sequence_gap_is_an_error() -> None:
    session = finished_session("ri-gap")
    session.run()
    events = session.events()
    moved = events[2].model_copy(update={"sequence": 9})
    store = InMemoryEventStore()
    for event in sorted([e for e in events if e.event_id != moved.event_id] + [moved],
                        key=lambda e: e.sequence):
        store.append(event)

    error = load_into_error(store, "ri-gap")
    assert "SEQUENCE_GAP" in error_codes(error)
    assert "sequence gap" in str(error)


def test_duplicate_sequence_is_an_error() -> None:
    session = finished_session("ri-dup")
    session.run()
    events = session.events()
    forged = events[0].model_copy(update={"event_id": "forged", "sequence": events[-1].sequence})
    store = InMemoryEventStore()
    for event in [*events, forged]:
        store.append(event)

    error = load_into_error(store, "ri-dup")
    assert "DUPLICATE_SEQUENCE" in error_codes(error)


def test_mixed_trace_ids_are_an_error() -> None:
    session = finished_session("ri-trace")
    session.run()
    events = session.events()
    foreign = events[1].model_copy(update={"trace_id": "tr-other"})
    store = InMemoryEventStore()
    for event in [e if e.event_id != foreign.event_id else foreign for e in events]:
        store.append(event)

    error = load_into_error(store, "ri-trace")
    assert "MIXED_TRACE_IDS" in error_codes(error)
    issue = next(i for i in error.integrity.errors if i.code == "MIXED_TRACE_IDS")
    assert issue.sequence == foreign.sequence


def test_unsupported_schema_version_is_an_error() -> None:
    session = finished_session("ri-schema")
    session.run()
    events = session.events()
    future = events[1].model_copy(update={"schema_version": "9.9"})
    store = InMemoryEventStore()
    for event in [e if e.event_id != future.event_id else future for e in events]:
        store.append(event)

    error = load_into_error(store, "ri-schema")
    assert "UNSUPPORTED_SCHEMA_VERSION" in error_codes(error)


def test_missing_tool_finish_is_an_error_when_session_terminated() -> None:
    session = compacting_session("ri-tool")
    session.run()
    events = session.events()
    tool_finish = next(e for e in events if e.event_type == AgentEventType.TOOL_CALL_FINISHED)
    truncated = without_events(events, tool_finish)

    error = load_into_error(truncated, "ri-tool")
    assert "UNMATCHED_TOOL_CALL" in error_codes(error)


def test_missing_compaction_finish_is_an_error_when_session_terminated() -> None:
    session = compacting_session("ri-comp")
    session.run()
    events = session.events()
    compaction_finish = next(
        e for e in events if e.event_type == AgentEventType.CONTEXT_COMPACTION_FINISHED
    )
    truncated = without_events(events, compaction_finish)

    error = load_into_error(truncated, "ri-comp")
    assert "UNMATCHED_COMPACTION" in error_codes(error)


def test_conflicting_terminal_events_are_an_error() -> None:
    session = finished_session("ri-term")
    session.run()
    events = session.events()
    forged_failure = AgentEvent(
        event_id="forged-failure",
        session_id="ri-term",
        trace_id=events[0].trace_id,
        sequence=events[-1].sequence,
        timestamp=FIXED_TIME,
        event_type=AgentEventType.AGENT_FAILED,
        payload={"phase": "provider", "error": "conflict"},
    )
    store = InMemoryEventStore()
    for event in [*events, forged_failure]:
        store.append(event)

    error = load_into_error(store, "ri-term")
    assert "CONFLICTING_TERMINAL_EVENTS" in error_codes(error)


def test_running_session_without_terminal_event_is_valid_with_warning() -> None:
    session = finished_session("ri-running")
    session.run()
    events = session.events()
    truncated = without_events(events, events[-1])

    replay = SessionReplayer(truncated).load("ri-running")
    assert replay.integrity.valid is True
    assert replay.status == "running"
    assert "NO_TERMINAL_EVENT" in [issue.code for issue in replay.integrity.warnings]


def test_in_flight_session_mid_tool_call_is_valid_with_warning() -> None:
    session = compacting_session("ri-inflight")
    session.run()
    events = session.events()
    # A live session that is currently executing a tool: the log ends right
    # after TOOL_CALL_STARTED (tail truncation, no hole in the middle).
    tool_start = next(e for e in events if e.event_type == AgentEventType.TOOL_CALL_STARTED)
    truncated = InMemoryEventStore()
    for event in [e for e in events if e.sequence <= tool_start.sequence]:
        truncated.append(event)

    replay = SessionReplayer(truncated).load("ri-inflight")
    assert replay.integrity.valid is True
    assert replay.status == "running"
    assert any(
        issue.code == "UNMATCHED_TOOL_CALL" and issue.severity == "warning"
        for issue in replay.integrity.warnings
    )


def test_missing_session_start_is_an_error() -> None:
    session = finished_session("ri-nostart")
    session.run()
    events = session.events()
    rebuilt = without_events(events, events[0])

    error = load_into_error(rebuilt, "ri-nostart")
    assert "MISSING_SESSION_START" in error_codes(error)

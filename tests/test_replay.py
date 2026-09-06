"""Timeline and replay: read-only reconstruction from the event log alone."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemoryEventStore, InMemorySnapshotStore
from packages.observability.replay import ReplayError, SessionReplayer
from packages.observability.sqlite import SqliteEventStore, SqlitePersistence, SqliteSnapshotStore
from packages.observability.timeline import SessionTimelineBuilder, UnknownSessionError
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

ESTIMATOR = DeterministicEstimator()


class LongTool:
    name = "long_tool"
    description = "Returns a long observation."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value="x" * 1500)


def make_provider() -> FakeModelProvider:
    return FakeModelProvider(
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
    )


def run_full_session(
    session_id: str, snapshot_store=None, bus: EventBus | None = None
) -> AgentSession:
    """Session with prompt builder, context manager and compaction wired."""
    budget = TokenBudgetManager(
        BudgetConfig(max_context_tokens=500, reserved_output_tokens=50)
    )
    return AgentSession(
        task="Digest the long report.",
        provider=make_provider(),
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
        bus=bus,
        session_id=session_id,
    )


def test_timeline_lists_every_event_in_sequence_with_summaries() -> None:
    session = run_full_session("tl-1", InMemorySnapshotStore())
    session.run()

    timeline = SessionTimelineBuilder(session.store).build("tl-1")
    assert [e.sequence for e in timeline.entries] == list(range(len(timeline.entries)))
    assert len(timeline.entries) == len(session.events())
    assert timeline.entries[0].event_type == "SessionStarted"
    assert "Digest the long report." in timeline.entries[0].summary
    tool = next(e for e in timeline.entries if e.event_type == "ToolCallFinished")
    assert "ok=True" in tool.summary
    assert len(tool.summary) < 120  # long values are previewed, not dumped
    assert timeline.entries[-1].event_type == "AgentFinished"
    assert timeline.event_counts["LLMCallStarted"] == 2


def test_timeline_unknown_session_raises_typed_error() -> None:
    builder = SessionTimelineBuilder(InMemoryEventStore())
    with pytest.raises(UnknownSessionError):
        builder.build("missing")


def test_replay_reconstructs_steps_answers_and_identity() -> None:
    snapshot_store = InMemorySnapshotStore()
    session = run_full_session("rp-1", snapshot_store)
    result = session.run()

    replay = SessionReplayer(session.store, snapshot_store).load("rp-1")
    assert replay.session_id == "rp-1"
    assert replay.trace_id == session.trace_id
    assert replay.task == "Digest the long report."
    assert replay.model == "fake-model"
    assert replay.max_steps == 4
    assert replay.status == "finished"
    assert replay.final_answer == result.answer == "Report digested."
    assert len(replay.steps) == 2
    assert replay.prompt_snapshot is not None
    assert replay.prompt_snapshot.snapshot_id == replay.prompt_snapshot_id


def test_replay_attaches_context_snapshots_and_tool_calls_per_step() -> None:
    snapshot_store = InMemorySnapshotStore()
    session = run_full_session("rp-2", snapshot_store)
    session.run()

    replay = SessionReplayer(session.store, snapshot_store).load("rp-2")
    step1, step2 = replay.steps
    assert step1.context_snapshot is not None
    assert step1.context_total_tokens is not None
    assert step1.finish_reason == "tool_calls"
    assert [c.name for c in step1.tool_calls] == ["long_tool"]
    assert step1.tool_calls[0].ok is True
    assert step1.tool_calls[0].value == "x" * 1500
    # The compaction fired before step 2 and is attached to it.
    assert step2.compaction is not None
    assert step2.compaction.strategy == "semantic_state"
    assert step2.finish_reason == "stop"
    assert step2.response_content == "Report digested."
    assert len(replay.compactions) == 1
    assert replay.compactions[0].preserved_state["goal"] == "Digest the long report."


def test_replay_matches_the_snapshots_recorded_during_the_run() -> None:
    snapshot_store = InMemorySnapshotStore()
    session = run_full_session("rp-3", snapshot_store)
    session.run()
    replay = SessionReplayer(session.store, snapshot_store).load("rp-3")

    stored = {s.snapshot_id: s for s in snapshot_store.get_context_snapshots("rp-3")}
    for step in replay.steps:
        assert step.context_snapshot == stored[step.context_snapshot_id]


def test_replay_works_without_a_snapshot_store() -> None:
    session = run_full_session("rp-4")
    session.run()
    replay = SessionReplayer(session.store).load("rp-4")
    assert replay.status == "finished"
    assert replay.steps[1].context_snapshot is None
    assert replay.steps[1].context_total_tokens is not None
    assert replay.steps[1].response_content == "Report digested."
    assert replay.compactions[0].preserved_state["goal"] == "Digest the long report."


def test_replay_of_failed_session_preserves_redacted_error() -> None:
    session = AgentSession(
        task="doomed",
        provider=FakeModelProvider([RuntimeError("api_key=sk-secret-9 leaked")]),
        session_id="rp-fail",
    )
    session.run()
    replay = SessionReplayer(session.store).load("rp-fail")
    assert replay.status == "failed"
    assert "sk-secret-9" not in (replay.error or "")
    assert replay.final_answer is None


def test_replay_never_executes_provider_or_tools_across_restart() -> None:
    # Phase 1: run into SQLite. Phase 2: fresh stores, no runtime objects.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "replay.db"
        bus = EventBus()
        persistence = SqlitePersistence(bus, db)
        snapshots = SqliteSnapshotStore(db)
        session = run_full_session("rp-sqlite", snapshots, bus=bus)
        session.run()
        persistence.close()
        snapshots.close()

        event_store = SqliteEventStore(db)
        snapshot_store = SqliteSnapshotStore(db)
        try:
            first = SessionReplayer(event_store, snapshot_store).load("rp-sqlite")
            second = SessionReplayer(event_store, snapshot_store).load("rp-sqlite")
        finally:
            event_store.close()
            snapshot_store.close()

        assert first == second  # replay is a pure function of the log
        assert first.status == "finished"
        assert first.final_answer == "Report digested."
        assert len(first.steps) == 2


def test_unknown_session_raises_typed_error_on_replay() -> None:
    replayer = SessionReplayer(InMemoryEventStore())
    with pytest.raises(UnknownSessionError):
        replayer.load("missing")


def test_sequence_gap_raises_replay_error() -> None:
    store = InMemoryEventStore()
    session = AgentSession(
        task="t",
        provider=FakeModelProvider(
            [ModelResponse(message=ModelMessage(role="assistant", content="done"),
                           finish_reason="stop")]
        ),
        store=store,
        session_id="gap-session",
    )
    session.run()
    events = store.get_session_events("gap-session")
    # Simulate a lost event by moving one event's sequence forward.
    tampered = list(events)
    tampered[2] = tampered[2].model_copy(update={"sequence": 5})
    tampered_store = InMemoryEventStore()
    for event in sorted(tampered, key=lambda e: e.sequence):
        tampered_store.append(event)

    replayer = SessionReplayer(tampered_store)
    with pytest.raises(ReplayError, match="sequence gap"):
        replayer.load("gap-session")


def test_replay_records_every_event_type_exactly_once_per_occurrence() -> None:
    session = run_full_session("rp-count", InMemorySnapshotStore())
    session.run()
    replay = SessionReplayer(session.store, InMemorySnapshotStore()).load("rp-count")
    types = [e.event_type for e in session.events()]
    # Nothing was dropped: compactions + steps + terminal events all present.
    assert types.count(AgentEventType.CONTEXT_COMPACTION_FINISHED) == len(replay.compactions)
    assert types.count(AgentEventType.LLM_CALL_FINISHED) == len(replay.steps)
    assert types[-1] == AgentEventType.AGENT_FINISHED

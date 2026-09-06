"""Phase 3 (T5) demo: inspect the timeline and replay a session from the log.

Run:

    python examples/replay_demo.py

Phase 1 runs the agent; then every runtime object (provider, tools, session)
goes out of scope. The replay phase opens fresh SQLite stores over the same
database and rebuilds the whole session from the event log and snapshots
only: per-step context ("what the model saw"), responses, tool calls,
compactions and the final answer. No provider or tool exists in the replay
phase, so nothing can be re-executed.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.eventbus import EventBus
from packages.observability.replay import SessionReplayer
from packages.observability.sqlite import SqliteEventStore, SqlitePersistence, SqliteSnapshotStore
from packages.observability.timeline import SessionTimelineBuilder
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession


class LongTool:
    name = "long_tool"
    description = "Returns a long observation."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value="x" * 1500)


def build_provider() -> FakeModelProvider:
    tool_call = ModelResponse(
        message=ModelMessage(role="assistant", content=""),
        finish_reason="tool_calls",
        tool_calls=(ToolCallRequest(call_id="c1", name="long_tool", arguments={}),),
    )
    final = ModelResponse(
        message=ModelMessage(role="assistant", content="Report digested."),
        finish_reason="stop",
    )
    return FakeModelProvider([tool_call, final])


def run_session(db_path: Path) -> str:
    bus = EventBus()
    persistence = SqlitePersistence(bus, db_path)
    store = SqliteSnapshotStore(db_path)
    estimator = DeterministicEstimator()
    budget = TokenBudgetManager(BudgetConfig(max_context_tokens=500, reserved_output_tokens=50))

    session = AgentSession(
        task="Digest the long report.",
        provider=build_provider(),
        tools=[LongTool()],
        config=AgentLoopConfig(max_steps=4, system_prompt="You are AgentFlow."),
        prompt_builder=PromptBuilder(estimator, store),
        context_manager=ContextManager(
            estimator,
            budget,
            store,
            compaction_engine=CompactionEngine(
                estimator, budget, CompactionConfig(strategy="semantic_state")
            ),
        ),
        bus=bus,
    )
    result = session.run()
    print(f"[phase 1] ran session {session.session_id[:8]}… status={result.status} "
          f"answer={result.answer!r}")
    persistence.close()
    store.close()
    return session.session_id


def replay_session(db_path: Path, session_id: str) -> None:
    # Fresh store instances over the same database: the runtime is gone.
    event_store = SqliteEventStore(db_path)
    snapshot_store = SqliteSnapshotStore(db_path)
    try:
        timeline = SessionTimelineBuilder(event_store).build(session_id)
        replay = SessionReplayer(event_store, snapshot_store).load(session_id)
    finally:
        event_store.close()
        snapshot_store.close()

    print(f"\n[phase 2] replayed {session_id[:8]}… from the event log alone "
          f"(no provider, no tools in scope)")

    print(f"\n-- timeline ({len(timeline.entries)} entries) --")
    for entry in timeline.entries:
        step = f" step={entry.step}" if entry.step is not None else ""
        print(f"  seq={entry.sequence:02d} {entry.event_type}{step}: {entry.summary}")

    print("\n-- replayed steps (what the model saw) --")
    for step in replay.steps:
        tokens = step.context_total_tokens
        print(f"  step {step.step}: context_tokens={tokens} fits={step.context_fits} "
              f"finish_reason={step.finish_reason}")
        if step.compaction is not None:
            c = step.compaction
            print(f"    compaction[{c.strategy}]: {c.before_tokens}→{c.after_tokens} tokens, "
                  f"removed={len(c.removed_ids)}, fits={c.fits_budget}")
        for call in step.tool_calls:
            print(f"    tool {call.name} ok={call.ok} value={str(call.value)[:24]}…")
        print(f"    answer: {step.response_content!r}")

    print(f"\nstatus: {replay.status}  final answer: {replay.final_answer!r}")
    print(f"task: {replay.task!r}  model: {replay.model}  max_steps: {replay.max_steps}")


def main() -> None:
    print("=== AgentFlow T5 demo: timeline and replay without re-execution ===")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "replay-demo.db"
        session_id = run_session(db_path)
        replay_session(db_path, session_id)
    print("\nthe runtime is gone; everything above came from the event log.")


if __name__ == "__main__":
    main()

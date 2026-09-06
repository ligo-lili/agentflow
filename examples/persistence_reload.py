"""Phase 1 persistence demo: session, events and JSON payloads survive a restart.

Run:

    python examples/persistence_reload.py

The demo runs in two real OS processes against one SQLite database
(``.agentflow/agentflow.db``):

1. default mode — prepares a fresh database, then spawns this same script
   with ``--write`` as a child process and waits for it to exit;
2. ``--write`` mode — runs one deterministic offline agent session wired to
   ``SqlitePersistence``, prints what it wrote, and exits (connections closed).

After the child process is gone, the parent opens brand-new store instances
over the same file and reloads the session record and full event log — no
in-memory state is shared, so everything shown was read back from SQLite.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.eventbus import EventBus
from packages.observability.sqlite import (
    DEFAULT_DB_PATH,
    SqliteEventStore,
    SqlitePersistence,
    SqliteSessionStore,
)
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

SCRIPT = Path(__file__).resolve()

REPORT_TEXT = (
    "AgentFlow makes agent behavior inspectable: prompts, context, tool "
    "results and compaction become measurable artifacts instead of hidden state."
)


class WordCountTool:
    """Deterministic demo tool: counts the words of the given text."""

    name = "word_count"
    description = "Count the words in the provided text."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        text = str(arguments.get("text", ""))
        return ToolResult(name=self.name, ok=True, value=len(text.split()))


def build_provider() -> FakeModelProvider:
    """Same scripted plan as simple_agent.py: one tool call, then the answer."""
    word_count = len(REPORT_TEXT.split())
    return FakeModelProvider(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCallRequest(
                        call_id="call-1",
                        name="word_count",
                        arguments={"text": REPORT_TEXT},
                    ),
                ),
            ),
            ModelResponse(
                message=ModelMessage(
                    role="assistant",
                    content=f"The report contains {word_count} words.",
                ),
                finish_reason="stop",
            ),
        ]
    )


def build_session(db_path: Path) -> tuple[AgentSession, SqlitePersistence]:
    """Build the demo session wired to SQLite through the event bus."""
    bus = EventBus()
    persistence = SqlitePersistence(bus, db_path)
    session = AgentSession(
        task="Count the words of the AgentFlow report text.",
        provider=build_provider(),
        tools=[WordCountTool()],
        config=AgentLoopConfig(model="fake-model", max_steps=8),
        bus=bus,
    )
    return session, persistence


def run_write_phase(db_path: Path) -> None:
    session, persistence = build_session(db_path)
    result = session.run()
    persistence.close()
    print(f"[child ] wrote {len(session.events())} events for session {session.session_id}")
    print(f"[child ] run status: {result.status}, answer: {result.answer}")


def run_reload_phase(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()

    subprocess.run([sys.executable, str(SCRIPT), "--write"], check=True, timeout=60)
    print("[parent] child process exited; reopening the database from scratch")

    event_store = SqliteEventStore(db_path)
    session_store = SqliteSessionStore(db_path)
    try:
        session_ids = event_store.session_ids()
        if len(session_ids) != 1:
            raise SystemExit(f"expected exactly one session in the demo db, got {session_ids}")
        events = event_store.require_session_events(session_ids[0])
        record = session_store.require_session(session_ids[0])
    finally:
        event_store.close()
        session_store.close()

    print(f"[parent] reloaded session: task={record.task!r} status={record.status}")
    print(f"[parent] reloaded {len(events)} events in sequence order:")
    for event in events:
        print(f"  seq={event.sequence:02d} {event.event_type.value}")


def main() -> None:
    if "--write" in sys.argv:
        run_write_phase(DEFAULT_DB_PATH)
        return

    print("=== AgentFlow Phase 1 demo: SQLite persistence and reload ===")
    print(f"database: {DEFAULT_DB_PATH}")
    run_reload_phase(DEFAULT_DB_PATH)
    print("[parent] round-trip complete: the child is gone, the data stayed.")


if __name__ == "__main__":
    main()

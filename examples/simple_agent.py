"""Phase 1 demo: one deterministic offline agent task with a tool call.

Run:

    python examples/simple_agent.py

The task runs fully offline: the FakeModelProvider follows a fixed script
(tool call, then final answer), so the event trace and output are identical
on every run. After the run, the recorded events are re-read from the event
store (not from the loop) to demonstrate the event-log integration boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.core.events import AgentEvent
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

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
    """Scripted plan: call the tool once, then give the final answer."""
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


def describe_event(event: AgentEvent) -> str:
    if event.event_type.value == "ToolCallFinished":
        detail = f"ok={event.payload['ok']} value={event.payload['value']}"
    elif event.event_type.value == "LLMCallFinished":
        detail = f"finish_reason={event.payload['finish_reason']}"
    elif event.event_type.value == "AgentFinished":
        detail = f"answer={event.payload['answer']!r}"
    else:
        detail = ""
    suffix = f" ({detail})" if detail else ""
    return f"  seq={event.sequence:02d} {event.event_type.value}{suffix}"


def main() -> None:
    config = AgentLoopConfig(model="fake-model", max_steps=8)
    session = AgentSession(
        task="Count the words of the AgentFlow report text.",
        provider=build_provider(),
        tools=[WordCountTool()],
        config=config,
    )

    print("=== AgentFlow Phase 1 demo: simple agent ===")
    print(f"session: {session.session_id[:8]}…  task: Count the words of the report")
    result = session.run()

    print("\n-- event trace (from the event store, in sequence order) --")
    for event in session.events():
        print(describe_event(event))

    print("\n-- result --")
    print(f"status: {result.status}")
    print(f"answer: {result.answer}")
    print(f"steps:  {result.steps} (LLM calls)")
    print(f"events: {len(session.events())} persisted")


if __name__ == "__main__":
    main()

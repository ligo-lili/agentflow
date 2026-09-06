"""Phase 2 (T3) demo: context growth is measured at every model call.

Run:

    python examples/context_growth_demo.py

The scripted agent calls a report generator three times; each tool result is
appended to the transcript, so the model-bound context grows step by step.
Before every model call the ContextManager records a ContextSnapshot with a
per-role token breakdown and a budget verdict. All numbers are deterministic
(``deterministic-v1`` estimator), so this output is reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.inmemory import InMemorySnapshotStore
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession


class ReportTool:
    """Generates a filler report of the requested length (in words)."""

    name = "generate_report"
    description = "Generate a report with the given number of words."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        words = int(arguments.get("words", 10))
        return ToolResult(name=self.name, ok=True, value=" ".join(["data"] * words))


def script() -> list[ModelResponse]:
    def tool_call(call_id: str, words: int) -> ModelResponse:
        return ModelResponse(
            message=ModelMessage(role="assistant", content=""),
            finish_reason="tool_calls",
            tool_calls=(
                ToolCallRequest(
                    call_id=call_id, name="generate_report", arguments={"words": words}
                ),
            ),
        )

    return [
        tool_call("call-1", 30),
        tool_call("call-2", 90),
        tool_call("call-3", 270),
        ModelResponse(
            message=ModelMessage(role="assistant", content="Report digested."),
            finish_reason="stop",
        ),
    ]


def main() -> None:
    store = InMemorySnapshotStore()
    estimator = DeterministicEstimator()
    budget = TokenBudgetManager(BudgetConfig(max_context_tokens=4096, reserved_output_tokens=256))

    session = AgentSession(
        task="Digest the growing report and confirm the final length.",
        provider=FakeModelProvider(script()),
        tools=[ReportTool()],
        config=AgentLoopConfig(model="fake-model", max_steps=8,
                               system_prompt="You are AgentFlow, a context debugger."),
        prompt_builder=PromptBuilder(estimator, store),
        context_manager=ContextManager(estimator, budget, store),
    )

    print("=== AgentFlow T3 demo: context growth under budget ===")
    print(f"estimator: {estimator.name}  budget: 4096-256=3840 input tokens")
    result = session.run()

    print("\n-- context built before each model call --")
    for event in session.events():
        if event.event_type.value == "ContextBuilt":
            p = event.payload
            print(
                f"  step={p['step']} total_tokens={p['total_tokens']:4d} "
                f"fits={p['fits']} ({p['reason']})"
            )

    print("\n-- persisted snapshots --")
    for snapshot in store.get_context_snapshots(session.session_id):
        print(
            f"  {snapshot.snapshot_id}: total={snapshot.total_tokens:4d} "
            f"components={snapshot.component_tokens}"
        )

    print(f"\nresult: {result.status}, steps={result.steps}, answer={result.answer!r}")


if __name__ == "__main__":
    main()

"""A/B experiment: measure both compaction strategies on one shared benchmark.

The canonical benchmark (docs/architecture/evaluation.md) is offline and
deterministic, includes a tool call, context growth and at least one
compaction. Both strategies receive the exact same fixture: identical task,
provider script, tool, budget and step limit — only the compaction strategy
differs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.evals.evaluator import EvaluationConfig, EvaluationReport, Evaluator
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemoryEventStore, InMemorySnapshotStore
from packages.observability.replay import SessionReplayer
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

#: Budget for the canonical benchmark (limit 450, threshold 360).
CANONICAL_BUDGET = BudgetConfig(max_context_tokens=500, reserved_output_tokens=50)

#: The benchmark plan is 3 LLM calls + 2 tool calls; a correct run cannot do
#: better, so this is the step baseline for the benchmark.
CANONICAL_BASELINE_STEPS = 5

CANONICAL_STRATEGIES = ("keep_recent_summary", "semantic_state")

_TOOL_VALUE = "x" * 1500


class BenchmarkTool:
    name = "long_tool"
    description = "Returns a long observation (drives context growth)."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value=_TOOL_VALUE)


def _benchmark_script() -> list[ModelResponse]:
    tool_call = ModelResponse(
        message=ModelMessage(role="assistant", content=""),
        finish_reason="tool_calls",
        tool_calls=(ToolCallRequest(call_id="c1", name="long_tool", arguments={}),),
    )
    final = ModelResponse(
        message=ModelMessage(role="assistant", content="Benchmark complete."),
        finish_reason="stop",
    )
    return [tool_call, tool_call.model_copy(deep=True), final]


def build_benchmark_session(
    strategy: str,
    session_id: str,
    event_store: InMemoryEventStore,
    snapshot_store: InMemorySnapshotStore,
) -> AgentSession:
    """One canonical benchmark session; ``strategy`` is the only variable."""
    bus = EventBus()
    event_store = _subscribe(event_store, bus)
    estimator = DeterministicEstimator()
    budget = TokenBudgetManager(CANONICAL_BUDGET)
    return AgentSession(
        task="Digest the long report.",
        provider=FakeModelProvider(_benchmark_script()),
        tools=[BenchmarkTool()],
        config=AgentLoopConfig(max_steps=4, system_prompt="You are AgentFlow."),
        prompt_builder=PromptBuilder(estimator, snapshot_store),
        context_manager=ContextManager(
            estimator,
            budget,
            snapshot_store,
            compaction_engine=CompactionEngine(
                estimator,
                budget,
                CompactionConfig(strategy=strategy, keep_last_messages=1),  # type: ignore[arg-type]
            ),
        ),
        bus=bus,
        session_id=session_id,
    )


def _subscribe(event_store: InMemoryEventStore, bus: EventBus) -> InMemoryEventStore:
    bus.subscribe(event_store.append)
    return event_store


def run_benchmark(strategy: str, session_id: str | None = None) -> EvaluationReport:
    """Run one strategy on the canonical fixture and evaluate the replay."""
    session_id = session_id or f"bench-{strategy}"
    event_store = InMemoryEventStore()
    snapshot_store = InMemorySnapshotStore()
    session = build_benchmark_session(strategy, session_id, event_store, snapshot_store)
    session.run()
    replay = SessionReplayer(event_store, snapshot_store).load(session_id)
    evaluator = Evaluator(
        EvaluationConfig(baseline_steps=CANONICAL_BASELINE_STEPS)
    )
    return evaluator.evaluate(replay)


class StrategyComparison(BaseModel):
    """One strategy's report inside the comparison."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    report: EvaluationReport


class ComparisonReport(BaseModel):
    """A/B result over the same fixture; winner is the higher score."""

    model_config = ConfigDict(frozen=True)

    entries: tuple[StrategyComparison, ...]
    winner: str
    score_delta: float


def compare_strategies(
    strategies: tuple[str, ...] = CANONICAL_STRATEGIES,
) -> ComparisonReport:
    """Evaluate every strategy on the identical fixture and rank by score."""
    entries = tuple(
        StrategyComparison(strategy=strategy, report=run_benchmark(strategy))
        for strategy in strategies
    )
    ranked = sorted(entries, key=lambda e: e.report.score, reverse=True)
    best, runner_up = ranked[0], ranked[-1]
    return ComparisonReport(
        entries=entries,
        winner=best.strategy,
        score_delta=round(best.report.score - runner_up.report.score, 6),
    )

"""Versioned multi-scenario evaluation suite (review R6).

The canonical benchmark (``compare.py``) stays as-is; this module adds a
small, versioned suite of three deterministic scenarios so the A/B result is
not based on one fixture:

``irrelevant_large_output``
    One huge, task-irrelevant tool observation inflates the context; the
    answer does not depend on it. Checks: compaction triggers, the goal
    survives, the blob does not leak into the answer, and the final answer
    is intact. Both strategies keep the verbatim blob (the recent tail /
    artifact contract), so the compaction is honestly recorded with zero
    token recovery — the raw metrics show it instead of hiding it.

``critical_early_decision``
    An early assistant message records a critical decision; large tool
    outputs then push the transcript over budget. Checks: the decision text
    survives compaction (``semantic_state`` carries it in its structured
    state; ``keep_recent_summary`` documents only task + latest tool result
    and legitimately fails this check — reported as a failure case, not
    hidden), and the final answer is intact.

``repeated_failed_tool``
    The tool fails repeatedly; the run still completes. Checks: every tool
    call is recorded as failed, failed results are never recorded as
    artifacts, and the final answer is intact.

Reporting order: raw per-scenario metrics first (``EvaluationReport`` fields),
composite score second (last field of each report; ``StrategyAggregate``
lists the mean raw metrics before its ``composite``). The sensitivity block
re-scores the same aggregates under an alternate, documented weight set. No
LLM judge and no network access are involved.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import Tool, ToolContext, ToolResult
from packages.evals.evaluator import EvaluationConfig, EvaluationReport, Evaluator
from packages.evals.metrics import SCORE_WEIGHTS, composite_score
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemoryEventStore, InMemorySnapshotStore
from packages.observability.replay import SessionReplay, SessionReplayer
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

#: Version of the scenario definitions and their semantic checks.
SCENARIO_SUITE_VERSION = "1"

#: The documented composite weights (see ``packages.evals.metrics``).
DOCUMENTED_WEIGHTS: dict[str, float] = dict(SCORE_WEIGHTS)

#: Alternate weight set for the sensitivity comparison (review R6):
#: reliability-heavy — task completion and tool reliability weigh more, the
#: efficiency terms less. Sum is 1.0 like the documented set.
ALTERNATE_WEIGHTS: dict[str, float] = {
    "task_completed": 0.40,
    "tool_success_rate": 0.25,
    "context_efficiency": 0.20,
    "step_efficiency": 0.10,
    "compaction_efficiency": 0.05,
}

_SUITE_BUDGET = BudgetConfig(max_context_tokens=500, reserved_output_tokens=50)

_DECISION_TEXT = "Decision: use SQLite for persistence."
_FINAL_LARGE_OUTPUT = "Q3 revenue is 1200."
_FINAL_FAILED_TOOLS = "Metrics unavailable; reported the failures."


def _blob(marker: str) -> str:
    return f"{marker}: " + "lorem ipsum dolor sit amet " * 52


class _BlobTool:
    """Deterministic tool returning one large fixed observation."""

    def __init__(self, name: str, value: str) -> None:
        self._name = name
        self._value = value
        self.name = name
        self.description = f"Returns the fixed {name} observation."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self._name, ok=True, value=self._value)


class _FailingTool:
    """Deterministic tool that always fails (raised error, never a value)."""

    name = "get_metrics"
    description = "Fails on every call."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        raise RuntimeError("metrics service unreachable")


def _tool_call(call_id: str, name: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=""),
        finish_reason="tool_calls",
        tool_calls=(ToolCallRequest(call_id=call_id, name=name, arguments={}),),
    )


def _final(content: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=content),
        finish_reason="stop",
    )


def _decision_tool_call() -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=_DECISION_TEXT),
        finish_reason="tool_calls",
        tool_calls=(ToolCallRequest(call_id="c1", name="blob_a", arguments={}),),
    )


def _generated_summaries(replay: SessionReplay) -> list[str]:
    """Rendered compaction summary/state texts recorded in the snapshots."""
    texts: list[str] = []
    for step in replay.steps:
        if step.context_snapshot is None:
            continue
        for message in step.context_snapshot.messages:
            if message.name in ("compaction_summary", "semantic_state"):
                texts.append(message.content)
    return texts


def _check_goal_preserved(replay: SessionReplay, strategy: str) -> bool:
    task = replay.task
    if strategy == "semantic_state":
        return any(
            compaction.preserved_state.get("goal") == task
            for compaction in replay.compactions
        )
    return any(task in text for text in _generated_summaries(replay))


def _check_decision_preserved(replay: SessionReplay, strategy: str) -> bool:
    if strategy == "semantic_state":
        return any(
            _DECISION_TEXT in json.dumps(compaction.preserved_state)
            for compaction in replay.compactions
        )
    return any(_DECISION_TEXT in text for text in _generated_summaries(replay))


def _check_final_answer(expected: str) -> Callable[[SessionReplay, str], bool]:
    def check(replay: SessionReplay, strategy: str) -> bool:
        return replay.final_answer == expected

    return check


def _check_compaction_triggered(replay: SessionReplay, strategy: str) -> bool:
    return len(replay.compactions) >= 1


def _check_answer_ignores_blob(marker: str) -> Callable[[SessionReplay, str], bool]:
    def check(replay: SessionReplay, strategy: str) -> bool:
        return replay.final_answer is not None and marker not in replay.final_answer

    return check


def _check_all_tool_calls_failed(replay: SessionReplay, strategy: str) -> bool:
    calls = [c for step in replay.steps for c in step.tool_calls]
    return bool(calls) and all(c.ok is False for c in calls)


def _check_failed_results_not_artifacts(replay: SessionReplay, strategy: str) -> bool:
    for compaction in replay.compactions:
        artifacts = compaction.preserved_state.get("artifacts", [])
        if any("unreachable" in str(artifact) for artifact in artifacts):
            return False
    return True


@dataclass(frozen=True)
class SemanticCheck:
    """One scenario-specific deterministic assertion over the replay."""

    code: str
    description: str
    fn: Callable[[SessionReplay, str], bool]


@dataclass(frozen=True)
class Scenario:
    """One versioned suite scenario: fixture plus its semantic checks."""

    name: str
    description: str
    task: str
    script: tuple[ModelResponse, ...]
    tools: tuple[Tool, ...]
    baseline_steps: int
    checks: tuple[SemanticCheck, ...] = field(default_factory=tuple)
    budget: BudgetConfig = _SUITE_BUDGET
    max_steps: int = 4


def iter_scenarios() -> tuple[Scenario, ...]:
    """The suite scenarios in stable, documented order."""
    return (
        Scenario(
            name="irrelevant_large_output",
            description=(
                "One huge task-irrelevant tool observation inflates the context; "
                "the answer does not depend on it and the compaction outcome is "
                "recorded honestly (zero recovery when the blob must be kept)."
            ),
            task="Summarize the Q3 revenue line only.",
            script=(
                _tool_call("c1", "marketing_dump"),
                _final(_FINAL_LARGE_OUTPUT),
            ),
            tools=(_BlobTool("marketing_dump", _blob("marketing blast")),),
            baseline_steps=3,
            checks=(
                SemanticCheck(
                    code="compaction_triggered",
                    description="the large output pushed the context over threshold",
                    fn=_check_compaction_triggered,
                ),
                SemanticCheck(
                    code="goal_preserved",
                    description="the session goal survives compaction",
                    fn=_check_goal_preserved,
                ),
                SemanticCheck(
                    code="answer_ignores_irrelevant_output",
                    description="the irrelevant blob does not leak into the answer",
                    fn=_check_answer_ignores_blob("marketing"),
                ),
                SemanticCheck(
                    code="final_answer_intact",
                    description="the final answer does not change",
                    fn=_check_final_answer(_FINAL_LARGE_OUTPUT),
                ),
            ),
        ),
        Scenario(
            name="critical_early_decision",
            description=(
                "An early assistant message records a critical decision; large "
                "tool outputs then push the transcript over budget."
            ),
            task="Plan the database migration rollout.",
            script=(
                _decision_tool_call(),
                _tool_call("c2", "blob_b"),
                _final("Rollout plan ready: follow the decision above."),
            ),
            tools=(
                _BlobTool("blob_a", _blob("migration survey")),
                _BlobTool("blob_b", _blob("host inventory")),
            ),
            baseline_steps=5,
            checks=(
                SemanticCheck(
                    code="decision_survives_compaction",
                    description="the early decision text survives compaction",
                    fn=_check_decision_preserved,
                ),
                SemanticCheck(
                    code="final_answer_intact",
                    description="the final answer does not change",
                    fn=_check_final_answer("Rollout plan ready: follow the decision above."),
                ),
            ),
        ),
        Scenario(
            name="repeated_failed_tool",
            description=(
                "The tool fails on every call; the run still completes and the "
                "failures are recorded honestly."
            ),
            task="Fetch the metrics three times, then report.",
            script=(
                _tool_call("c1", "get_metrics"),
                _tool_call("c2", "get_metrics"),
                _tool_call("c3", "get_metrics"),
                _final(_FINAL_FAILED_TOOLS),
            ),
            tools=(_FailingTool(),),
            baseline_steps=7,
            checks=(
                SemanticCheck(
                    code="all_tool_calls_failed",
                    description="every tool call is recorded as failed",
                    fn=_check_all_tool_calls_failed,
                ),
                SemanticCheck(
                    code="failed_results_not_artifacts",
                    description="failed results are never recorded as artifacts",
                    fn=_check_failed_results_not_artifacts,
                ),
                SemanticCheck(
                    code="final_answer_intact",
                    description="the final answer does not change",
                    fn=_check_final_answer(_FINAL_FAILED_TOOLS),
                ),
            ),
        ),
    )


class CheckResult(BaseModel):
    """Outcome of one semantic check."""

    model_config = ConfigDict(frozen=True)

    code: str
    description: str
    passed: bool
    detail: str


class ScenarioResult(BaseModel):
    """One (scenario, strategy) run: raw metrics first, checks second."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    strategy: str
    report: EvaluationReport
    checks: tuple[CheckResult, ...] = ()


class StrategyAggregate(BaseModel):
    """Per-strategy aggregate: mean raw metrics first, composite last."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    scenarios: int
    mean_task_completed: float
    mean_tool_success_rate: float
    mean_context_efficiency: float
    mean_step_efficiency: float
    mean_compaction_efficiency: float
    composite: float


class CheckFailure(BaseModel):
    """One failed semantic check, reported instead of hidden."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    strategy: str
    code: str
    description: str


class WeightSensitivity(BaseModel):
    """Suite winner under one named weight set."""

    model_config = ConfigDict(frozen=True)

    weights_name: str
    weights: dict[str, float]
    composites: dict[str, float]
    winner: str
    score_delta: float


class SuiteReport(BaseModel):
    """The whole suite result: scenarios, aggregates, failures, sensitivity."""

    model_config = ConfigDict(frozen=True)

    suite_version: str
    estimator: str
    strategies: tuple[str, ...]
    scenarios: tuple[ScenarioResult, ...]
    aggregates: tuple[StrategyAggregate, ...]
    failures: tuple[CheckFailure, ...] = ()
    sensitivity: tuple[WeightSensitivity, ...] = ()


def build_scenario_session(
    scenario: Scenario,
    strategy: str,
    session_id: str,
    event_store: InMemoryEventStore,
    snapshot_store: InMemorySnapshotStore,
) -> AgentSession:
    """One scenario session; ``strategy`` is the only variable."""
    bus = EventBus()
    bus.subscribe(event_store.append)
    estimator = DeterministicEstimator()
    budget = TokenBudgetManager(scenario.budget)
    return AgentSession(
        task=scenario.task,
        provider=FakeModelProvider(scenario.script),
        tools=list(scenario.tools),
        config=AgentLoopConfig(max_steps=scenario.max_steps, system_prompt="You are AgentFlow."),
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


def run_scenario(
    scenario: Scenario,
    strategy: str,
    session_id: str | None = None,
) -> ScenarioResult:
    """Run one (scenario, strategy) pair and evaluate replay plus checks."""
    session_id = session_id or f"suite-{scenario.name}-{strategy}"
    event_store = InMemoryEventStore()
    snapshot_store = InMemorySnapshotStore()
    session = build_scenario_session(
        scenario, strategy, session_id, event_store, snapshot_store
    )
    session.run()
    replay = SessionReplayer(event_store, snapshot_store).load(session_id)
    report = Evaluator(EvaluationConfig(baseline_steps=scenario.baseline_steps)).evaluate(replay)
    checks = tuple(
        CheckResult(
            code=check.code,
            description=check.description,
            passed=(passed := check.fn(replay, strategy)),
            detail="ok" if passed else "semantic artifact not preserved",
        )
        for check in scenario.checks
    )
    return ScenarioResult(scenario=scenario.name, strategy=strategy, report=report, checks=checks)


def _aggregate(strategy: str, reports: Sequence[EvaluationReport]) -> StrategyAggregate:
    def mean(pick: Callable[[EvaluationReport], float]) -> float:
        return round(sum(pick(r) for r in reports) / len(reports), 6)

    means = {
        "task_completed": mean(lambda r: float(r.task_completed)),
        "tool_success_rate": mean(lambda r: r.tool_success_rate),
        "context_efficiency": mean(lambda r: r.context_efficiency),
        "step_efficiency": mean(lambda r: r.step_efficiency),
        "compaction_efficiency": mean(lambda r: r.compaction_efficiency),
    }
    composite = composite_score(
        round(means["task_completed"]),
        means["tool_success_rate"],
        means["context_efficiency"],
        means["step_efficiency"],
        means["compaction_efficiency"],
    )
    return StrategyAggregate(
        strategy=strategy,
        scenarios=len(reports),
        mean_task_completed=means["task_completed"],
        mean_tool_success_rate=means["tool_success_rate"],
        mean_context_efficiency=means["context_efficiency"],
        mean_step_efficiency=means["step_efficiency"],
        mean_compaction_efficiency=means["compaction_efficiency"],
        composite=round(composite, 6),
    )


def _sensitivity(
    strategies: tuple[str, ...],
    aggregates: Sequence[StrategyAggregate],
) -> tuple[WeightSensitivity, ...]:
    by_strategy = {a.strategy: a for a in aggregates}
    entries: list[WeightSensitivity] = []
    for name, weights in (("documented-v1", DOCUMENTED_WEIGHTS), ("reliability-heavy-v1", ALTERNATE_WEIGHTS)):
        composites = {
            strategy: round(
                composite_score(
                    round(by_strategy[strategy].mean_task_completed),
                    by_strategy[strategy].mean_tool_success_rate,
                    by_strategy[strategy].mean_context_efficiency,
                    by_strategy[strategy].mean_step_efficiency,
                    by_strategy[strategy].mean_compaction_efficiency,
                    weights=weights,
                ),
                6,
            )
            for strategy in strategies
        }
        winner = max(composites, key=lambda s: composites[s])
        sorted_scores = sorted(composites.values(), reverse=True)
        entries.append(
            WeightSensitivity(
                weights_name=name,
                weights=dict(weights),
                composites=composites,
                winner=winner,
                score_delta=round(sorted_scores[0] - sorted_scores[-1], 6),
            )
        )
    return tuple(entries)


def run_suite(strategies: tuple[str, ...] = ("keep_recent_summary", "semantic_state")) -> SuiteReport:
    """Run every scenario for every strategy on identical versioned fixtures.

    Fully offline and deterministic: fixed scripts, tools, budgets, session
    ids and the shared deterministic estimator — repeated runs are
    byte-for-byte stable.
    """
    scenario_results: list[ScenarioResult] = []
    failures: list[CheckFailure] = []
    for scenario in iter_scenarios():
        for strategy in strategies:
            result = run_scenario(scenario, strategy)
            scenario_results.append(result)
            failures.extend(
                CheckFailure(
                    scenario=result.scenario,
                    strategy=result.strategy,
                    code=check.code,
                    description=check.description,
                )
                for check in result.checks
                if not check.passed
            )

    aggregates = tuple(
        _aggregate(
            strategy,
            [r.report for r in scenario_results if r.strategy == strategy],
        )
        for strategy in strategies
    )
    return SuiteReport(
        suite_version=SCENARIO_SUITE_VERSION,
        estimator=DeterministicEstimator().name,
        strategies=strategies,
        scenarios=tuple(scenario_results),
        aggregates=aggregates,
        failures=tuple(failures),
        sensitivity=_sensitivity(strategies, aggregates),
    )

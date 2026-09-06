"""Evaluation and A/B comparison: named normalizations, deterministic benchmark."""

from __future__ import annotations

import pytest

from packages.core.provider import ModelMessage, ModelResponse
from packages.evals.evaluator import (
    DEFAULT_BASELINE_STEPS,
    EvaluationConfig,
    EvaluationReport,
    Evaluator,
)
from packages.evals.metrics import (
    SCORE_WEIGHTS,
    clamp01,
    compaction_efficiency,
    compaction_recovery,
    composite_score,
    context_efficiency,
    step_efficiency,
    tool_success_rate,
)
from packages.experiments.compare import (
    CANONICAL_BASELINE_STEPS,
    CANONICAL_STRATEGIES,
    build_benchmark_session,
    compare_strategies,
    run_benchmark,
)
from packages.observability.inmemory import InMemoryEventStore, InMemorySnapshotStore
from packages.observability.replay import SessionReplay, SessionReplayer
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

# --- named normalization functions -----------------------------------------

def test_tool_success_rate_handles_zero_denominator() -> None:
    assert tool_success_rate(0, 0) == 1.0  # no tool calls: not penalized
    assert tool_success_rate(4, 4) == 1.0
    assert tool_success_rate(4, 2) == 0.5
    assert tool_success_rate(4, 5) == 1.0  # clamped


def test_step_efficiency_uses_documented_baseline_rule() -> None:
    assert step_efficiency(0, 3) == 1.0  # nothing executed: neutral
    assert step_efficiency(3, DEFAULT_BASELINE_STEPS) == 1.0
    assert step_efficiency(6, 3) == 0.5  # twice the reference plan
    assert step_efficiency(10, 3) == 0.3  # clamped at the ratio, not below 0


def test_context_efficiency_rewards_headroom() -> None:
    assert context_efficiency(0, 450) == 1.0  # no snapshots: neutral
    assert context_efficiency(100, 0) == 1.0  # unknown limit: neutral
    assert context_efficiency(225, 450) == pytest.approx(0.5)
    assert context_efficiency(450, 450) == 0.0
    assert context_efficiency(900, 450) == 0.0  # clamped, never negative


def test_compaction_recovery_and_efficiency() -> None:
    assert compaction_recovery(0, 0) == 0.0
    assert compaction_recovery(100, 60) == pytest.approx(0.4)
    assert compaction_recovery(50, 60) == 0.0  # growth recorded as 0, honest
    assert compaction_efficiency([]) == 1.0  # zero compactions: neutral
    assert compaction_efficiency([0.4, 0.2]) == pytest.approx(0.3)


def test_score_weights_sum_to_one_and_composite_matches_documented_formula() -> None:
    assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9
    score = composite_score(1, 1.0, 1.0, 1.0, 1.0)
    assert score == 1.0
    manual = 0.35 * 1 + 0.20 * 0.5 + 0.20 * 0.25 + 0.15 * 0.75 + 0.10 * 0.1
    assert composite_score(1, 0.5, 0.25, 0.75, 0.1) == pytest.approx(clamp01(manual))
    assert composite_score(0, 1.0, 1.0, 1.0, 1.0) == pytest.approx(0.65)


# --- evaluator over replays --------------------------------------------------

def make_finished_replay(session_id: str) -> SessionReplay:
    session = AgentSession(
        task="t",
        provider=FakeModelProvider(
            [ModelResponse(message=ModelMessage(role="assistant", content="done"),
                           finish_reason="stop")]
        ),
        session_id=session_id,
    )
    session.run()
    return SessionReplayer(session.store).load(session_id)


def test_evaluator_scores_finished_session_with_tool_neutral_compaction() -> None:
    replay = make_finished_replay("eval-1")
    report = Evaluator(EvaluationConfig()).evaluate(replay)
    assert isinstance(report, EvaluationReport)
    assert report.task_completed == 1
    assert report.tool_call_count == 0
    assert report.tool_success_rate == 1.0  # zero-tool denominator rule
    assert report.compaction_count == 0
    assert report.compaction_efficiency == 1.0  # zero-compaction rule
    assert report.execution_steps == 1
    assert report.status == "finished"
    assert 0.0 <= report.score <= 1.0


def test_evaluator_reports_failed_session_as_incomplete() -> None:
    session = AgentSession(
        task="doomed",
        provider=FakeModelProvider([RuntimeError("provider offline")]),
        session_id="eval-fail",
    )
    session.run()
    replay = SessionReplayer(session.store).load("eval-fail")
    report = Evaluator(EvaluationConfig()).evaluate(replay)
    assert report.task_completed == 0
    assert report.status == "failed"
    # 0.35 * 0 + the four perfect metrics = the documented no-failure ceiling.
    assert report.score == pytest.approx(0.65)


def test_evaluator_deterministic_on_the_same_replay() -> None:
    replay = make_finished_replay("eval-det")
    first = Evaluator(EvaluationConfig()).evaluate(replay)
    second = Evaluator(EvaluationConfig()).evaluate(replay)
    assert first == second


# --- canonical benchmark and A/B comparison ---------------------------------

def test_benchmark_fixture_includes_growth_and_compaction_for_both_strategies() -> None:
    for strategy in CANONICAL_STRATEGIES:
        report = run_benchmark(strategy, session_id=f"fixture-{strategy}")
        assert report.task_completed == 1
        assert report.tool_call_count == 2  # the fixture includes tool calls
        assert report.compaction_count >= 1  # ...and at least one compaction
        assert report.peak_context_tokens > report.final_context_tokens >= 0 or (
            report.peak_context_tokens > 0
        )


def test_benchmark_is_deterministic_per_strategy() -> None:
    for strategy in CANONICAL_STRATEGIES:
        first = run_benchmark(strategy, session_id=f"det-{strategy}")
        second = run_benchmark(strategy, session_id=f"det-{strategy}")
        assert first == second


def test_benchmark_session_uses_only_strategy_as_variable() -> None:
    stores: list[tuple[InMemoryEventStore, InMemorySnapshotStore]] = []
    sessions = []
    for strategy in CANONICAL_STRATEGIES:
        events, snapshots = InMemoryEventStore(), InMemorySnapshotStore()
        session = build_benchmark_session(strategy, f"same-{strategy}", events, snapshots)
        sessions.append(session)
        stores.append((events, snapshots))
        session.run()
    replays = [
        SessionReplayer(events, snapshots).load(s.session_id)
        for s, (events, snapshots) in zip(sessions, stores)
    ]
    # Same fixture: identity, task, response content and tool outcomes align.
    assert [r.task for r in replays] == [replays[0].task] * len(replays)
    assert [r.steps[0].response_content for r in replays] == [replays[0].steps[0].response_content] * 2
    assert [r.steps[0].tool_calls[0].value for r in replays] == [replays[0].steps[0].tool_calls[0].value] * 2


def test_compare_strategies_ranks_by_score_with_delta() -> None:
    comparison = compare_strategies()
    assert len(comparison.entries) == 2
    scores = [e.report.score for e in comparison.entries]
    best = max(zip(CANONICAL_STRATEGIES, scores), key=lambda pair: pair[1])
    assert comparison.winner == best[0]
    assert comparison.score_delta == pytest.approx(abs(scores[0] - scores[1]), abs=1e-6)
    # Determinism of the whole comparison.
    assert compare_strategies() == comparison


def test_baseline_constant_matches_benchmark_plan() -> None:
    assert CANONICAL_BASELINE_STEPS == 5  # 3 LLM calls + 2 tool calls

"""Scenario suite (review R6): versioned fixtures, semantic checks, sensitivity."""

from __future__ import annotations

import json

import pytest

from packages.evals.metrics import SCORE_WEIGHTS
from packages.experiments.suite import (
    ALTERNATE_WEIGHTS,
    DOCUMENTED_WEIGHTS,
    SCENARIO_SUITE_VERSION,
    iter_scenarios,
    run_scenario,
    run_suite,
)


def test_suite_version_and_scenario_names_are_stable() -> None:
    assert SCENARIO_SUITE_VERSION == "1"
    assert [s.name for s in iter_scenarios()] == [
        "irrelevant_large_output",
        "critical_early_decision",
        "repeated_failed_tool",
    ]


def test_both_strategies_complete_every_scenario() -> None:
    for scenario in iter_scenarios():
        for strategy in ("keep_recent_summary", "semantic_state"):
            result = run_scenario(scenario, strategy)
            assert result.report.task_completed == 1
            assert result.report.status == "finished"


def test_strategies_receive_identical_versioned_fixtures() -> None:
    for scenario in iter_scenarios():
        keep = run_scenario(scenario, "keep_recent_summary")
        semantic = run_scenario(scenario, "semantic_state")
        # Same task, tool count and tool call count — only the strategy differs.
        assert keep.report.tool_call_count == semantic.report.tool_call_count
        assert keep.report.execution_steps == semantic.report.execution_steps


def test_irrelevant_large_output_scenario_honest_metrics() -> None:
    for strategy in ("keep_recent_summary", "semantic_state"):
        result = run_scenario(_scenario("irrelevant_large_output"), strategy)
        assert result.report.compaction_count >= 1
        # Both strategies keep the verbatim blob (recent tail / artifact
        # contract), so zero recovery is recorded honestly, never hidden.
        assert result.report.compaction_efficiency == 0.0
        assert all(check.passed for check in result.checks)


def test_critical_early_decision_is_the_documented_differentiator() -> None:
    semantic = run_scenario(_scenario("critical_early_decision"), "semantic_state")
    keep = run_scenario(_scenario("critical_early_decision"), "keep_recent_summary")
    assert all(check.passed for check in semantic.checks)  # state carries the decision
    decision_check = next(c for c in keep.checks if c.code == "decision_survives_compaction")
    assert decision_check.passed is False  # summary keeps task + tool result only


def test_repeated_failed_tool_scenario_records_failures_honestly() -> None:
    for strategy in ("keep_recent_summary", "semantic_state"):
        result = run_scenario(_scenario("repeated_failed_tool"), strategy)
        assert result.report.tool_call_count == 3
        assert result.report.tool_success_rate == 0.0
        assert result.report.compaction_count == 0
        assert all(check.passed for check in result.checks)


def test_suite_reports_failures_instead_of_hiding_them() -> None:
    report = run_suite()
    assert [(f.scenario, f.strategy, f.code) for f in report.failures] == [
        ("critical_early_decision", "keep_recent_summary", "decision_survives_compaction"),
    ]


def test_suite_is_byte_for_byte_stable() -> None:
    first = json.dumps(run_suite().model_dump(mode="json"), sort_keys=True)
    second = json.dumps(run_suite().model_dump(mode="json"), sort_keys=True)
    assert first == second


def test_suite_aggregates_report_raw_metrics_before_composite() -> None:
    report = run_suite()
    assert len(report.aggregates) == 2
    for aggregate in report.aggregates:
        raw = [
            aggregate.mean_task_completed,
            aggregate.mean_tool_success_rate,
            aggregate.mean_context_efficiency,
            aggregate.mean_step_efficiency,
            aggregate.mean_compaction_efficiency,
        ]
        assert all(0.0 <= value <= 1.0 for value in raw)
        assert 0.0 <= aggregate.composite <= 1.0


def test_weight_sensitivity_reports_both_weight_sets() -> None:
    report = run_suite()
    assert [s.weights_name for s in report.sensitivity] == [
        "documented-v1",
        "reliability-heavy-v1",
    ]
    for entry in report.sensitivity:
        assert abs(sum(entry.weights.values()) - 1.0) < 1e-9
        assert set(entry.weights) == set(SCORE_WEIGHTS)
        best = max(entry.composites.values())
        worst = min(entry.composites.values())
        assert entry.composites[entry.winner] == best
        assert entry.score_delta == pytest.approx(best - worst, abs=1e-6)
    _documented, _alternate = report.sensitivity
    assert DOCUMENTED_WEIGHTS == dict(SCORE_WEIGHTS)
    assert ALTERNATE_WEIGHTS["task_completed"] > DOCUMENTED_WEIGHTS["task_completed"]


def test_documented_and_alternate_composites_match_the_aggregates() -> None:
    report = run_suite()
    documented = report.sensitivity[0]
    for aggregate in report.aggregates:
        assert documented.composites[aggregate.strategy] == aggregate.composite


def _scenario(name: str):  # type: ignore[no-untyped-def]
    return next(s for s in iter_scenarios() if s.name == name)

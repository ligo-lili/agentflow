"""Estimator auditability (review R5): formula anchors, metadata, comparability."""

from __future__ import annotations

import pytest

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.estimator import (
    DeterministicEstimator,
    EstimatorMismatchError,
    EstimatorUnavailableError,
    TiktokenEstimator,
)
from packages.context.fixture import AUDIT_FIXTURE_VERSION, AUDIT_TEXTS
from packages.context.manager import ContextManager
from packages.core.errors import AgentFlowError
from packages.core.provider import ModelMessage
from packages.evals.evaluator import EvaluationReport
from packages.experiments.compare import (
    StrategyComparison,
    compare_strategy_reports,
    run_benchmark,
)

# --- versioned audit fixtures: exact deterministic counts -------------------

def test_audit_fixture_version_is_stable() -> None:
    assert AUDIT_FIXTURE_VERSION == 1
    assert set(AUDIT_TEXTS) == {
        "empty", "ascii", "chinese", "json", "tool_schema",
    }


def test_audit_fixtures_follow_the_documented_formula_exactly() -> None:
    estimator = DeterministicEstimator()
    for name, text in AUDIT_TEXTS.items():
        expected = 0 if text == "" else max(1, (len(text) + 3) // 4)
        assert estimator.count(text) == expected, name


def test_audit_fixture_counts_are_anchored_to_literals() -> None:
    """Literal anchors recorded in Evidence: any formula drift on any
    supported Python version or platform fails here."""
    estimator = DeterministicEstimator()
    assert estimator.count(AUDIT_TEXTS["empty"]) == 0
    assert estimator.count(AUDIT_TEXTS["ascii"]) == 18
    assert estimator.count(AUDIT_TEXTS["chinese"]) == 7
    assert estimator.count(AUDIT_TEXTS["json"]) == 19
    assert estimator.count(AUDIT_TEXTS["tool_schema"]) == 39


def test_audit_fixtures_are_repetitively_stable() -> None:
    estimator = DeterministicEstimator()
    first = {name: estimator.count(text) for name, text in AUDIT_TEXTS.items()}
    second = {name: estimator.count(text) for name, text in AUDIT_TEXTS.items()}
    assert first == second
    fresh = DeterministicEstimator()
    assert all(fresh.count(t) == first[n] for n, t in AUDIT_TEXTS.items())


# --- estimator metadata provenance ------------------------------------------

def test_deterministic_estimator_records_formula_metadata() -> None:
    estimator = DeterministicEstimator()
    assert estimator.metadata["formula"] == "0 if text == '' else max(1, ceil(len(text) / 4))"
    assert estimator.metadata["counting_unit"] == "python_characters"


def test_tiktoken_estimator_records_encoding_and_model_metadata() -> None:
    try:
        estimator = TiktokenEstimator()
    except EstimatorUnavailableError:
        pytest.skip("tiktoken or its encoding files are unavailable in this environment")
    assert estimator.metadata["encoding"] == "cl100k_base"
    assert estimator.metadata["model"] == "unspecified"
    assert estimator.name == "tiktoken-cl100k_base"

    hinted = TiktokenEstimator(model="fake-model")
    assert hinted.metadata["model"] == "fake-model"
    assert hinted.name == "tiktoken-cl100k_base:fake-model"


def test_context_snapshot_records_estimator_metadata() -> None:
    estimator = DeterministicEstimator()
    manager = ContextManager(estimator, TokenBudgetManager(BudgetConfig()))
    build = manager.build(
        session_id="s-meta",
        trace_id="tr-meta",
        step=1,
        messages=(ModelMessage(role="user", content="hello"),),
    )
    assert build.snapshot.metadata["estimator_metadata"] == estimator.metadata
    assert build.snapshot.estimator == "deterministic-v1"


# --- comparison rejects estimator mismatch ----------------------------------

class _FakeEstimator:
    """A different deterministic counting rule (offline, no tiktoken)."""

    @property
    def name(self) -> str:
        return "fake-estimator-v1"

    def count(self, text: str) -> int:
        return len(text)


def test_comparison_discloses_the_shared_estimator() -> None:
    comparison = compare_strategy_reports(
        tuple(
            StrategyComparison(
                strategy=strategy,
                report=run_benchmark(strategy, session_id=f"audit-{strategy}"),
                estimator="deterministic-v1",
            )
            for strategy in ("keep_recent_summary", "semantic_state")
        )
    )
    assert comparison.estimator == "deterministic-v1"


def test_comparison_rejects_mixed_estimator_identities() -> None:
    entries = (
        StrategyComparison(
            strategy="keep_recent_summary",
            report=run_benchmark("keep_recent_summary", session_id="mixed-a"),
            estimator="deterministic-v1",
        ),
        StrategyComparison(
            strategy="semantic_state",
            report=run_benchmark("semantic_state", session_id="mixed-b"),
            estimator="tiktoken-cl100k_base",
        ),
    )
    with pytest.raises(EstimatorMismatchError) as exc_info:
        compare_strategy_reports(entries)
    assert isinstance(exc_info.value, AgentFlowError)
    assert "deterministic-v1" in str(exc_info.value)
    assert "tiktoken-cl100k_base" in str(exc_info.value)


def test_benchmark_can_run_under_an_injected_estimator() -> None:
    report: EvaluationReport = run_benchmark(
        "semantic_state", session_id="injected-est", estimator=_FakeEstimator()
    )
    assert report.task_completed == 1

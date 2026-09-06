"""Estimator and budget contracts: documented formulas, breakdown invariants."""

from __future__ import annotations

import pytest

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.estimator import (
    DeterministicEstimator,
    EstimatorUnavailableError,
    TiktokenEstimator,
    create_estimator,
)


def test_deterministic_estimator_follows_the_documented_formula() -> None:
    estimator = DeterministicEstimator()
    assert estimator.name == "deterministic-v1"
    assert estimator.count("") == 0
    assert estimator.count("abcd") == 1  # 4 chars -> 1 token
    assert estimator.count("abcde") == 2  # 5 chars -> ceil(5/4)
    assert estimator.count("a" * 400) == 100  # exactly 400 chars -> 100 tokens
    assert estimator.count("a") == 1  # minimum 1 for non-empty text


def test_deterministic_estimator_counts_unicode_by_characters() -> None:
    estimator = DeterministicEstimator()
    assert estimator.count("你好世界") == 1  # 4 characters, not bytes
    assert estimator.count("你" * 8) == 2


def test_tiktoken_estimator_when_available() -> None:
    try:
        estimator = TiktokenEstimator()
    except EstimatorUnavailableError:
        pytest.skip("tiktoken or its encoding files are unavailable in this environment")
    assert estimator.name == "tiktoken-cl100k_base"
    sample = "AgentFlow makes context inspectable."
    assert estimator.count(sample) > 0
    # tiktoken shares no leading whitespace: distinct texts differ sensibly.
    assert estimator.count(sample) != estimator.count(sample + sample)


def test_create_estimator_selects_by_name() -> None:
    assert isinstance(create_estimator("deterministic"), DeterministicEstimator)
    auto = create_estimator("auto")
    assert isinstance(auto, (DeterministicEstimator, TiktokenEstimator))
    with pytest.raises(ValueError, match="unknown estimator preference"):
        create_estimator("bogus")


def test_budget_config_rejects_reservation_that_eats_the_whole_budget() -> None:
    with pytest.raises(ValueError, match="reserved_output_tokens must be smaller"):
        BudgetConfig(max_context_tokens=100, reserved_output_tokens=100)


def test_budget_report_breakdown_sums_to_total() -> None:
    manager = TokenBudgetManager(
        BudgetConfig(max_context_tokens=4096, reserved_output_tokens=256)
    )
    report = manager.evaluate({"system": 10, "user": 30, "tool": 60})
    assert report.total_tokens == sum(report.component_tokens.values()) == 100
    assert report.limit == 4096 - 256
    assert report.reserved_output_tokens == 256


def test_budget_fits_and_over_budget_verdicts_with_explicit_reason() -> None:
    manager = TokenBudgetManager(
        BudgetConfig(max_context_tokens=100, reserved_output_tokens=20)
    )
    fits = manager.evaluate({"system": 40, "user": 40})
    assert fits.fits is True
    assert fits.reason == "within_budget total=80 limit=80"

    over = manager.evaluate({"system": 50, "user": 40, "tool": 11})
    assert over.fits is False
    assert over.total_tokens == 101
    assert over.reason == "over_budget total=101 limit=80 overage=21"

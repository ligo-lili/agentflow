"""Experiments: A/B comparison of context strategies over the canonical benchmark (T7)."""

from packages.experiments.compare import (
    CANONICAL_BASELINE_STEPS,
    CANONICAL_BUDGET,
    CANONICAL_STRATEGIES,
    ComparisonReport,
    StrategyComparison,
    build_benchmark_session,
    compare_strategies,
    run_benchmark,
)

__all__ = [
    "CANONICAL_BASELINE_STEPS",
    "CANONICAL_BUDGET",
    "CANONICAL_STRATEGIES",
    "ComparisonReport",
    "StrategyComparison",
    "build_benchmark_session",
    "compare_strategies",
    "run_benchmark",
]

"""Evaluation: rule-based metrics and scoring over recorded sessions (T7)."""

from packages.evals.evaluator import (
    DEFAULT_BASELINE_STEPS,
    DEFAULT_BUDGET_LIMIT,
    EvaluationConfig,
    EvaluationReport,
    Evaluator,
    default_task_completed,
    score_of,
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

__all__ = [
    "DEFAULT_BASELINE_STEPS",
    "DEFAULT_BUDGET_LIMIT",
    "SCORE_WEIGHTS",
    "EvaluationConfig",
    "EvaluationReport",
    "Evaluator",
    "clamp01",
    "compaction_efficiency",
    "compaction_recovery",
    "composite_score",
    "context_efficiency",
    "default_task_completed",
    "score_of",
    "step_efficiency",
    "tool_success_rate",
]

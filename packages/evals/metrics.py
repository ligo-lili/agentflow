"""Metric normalizations for trajectory evaluation (rule-based, no LLM judge).

Every function here is a named, documented rule so scores are explainable and
reproducible (docs/architecture/evaluation.md). Denominator edge cases
(zero tool calls, zero compactions) have explicit, tested behavior instead
of silent defaults.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: Weights of the documented composite score; they must always sum to 1.0.
SCORE_WEIGHTS: dict[str, float] = {
    "task_completed": 0.35,
    "tool_success_rate": 0.20,
    "context_efficiency": 0.20,
    "step_efficiency": 0.15,
    "compaction_efficiency": 0.10,
}


def clamp01(value: float) -> float:
    """Clamp ``value`` into [0, 1]."""
    return max(0.0, min(1.0, value))


def tool_success_rate(total_tool_calls: int, successful_tool_calls: int) -> float:
    """successful / total; a run with no tool calls is not penalized (1.0)."""
    if total_tool_calls <= 0:
        return 1.0
    return clamp01(successful_tool_calls / total_tool_calls)


def step_efficiency(execution_steps: int, baseline_steps: int) -> float:
    """baseline / steps, capped at 1; more steps than the baseline lose score.

    ``execution_steps`` = LLM calls + tool calls. ``baseline_steps`` is the
    documented reference plan length for the benchmark (a correct run cannot
    do better). Zero steps are neutral (1.0) — nothing was executed.
    """
    if execution_steps <= 0:
        return 1.0
    if baseline_steps <= 0:
        return 1.0
    return clamp01(baseline_steps / execution_steps)


def context_efficiency(peak_context_tokens: int, budget_limit: int) -> float:
    """Share of the budget left at the peak: 1 - peak/limit, clamped.

    Lower peak context scores higher. Runs without snapshots (peak 0) or
    without a known limit are neutral (1.0).
    """
    if peak_context_tokens <= 0 or budget_limit <= 0:
        return 1.0
    return clamp01(1.0 - peak_context_tokens / budget_limit)


def compaction_recovery(before_tokens: int, after_tokens: int) -> float:
    """Fractional token reduction of one compaction, clamped to [0, 1].

    A compaction that grew the context scores 0 (recorded honestly, never
    negative); a compaction with no recorded ``before`` scores 0.
    """
    if before_tokens <= 0:
        return 0.0
    return clamp01((before_tokens - after_tokens) / before_tokens)


def compaction_efficiency(recoveries: Sequence[float]) -> float:
    """Mean recovery across compactions; no compaction is neutral (1.0).

    A run that never needed compaction is not penalized for it.
    """
    if not recoveries:
        return 1.0
    return clamp01(sum(recoveries) / len(recoveries))


def composite_score(
    task_completed: int,
    tool_success: float,
    context_eff: float,
    step_eff: float,
    compaction_eff: float,
    weights: Mapping[str, float] | None = None,
) -> float:
    """The documented weighted score:

    0.35 * task_completed + 0.20 * tool_success_rate
    + 0.20 * context_efficiency + 0.15 * step_efficiency
    + 0.10 * compaction_efficiency

    ``weights`` overrides the documented set (used for the review-R6
    sensitivity comparison). An override must contain exactly the five
    metric keys and sum to 1.0.
    """
    active = SCORE_WEIGHTS if weights is None else dict(weights)
    if set(active) != set(SCORE_WEIGHTS):
        raise ValueError(
            f"weights must cover exactly {sorted(SCORE_WEIGHTS)}, got {sorted(active)}"
        )
    if abs(sum(active.values()) - 1.0) > 1e-9:
        raise ValueError(f"weights must sum to 1.0, got {sum(active.values())}")
    weighted = (
        active["task_completed"] * task_completed
        + active["tool_success_rate"] * tool_success
        + active["context_efficiency"] * context_eff
        + active["step_efficiency"] * step_eff
        + active["compaction_efficiency"] * compaction_eff
    )
    return clamp01(weighted)

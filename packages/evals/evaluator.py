"""Evaluator: turn one recorded session into metrics and a composite score.

Evaluation consumes only the replayed session (events + snapshots read back
from stores) — it never touches the runtime and never executes a model or
tool (docs/architecture/overview.md).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.evals.metrics import (
    compaction_efficiency,
    compaction_recovery,
    composite_score,
    context_efficiency,
    step_efficiency,
    tool_success_rate,
)
from packages.observability.replay import SessionReplay

#: Default reference plan length: 2 LLM calls + 1 tool call.
DEFAULT_BASELINE_STEPS = 3

#: Budget assumed when no snapshot recorded a limit.
DEFAULT_BUDGET_LIMIT = 4096


class EvaluationConfig(BaseModel):
    """Frozen evaluator settings (documented defaults)."""

    model_config = ConfigDict(frozen=True)

    baseline_steps: int = Field(default=DEFAULT_BASELINE_STEPS, ge=1)
    budget_limit: int | None = Field(default=None, ge=1)


class EvaluationReport(BaseModel):
    """Metrics + score for one recorded session."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    status: str
    task_completed: int  # 0 or 1
    tool_call_count: int
    successful_tool_calls: int
    tool_success_rate: float
    execution_steps: int
    llm_calls: int
    peak_context_tokens: int
    final_context_tokens: int
    compaction_count: int
    context_efficiency: float
    step_efficiency: float
    compaction_efficiency: float
    score: float


def default_task_completed(replay: SessionReplay) -> int:
    """Named default rule: finished with a non-empty answer counts as done."""
    return int(replay.status == "finished" and bool(replay.final_answer))


class Evaluator:
    """Evaluates one ``SessionReplay`` into an :class:`EvaluationReport`."""

    def __init__(self, config: EvaluationConfig | None = None) -> None:
        self._config = config or EvaluationConfig()

    @property
    def config(self) -> EvaluationConfig:
        return self._config

    def evaluate(
        self,
        replay: SessionReplay,
        task_check: Callable[[SessionReplay], int] | None = None,
    ) -> EvaluationReport:
        check = task_check or default_task_completed
        budget_limit = self._resolve_budget_limit(replay)

        llm_calls = len(replay.steps)
        tool_calls: list[Any] = [c for step in replay.steps for c in step.tool_calls]
        tool_call_count = len(tool_calls)
        successful = sum(1 for c in tool_calls if c.ok is True)

        tokens = [
            step.context_total_tokens
            for step in replay.steps
            if step.context_total_tokens is not None
        ]
        peak = max(tokens, default=0)
        final = tokens[-1] if tokens else 0

        recoveries = [
            compaction_recovery(c.before_tokens, c.after_tokens or 0)
            for c in replay.compactions
        ]

        tool_rate = tool_success_rate(tool_call_count, successful)
        steps_total = llm_calls + tool_call_count
        context_eff = context_efficiency(peak, budget_limit)
        step_eff = step_efficiency(steps_total, self._config.baseline_steps)
        comp_eff = compaction_efficiency(recoveries)
        task_completed = check(replay)

        return EvaluationReport(
            session_id=replay.session_id,
            status=replay.status,
            task_completed=task_completed,
            tool_call_count=tool_call_count,
            successful_tool_calls=successful,
            tool_success_rate=tool_rate,
            execution_steps=steps_total,
            llm_calls=llm_calls,
            peak_context_tokens=peak,
            final_context_tokens=final,
            compaction_count=len(replay.compactions),
            context_efficiency=context_eff,
            step_efficiency=step_eff,
            compaction_efficiency=comp_eff,
            score=composite_score(task_completed, tool_rate, context_eff, step_eff, comp_eff),
        )

    def _resolve_budget_limit(self, replay: SessionReplay) -> int:
        if self._config.budget_limit is not None:
            return self._config.budget_limit
        for step in replay.steps:
            snapshot = step.context_snapshot
            if snapshot is not None and snapshot.budget_limit is not None:
                return int(snapshot.budget_limit)
        return DEFAULT_BUDGET_LIMIT


def score_of(replay: SessionReplay, **config: Any) -> float:
    """Convenience one-shot scoring for quick comparisons."""
    return Evaluator(EvaluationConfig(**config)).evaluate(replay).score

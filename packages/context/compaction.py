"""Compaction: threshold-triggered, deterministic context reduction (ADR-004).

Two strategies, both fully deterministic (no LLM calls):

``keep_recent_summary``
    Keeps every non-generated system message plus the most recent N non-system
    messages and replaces everything older with one summary message rendered
    from a fixed template (removed counts by role, earliest task, latest tool
    result preview — first 80 characters).

``semantic_state``
    Replaces all non-system messages with one structured state message whose
    fields are extracted by documented rules: goal (first user message),
    current_state (last non-empty assistant message), decisions (earlier
    non-empty assistant messages), artifacts (distinct values of successful
    tool results, first-seen order), tool_findings (``name=artifact#k``
    references into artifacts), pending_tasks (user follow-up messages after
    the first).

Superseding rule: system messages produced by a previous compaction
(``compaction_summary`` / ``semantic_state``) are removed by the next one —
the newest summary replaces the older ones instead of accumulating.

Message ids are positional (``msg-0000``...) over the transcript *at
compaction time*; they identify removed messages within one compaction
result. Trigger, before/after budget and preserved state are always recorded.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.context.budget import BudgetReport, TokenBudgetManager
from packages.context.estimator import TokenEstimator
from packages.core.provider import ModelMessage

StrategyName = Literal["keep_recent_summary", "semantic_state"]

_NO_TOOL_RESULT = "none"
_EMPTY_STATE = "in_progress"
_GENERATED_NAMES = frozenset({"compaction_summary", "semantic_state"})
_PREVIEW_LIMIT = 80


def preview(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    """Deterministic truncation used inside summaries (documented rule)."""
    return text if len(text) <= limit else text[:limit] + "…"


class CompactionConfig(BaseModel):
    """Frozen compaction settings; the strategy name travels with the result."""

    model_config = ConfigDict(frozen=True)

    strategy: StrategyName = "keep_recent_summary"
    threshold_ratio: float = Field(default=0.8, gt=0, le=1)
    keep_last_messages: int = Field(default=6, ge=0)


class CompactionDecision(BaseModel):
    """Threshold verdict with an explicit, number-bearing reason."""

    model_config = ConfigDict(frozen=True)

    should: bool
    reason: str


class CompactionResult(BaseModel):
    """Structured record of one compaction run (ADR-004)."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    trigger_reason: str
    threshold: int
    limit: int
    before_tokens: int
    after_tokens: int
    removed_ids: tuple[str, ...]
    kept_ids: tuple[str, ...]
    messages: tuple[ModelMessage, ...]
    summary_message: ModelMessage | None
    preserved_state: dict[str, Any] = Field(default_factory=dict)
    fits_budget: bool


class CompactionEngine:
    """Compacts only when the budget threshold is reached; never speculatively."""

    def __init__(
        self,
        estimator: TokenEstimator,
        budget: TokenBudgetManager | None = None,
        config: CompactionConfig | None = None,
    ) -> None:
        self._estimator = estimator
        self._budget = budget or TokenBudgetManager()
        self._config = config or CompactionConfig()

    @property
    def config(self) -> CompactionConfig:
        return self._config

    def threshold_tokens(self) -> int:
        return int(self._budget.available_input_tokens * self._config.threshold_ratio)

    def should_compact(self, report: BudgetReport) -> CompactionDecision:
        threshold = self.threshold_tokens()
        limit = self._budget.available_input_tokens
        if report.total_tokens > threshold:
            reason = (
                f"threshold_exceeded total={report.total_tokens} "
                f"threshold={threshold} limit={limit} strategy={self._config.strategy}"
            )
            return CompactionDecision(should=True, reason=reason)
        return CompactionDecision(
            should=False,
            reason=f"within_threshold total={report.total_tokens} threshold={threshold}",
        )

    def compact(self, messages: tuple[ModelMessage, ...], trigger_reason: str) -> CompactionResult:
        ids = tuple(f"msg-{i:04d}" for i in range(len(messages)))
        before = self._tokens_of(messages)
        if self._config.strategy == "keep_recent_summary":
            result = self._keep_recent_summary(messages, ids, before, trigger_reason)
        else:
            result = self._semantic_state(messages, ids, before, trigger_reason)
        return result

    # -- strategy: keep_recent_summary -------------------------------------

    def _keep_recent_summary(
        self,
        messages: tuple[ModelMessage, ...],
        ids: tuple[str, ...],
        before: int,
        trigger_reason: str,
    ) -> CompactionResult:
        system_kept: list[tuple[int, ModelMessage]] = []
        generated: list[tuple[int, ModelMessage]] = []
        tail: list[tuple[int, ModelMessage]] = []
        for index, message in enumerate(messages):
            if message.role != "system":
                tail.append((index, message))
            elif message.name in _GENERATED_NAMES:
                generated.append((index, message))  # superseded by the new summary
            else:
                system_kept.append((index, message))

        keep_n = self._config.keep_last_messages
        removed_tail = tail[:-keep_n] if keep_n else tail
        kept_tail = tail[len(removed_tail):]
        removed = generated + removed_tail

        summary: ModelMessage | None = None
        new_messages = tuple(m for _, m in system_kept)
        if removed_tail:
            summary = ModelMessage(
                role="system",
                name="compaction_summary",
                content=self._summary_text([m for _, m in removed_tail]),
            )
            new_messages = new_messages + (summary,)
        new_messages = new_messages + tuple(m for _, m in kept_tail)

        preserved: dict[str, Any] = {
            "removed_role_counts": _role_counts(m for _, m in removed_tail),
            "kept_ids": [ids[i] for i, _ in system_kept + kept_tail],
        }
        return self._result(
            messages=new_messages,
            summary_message=summary,
            removed_ids=tuple(ids[i] for i, _ in removed),
            kept_ids=tuple(ids[i] for i, _ in system_kept + kept_tail),
            preserved_state=preserved,
            before=before,
            trigger_reason=trigger_reason,
        )

    def _summary_text(self, removed: list[ModelMessage]) -> str:
        users = [m.content for m in removed if m.role == "user"]
        first_task = users[0] if users else "unknown"
        last_tool = _NO_TOOL_RESULT
        for message in reversed(removed):
            if message.role == "tool":
                tool_value = _tool_value(message)
                if tool_value is not None:
                    last_tool = preview(tool_value)
                break
        return (
            f"[Context summary] {len(removed)} earlier messages removed "
            f"({_role_text(removed)}); earliest task: {first_task}; "
            f"latest tool result: {last_tool}."
        )

    # -- strategy: semantic_state ------------------------------------------

    def _semantic_state(
        self,
        messages: tuple[ModelMessage, ...],
        ids: tuple[str, ...],
        before: int,
        trigger_reason: str,
    ) -> CompactionResult:
        users = [m.content for m in messages if m.role == "user"]
        assistants = [m.content for m in messages if m.role == "assistant" and m.content]
        current_state = assistants[-1] if assistants else _EMPTY_STATE
        decisions = assistants[:-1]

        tool_findings: list[str] = []
        artifacts: list[str] = []
        artifact_index: dict[str, int] = {}
        for message in messages:
            if message.role != "tool":
                continue
            value = _tool_value(message)
            if value is None:
                continue
            if value not in artifact_index:
                artifact_index[value] = len(artifacts)
                artifacts.append(value)  # distinct values, first-seen order
            tool_findings.append(f"{message.name}=artifact#{artifact_index[value]}")

        state: dict[str, Any] = {
            "goal": users[0] if users else "",
            "current_state": current_state,
            "decisions": decisions,
            "artifacts": artifacts,
            "tool_findings": tool_findings,
            "pending_tasks": users[1:],
        }
        state_message = ModelMessage(
            role="system",
            name="semantic_state",
            content=self._render_state(state),
        )

        system_kept_ids = tuple(
            ids[i]
            for i, m in enumerate(messages)
            if m.role == "system" and m.name not in _GENERATED_NAMES
        )
        removed_ids = tuple(
            ids[i]
            for i, m in enumerate(messages)
            if m.role != "system" or m.name in _GENERATED_NAMES
        )
        system_messages = tuple(
            m for m in messages if m.role == "system" and m.name not in _GENERATED_NAMES
        )

        return self._result(
            messages=system_messages + (state_message,),
            summary_message=state_message,
            removed_ids=removed_ids,
            kept_ids=system_kept_ids,
            preserved_state=state,
            before=before,
            trigger_reason=trigger_reason,
        )

    def _render_state(self, state: dict[str, Any]) -> str:
        lines = ["[Conversation state]"]
        for key, value in state.items():
            rendered = "; ".join(value) if isinstance(value, list) else str(value)
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines)

    # -- shared -------------------------------------------------------------

    def _result(
        self,
        messages: tuple[ModelMessage, ...],
        summary_message: ModelMessage | None,
        removed_ids: tuple[str, ...],
        kept_ids: tuple[str, ...],
        preserved_state: dict[str, Any],
        before: int,
        trigger_reason: str,
    ) -> CompactionResult:
        after = self._tokens_of(messages)
        limit = self._budget.available_input_tokens
        return CompactionResult(
            strategy=self._config.strategy,
            trigger_reason=trigger_reason,
            threshold=self.threshold_tokens(),
            limit=limit,
            before_tokens=before,
            after_tokens=after,
            removed_ids=removed_ids,
            kept_ids=kept_ids,
            messages=messages,
            summary_message=summary_message,
            preserved_state=preserved_state,
            fits_budget=after <= limit,
        )

    def _tokens_of(self, messages: tuple[ModelMessage, ...]) -> int:
        return sum(self._estimator.count(f"{m.role}: {m.content}") for m in messages)


def _role_counts(messages: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        counts[message.role] = counts.get(message.role, 0) + 1
    return counts


def _role_text(messages: list[ModelMessage]) -> str:
    return ", ".join(f"{role}={count}" for role, count in sorted(_role_counts(messages).items()))


def _tool_value(message: ModelMessage) -> str | None:
    """Extract the value of a successful tool observation (documented rule)."""
    try:
        parsed = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and parsed.get("ok") is True:
        return str(parsed.get("value", ""))
    return None

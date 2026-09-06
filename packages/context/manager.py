"""ContextManager: one ContextSnapshot before every model call.

Each build counts every message, groups the counts by role (the component
breakdown), evaluates the token budget and returns the snapshot plus the
budget verdict. When a snapshot store is wired, the snapshot is persisted
immediately — this is the artifact replay and evaluation will consume.
When a CompactionEngine is wired, the threshold decision runs here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.context.budget import BudgetReport, TokenBudgetManager
from packages.context.compaction import CompactionEngine, CompactionResult
from packages.context.estimator import TokenEstimator
from packages.core.ids import utc_now
from packages.core.provider import ModelMessage
from packages.core.snapshots import ContextSnapshot
from packages.core.stores import SnapshotStore


class ContextBuild(BaseModel):
    """The snapshot plus the budget verdict for one model call."""

    model_config = ConfigDict(frozen=True)

    snapshot: ContextSnapshot
    report: BudgetReport
    compaction: CompactionResult | None = None


class ContextManager:
    """Builds (and optionally persists) one ContextSnapshot per model call.

    When a ``CompactionEngine`` is wired, the manager consults it on every
    build: once the threshold is reached, the engine compacts the transcript
    and the snapshot records the compacted messages and strategy name.
    """

    def __init__(
        self,
        estimator: TokenEstimator,
        budget: TokenBudgetManager | None = None,
        snapshot_store: SnapshotStore | None = None,
        compaction_engine: CompactionEngine | None = None,
    ) -> None:
        self._estimator = estimator
        self._budget = budget or TokenBudgetManager()
        self._store = snapshot_store
        self._engine = compaction_engine
        self._sequence = 0

    @property
    def budget(self) -> TokenBudgetManager:
        return self._budget

    def build(
        self,
        session_id: str,
        trace_id: str,
        step: int,
        messages: tuple[ModelMessage, ...],
    ) -> ContextBuild:
        component_tokens = count_by_role(messages, self._estimator)
        report = self._budget.evaluate(component_tokens)

        compaction: CompactionResult | None = None
        effective_messages = messages
        if self._engine is not None:
            decision = self._engine.should_compact(report)
            if decision.should:
                compaction = self._engine.compact(messages, decision.reason)
                effective_messages = compaction.messages

        metadata: dict[str, Any] = {"step": step}
        if compaction is not None:
            metadata["compaction"] = {
                "strategy": compaction.strategy,
                "removed_count": len(compaction.removed_ids),
                "before_tokens": compaction.before_tokens,
                "after_tokens": compaction.after_tokens,
            }

        # The snapshot documents what the model will actually see: after a
        # compaction that is the compacted transcript, so its breakdown and
        # total are recomputed on the effective messages (the pre-compaction
        # verdict that triggered the compaction stays in ``report`` and
        # ``metadata``).
        if compaction is not None:
            effective_tokens = count_by_role(effective_messages, self._estimator)
        else:
            effective_tokens = report.component_tokens

        snapshot = ContextSnapshot(
            snapshot_id=f"{session_id}-ctx-{self._sequence:04d}",
            session_id=session_id,
            trace_id=trace_id,
            sequence=self._sequence,
            created_at=utc_now(),
            messages=effective_messages,
            component_tokens=effective_tokens,
            total_tokens=sum(effective_tokens.values()),
            reserved_output_tokens=report.reserved_output_tokens,
            budget_limit=report.limit,
            compaction_state="none" if compaction is None else compaction.strategy,
            estimator=self._estimator.name,
            metadata=metadata,
        )
        self._sequence += 1
        if self._store is not None:
            self._store.save_context_snapshot(snapshot)
        return ContextBuild(snapshot=snapshot, report=report, compaction=compaction)


def count_by_role(messages: tuple[ModelMessage, ...], estimator: TokenEstimator) -> dict[str, int]:
    """Per-role token counts; each message is counted as ``role: content``."""
    counts: dict[str, int] = {}
    for message in messages:
        key = message.role
        counts[key] = counts.get(key, 0) + estimator.count(f"{message.role}: {message.content}")
    return counts


def total_tokens_of(component_tokens: Mapping[str, int]) -> int:
    """The breakdown-sum invariant in one place: total is the sum of parts."""
    return sum(component_tokens.values())

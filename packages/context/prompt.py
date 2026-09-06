"""PromptBuilder: assembles PromptSnapshots in the frozen section order.

Sections are first-class inputs (``PromptSections``), never hidden
``messages.append`` calls. Empty sections are omitted; the surviving
sections keep the contract order from ``PROMPT_SECTION_ORDER``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from packages.context.estimator import TokenEstimator
from packages.core.ids import utc_now
from packages.core.provider import ModelMessage
from packages.core.snapshots import PROMPT_SECTION_ORDER, PromptSection, PromptSnapshot
from packages.core.stores import SnapshotStore


class PromptSections(BaseModel):
    """Structured inputs for one prompt assembly; ``None``/empty = omitted."""

    model_config = ConfigDict(frozen=True)

    base_system: str | None = None
    agent_role: str | None = None
    project_context: str | None = None
    relevant_memory: str | None = None
    relevant_skills: str | None = None
    current_task: str | None = None
    runtime_context: str | None = None
    recent_messages: tuple[ModelMessage, ...] = ()
    tool_results: str | None = None


def _render_messages(messages: tuple[ModelMessage, ...]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


class PromptBuilder:
    """Builds one PromptSnapshot per call, persisting it when a store is wired."""

    def __init__(
        self,
        estimator: TokenEstimator,
        snapshot_store: SnapshotStore | None = None,
    ) -> None:
        self._estimator = estimator
        self._store = snapshot_store
        self._sequence = 0

    def build(
        self,
        sections: PromptSections,
        session_id: str,
        trace_id: str,
    ) -> PromptSnapshot:
        rendered: dict[PromptSection, str] = {}
        for section in PROMPT_SECTION_ORDER:
            value = getattr(sections, section.value)
            if section is PromptSection.RECENT_MESSAGES:
                if sections.recent_messages:
                    rendered[section] = _render_messages(sections.recent_messages)
            elif isinstance(value, str) and value:
                rendered[section] = value

        token_counts = {s.value: self._estimator.count(text) for s, text in rendered.items()}
        snapshot = PromptSnapshot(
            snapshot_id=f"{session_id}-prompt-{self._sequence:04d}",
            session_id=session_id,
            trace_id=trace_id,
            sequence=self._sequence,
            created_at=utc_now(),
            sections=tuple(rendered.keys()),
            section_content=rendered,
            estimator=self._estimator.name,
            token_counts=token_counts,
            total_tokens=sum(token_counts.values()),
        )
        self._sequence += 1
        if self._store is not None:
            self._store.save_prompt_snapshot(snapshot)
        return snapshot

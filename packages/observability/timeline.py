"""Session timeline: a readable, ordered view over the event log.

The timeline is a pure query over the event store — it never reads runtime
internal state and never executes anything. Entries are ordered by sequence,
one per event, with a short human-readable summary.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.errors import AgentFlowError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.stores import EventStore


class UnknownSessionError(AgentFlowError):
    """Raised when a session id has no events in the queried store."""


class TimelineEntry(BaseModel):
    """One event rendered for inspection."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: str
    step: int | None
    summary: str
    payload: dict[str, Any]


class SessionTimeline(BaseModel):
    """The full ordered timeline of one session."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    entries: tuple[TimelineEntry, ...]
    event_counts: dict[str, int]


class SessionTimelineBuilder:
    """Builds a :class:`SessionTimeline` from an event store."""

    def __init__(self, event_store: EventStore) -> None:
        self._store = event_store

    def build(self, session_id: str) -> SessionTimeline:
        events = self._store.get_session_events(session_id)
        if not events:
            raise UnknownSessionError(f"no events recorded for session {session_id!r}")
        entries = tuple(_entry(event) for event in events)
        counts = Counter(e.event_type for e in events)
        return SessionTimeline(
            session_id=session_id,
            entries=entries,
            event_counts=dict(sorted(counts.items())),
        )


def _entry(event: AgentEvent) -> TimelineEntry:
    return TimelineEntry(
        sequence=event.sequence,
        event_type=event.event_type.value,
        step=_step_of(event),
        summary=_summary(event),
        payload=dict(event.payload),
    )


def _step_of(event: AgentEvent) -> int | None:
    step = event.payload.get("step")
    return int(step) if isinstance(step, int) else None


_PREVIEW_LIMIT = 60


def _preview(value: Any, limit: int = _PREVIEW_LIMIT) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _summary(event: AgentEvent) -> str:
    payload = event.payload
    event_type = event.event_type
    if event_type == AgentEventType.SESSION_STARTED:
        return f"task={payload.get('task', '')!r} model={payload.get('model', '')}"
    if event_type == AgentEventType.PROMPT_BUILT:
        return f"sections={len(payload.get('sections', []))} total_tokens={payload.get('total_tokens')}"
    if event_type == AgentEventType.CONTEXT_BUILT:
        return f"total_tokens={payload.get('total_tokens')} fits={payload.get('fits')}"
    if event_type == AgentEventType.LLM_CALL_STARTED:
        return f"model={payload.get('model')} messages={payload.get('message_count')}"
    if event_type == AgentEventType.LLM_CALL_FINISHED:
        return f"finish_reason={payload.get('finish_reason')}"
    if event_type == AgentEventType.TOOL_CALL_STARTED:
        return f"name={payload.get('name')}"
    if event_type == AgentEventType.TOOL_CALL_FINISHED:
        if payload.get("ok") is True:
            return f"name={payload.get('name')} ok=True value={_preview(payload.get('value'))!r}"
        return f"name={payload.get('name')} ok=False error={_preview(payload.get('error'))!r}"
    if event_type == AgentEventType.CONTEXT_COMPACTION_STARTED:
        return f"strategy={payload.get('strategy')} trigger={payload.get('trigger')}"
    if event_type == AgentEventType.CONTEXT_COMPACTION_FINISHED:
        return (
            f"strategy={payload.get('strategy')} "
            f"tokens={payload.get('before_tokens')}→{payload.get('after_tokens')} "
            f"removed={len(payload.get('removed_ids', []))}"
        )
    if event_type == AgentEventType.AGENT_FINISHED:
        return f"answer={payload.get('answer')!r}"
    if event_type == AgentEventType.AGENT_FAILED:
        return f"phase={payload.get('phase')} error={payload.get('error')!r}"
    return ""

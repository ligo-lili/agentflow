"""Shared deterministic helpers for contract tests (no network, no API keys)."""

from __future__ import annotations

from datetime import UTC, datetime

from packages.core.events import AgentEvent, AgentEventType

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def make_event(
    event_type: AgentEventType = AgentEventType.SESSION_STARTED,
    sequence: int = 0,
    session_id: str = "s-1",
    trace_id: str = "tr-1",
    payload: dict | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=f"ev-{sequence:04d}",
        session_id=session_id,
        trace_id=trace_id,
        sequence=sequence,
        timestamp=FIXED_TIME,
        event_type=event_type,
        payload=payload if payload is not None else {},
    )

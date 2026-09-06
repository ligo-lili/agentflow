"""EventRecorder: the runtime's single emission point for lifecycle events.

The recorder owns per-session sequence allocation, derives stable event ids,
redacts payloads, and publishes through the EventBus. Nothing else in the
runtime constructs ``AgentEvent`` directly.
"""

from __future__ import annotations

from typing import Any

from packages.core.events import AgentEvent, AgentEventType
from packages.core.ids import utc_now
from packages.core.redaction import redact_payload
from packages.observability.eventbus import EventBus


class EventRecorder:
    """Allocates monotonic sequences and emits redacted events on the bus."""

    def __init__(self, bus: EventBus, session_id: str, trace_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self._trace_id = trace_id
        self._sequence = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def emit(self, event_type: AgentEventType, payload: dict[str, Any]) -> AgentEvent:
        event = AgentEvent(
            event_id=f"{self._session_id}-{self._sequence:04d}",
            session_id=self._session_id,
            trace_id=self._trace_id,
            sequence=self._sequence,
            timestamp=utc_now(),
            event_type=event_type,
            payload=redact_payload(payload),
        )
        self._sequence += 1
        self._bus.emit(event)
        return event

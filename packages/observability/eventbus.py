"""In-process event bus: fans lifecycle events out to subscribers and stores.

The bus is the only way producers emit events; consumers (UI, replay,
evaluation) subscribe to the bus or read a store, never the Agent Loop.
Sequence monotonicity is enforced here so ordering bugs surface immediately.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from packages.core.errors import EventOrderError
from packages.core.events import AgentEvent

EventHandler = Callable[[AgentEvent], None]


class EventBus:
    """Synchronous, in-process fan-out with per-session sequence validation."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._last_sequence: dict[str, int] = defaultdict(lambda: -1)

    def subscribe(self, handler: EventHandler) -> None:
        """Register an in-process handler; handlers run in subscription order."""
        self._handlers.append(handler)

    def emit(self, event: AgentEvent) -> None:
        """Validate ordering, then fan the event out to every subscriber.

        Handler exceptions propagate to the caller: emission failures are
        diagnosable, never silently swallowed.
        """
        expected = self._last_sequence[event.session_id] + 1
        if event.sequence != expected:
            raise EventOrderError(
                f"session {event.session_id!r}: expected sequence {expected}, "
                f"got {event.sequence}"
            )
        self._last_sequence[event.session_id] = event.sequence
        for handler in self._handlers:
            handler(event)

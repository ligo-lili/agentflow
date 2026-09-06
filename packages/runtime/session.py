"""AgentSession: wires stores to the bus, opens the session, drives the loop.

UI, replay and evaluation consume the events collected here (or read the
store); they never reach into the loop's internal state (ADR-002).
"""

from __future__ import annotations

from collections.abc import Sequence

from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.errors import AgentFlowError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.ids import new_id
from packages.core.provider import ModelProvider
from packages.core.stores import EventStore
from packages.core.tools import Tool
from packages.observability.eventbus import EventBus
from packages.observability.inmemory import InMemoryEventStore
from packages.runtime.loop import AgentLoop, AgentLoopConfig, AgentRunResult
from packages.runtime.recorder import EventRecorder
from packages.runtime.tools import ToolRuntime


class SessionAlreadyRunError(AgentFlowError):
    """Raised when run() is called twice on the same session."""


class AgentSession:
    """One agent run: session + trace identity, event wiring, bounded loop."""

    def __init__(
        self,
        task: str,
        provider: ModelProvider,
        tools: Sequence[Tool] | ToolRuntime | None = None,
        config: AgentLoopConfig | None = None,
        bus: EventBus | None = None,
        store: EventStore | None = None,
        session_id: str | None = None,
        prompt_builder: PromptBuilder | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._task = task
        self._provider = provider
        if isinstance(tools, ToolRuntime):
            self._tool_runtime = tools
        else:
            self._tool_runtime = ToolRuntime(tools or ())
        self._config = config or AgentLoopConfig()
        self._bus = bus or EventBus()
        self._store = store or InMemoryEventStore()
        self._bus.subscribe(self._store.append)
        self._session_id = session_id or new_id()
        self._trace_id = new_id()
        self._recorder = EventRecorder(self._bus, self._session_id, self._trace_id)
        self._prompt_builder = prompt_builder
        self._context_manager = context_manager
        self._run_result: AgentRunResult | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def store(self) -> EventStore:
        return self._store

    @property
    def run_result(self) -> AgentRunResult | None:
        return self._run_result

    def events(self) -> list[AgentEvent]:
        """All recorded events for this session, in sequence order."""
        return self._store.get_session_events(self._session_id)

    def run(self) -> AgentRunResult:
        if self._run_result is not None:
            raise SessionAlreadyRunError(
                f"session {self._session_id!r} has already been run; create a new session"
            )
        self._recorder.emit(
            AgentEventType.SESSION_STARTED,
            {
                "task": self._task,
                "model": self._config.model,
                "max_steps": self._config.max_steps,
            },
        )
        loop = AgentLoop(
            self._recorder,
            self._provider,
            self._tool_runtime,
            self._config,
            prompt_builder=self._prompt_builder,
            context_manager=self._context_manager,
        )
        self._run_result = loop.run(self._task)
        return self._run_result

"""Injectable timeout policy: wall-clock deadlines for provider and tool calls.

The runtime contract (docs/architecture/runtime.md) promises explicit timeout
handling. Deadlines are plain configuration values — this module never
imports a provider SDK, so the core runtime stays provider-agnostic. The
default enforcement runs the guarded call in a worker thread and abandons it
when the deadline expires; the AgentLoop converts a deadline breach into a
terminal ``AgentFailed`` event, so timed-out work can never be reported as
success. Abandoned threads cannot be interrupted; they are simply no longer
observed and their result is discarded.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

from packages.core.errors import AgentFlowError

T = TypeVar("T")

#: Timeout categories used in ``AgentFailed`` payloads.
PROVIDER_TIMEOUT = "provider"
TOOL_TIMEOUT = "tool"


class TimeoutExceededError(AgentFlowError):
    """A guarded provider or tool call exceeded its configured deadline."""

    def __init__(self, kind: str, target: str, timeout_seconds: float) -> None:
        self.kind = kind
        self.target = target
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"{kind} call {target!r} exceeded its {timeout_seconds:g} second deadline"
        )


class TimeoutPolicy:
    """Enforces provider and tool deadlines; ``None`` disables that guard."""

    def __init__(
        self,
        provider_timeout_seconds: float | None = None,
        tool_timeout_seconds: float | None = None,
    ) -> None:
        self.provider_timeout_seconds = provider_timeout_seconds
        self.tool_timeout_seconds = tool_timeout_seconds
        self._executor: concurrent.futures.ThreadPoolExecutor | None = (
            concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="agentflow-timeout"
            )
        )

    def close(self) -> None:
        """Release the worker pool; already-abandoned calls are not waited on."""
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def run_provider(self, target: str, call: Callable[[], T]) -> T:
        """Run one provider call under the provider deadline."""
        return self._guarded(PROVIDER_TIMEOUT, target, call, self.provider_timeout_seconds)

    def run_tool(self, target: str, call: Callable[[], T]) -> T:
        """Run one tool execution under the tool deadline."""
        return self._guarded(TOOL_TIMEOUT, target, call, self.tool_timeout_seconds)

    def _guarded(
        self,
        kind: str,
        target: str,
        call: Callable[[], T],
        timeout_seconds: float | None,
    ) -> T:
        if timeout_seconds is None:
            return call()
        executor = self._executor
        if executor is None:
            raise AgentFlowError("timeout policy has been closed")
        future = executor.submit(call)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            if future.done():
                # The call itself raised a TimeoutError-shaped exception just
                # before the deadline: re-raise the real failure instead of
                # misreporting it as a policy breach.
                raise
            future.cancel()  # cannot interrupt a running call; abandon it
            raise TimeoutExceededError(kind, target, timeout_seconds) from None

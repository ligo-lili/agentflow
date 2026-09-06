"""FakeModelProvider: the default offline, deterministic ModelProvider.

The fake provider executes a fixed script of responses (or injected
exceptions). It never touches the network and requires no API key, so tests
and demos are reproducible.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from packages.core.errors import AgentFlowError
from packages.core.provider import ModelRequest, ModelResponse


class ScriptExhaustedError(AgentFlowError):
    """Raised when the runtime makes more model calls than the script allows."""


ScriptEntry = ModelResponse | BaseException


class FakeModelProvider:
    """Scripted ModelProvider: each invoke consumes the next script entry."""

    def __init__(self, script: Sequence[ScriptEntry]) -> None:
        if not script:
            raise ValueError("FakeModelProvider requires a non-empty script")
        self._script: deque[ScriptEntry] = deque(script)
        self.calls: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not self._script:
            raise ScriptExhaustedError(
                f"script exhausted after {len(self.calls) - 1} call(s); "
                f"unexpected extra call to model {request.model!r}"
            )
        entry = self._script.popleft()
        if isinstance(entry, BaseException):
            raise entry
        return entry

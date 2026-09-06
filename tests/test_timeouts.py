"""Timeout contract: provider/tool deadlines are explicit, terminal, redacted."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import pytest

from packages.core.errors import AgentFlowError
from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelRequest, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession
from packages.runtime.timeouts import TimeoutPolicy

SLOW_SECONDS = 1.0
DEADLINE_SECONDS = 0.2


class SlowProvider:
    """Provider that blocks past any reasonable deadline before answering."""

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.calls = 0

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        time.sleep(self._delay)
        return ModelResponse(
            message=ModelMessage(role="assistant", content="late answer"),
            finish_reason="stop",
        )


class SlowTool:
    """Tool that blocks past any reasonable deadline before returning."""

    name = "slow_tool"
    description = "Sleeps before returning."

    def __init__(self, delay: float) -> None:
        self._delay = delay

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        time.sleep(self._delay)
        return ToolResult(name=self.name, ok=True, value="finished anyway")


def test_provider_timeout_fails_the_session_and_emits_timeout_category() -> None:
    session = AgentSession(
        task="slow provider",
        provider=SlowProvider(SLOW_SECONDS),
        config=AgentLoopConfig(provider_timeout_seconds=DEADLINE_SECONDS),
    )
    started = time.monotonic()
    result = session.run()
    elapsed = time.monotonic() - started

    assert result.status == "failed"
    assert result.steps == 1
    assert "deadline" in (result.error or "")
    assert elapsed < SLOW_SECONDS  # the loop did not wait for the provider
    failed = session.events()[-1]
    assert failed.event_type == AgentEventType.AGENT_FAILED
    assert failed.payload["phase"] == "provider"
    assert failed.payload["timeout"] == {
        "kind": "provider",
        "target": "fake-model",
        "timeout_seconds": DEADLINE_SECONDS,
    }
    assert not any(
        e.event_type == AgentEventType.AGENT_FINISHED for e in session.events()
    )


def test_tool_timeout_closes_the_tool_boundary_as_failed_and_fails_the_session() -> None:
    provider = FakeModelProvider(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(ToolCallRequest(call_id="c1", name="slow_tool", arguments={}),),
            ),
            ModelResponse(
                message=ModelMessage(role="assistant", content="never reached"),
                finish_reason="stop",
            ),
        ]
    )
    session = AgentSession(
        task="slow tool",
        provider=provider,
        tools=[SlowTool(SLOW_SECONDS)],
        config=AgentLoopConfig(tool_timeout_seconds=DEADLINE_SECONDS),
    )
    result = session.run()

    assert result.status == "failed"
    assert "deadline" in (result.error or "")
    types = [e.event_type for e in session.events()]
    assert types[-1] == AgentEventType.AGENT_FAILED
    assert AgentEventType.AGENT_FINISHED not in types  # never reported as success
    finished = [e for e in session.events() if e.event_type == AgentEventType.TOOL_CALL_FINISHED]
    assert len(finished) == 1
    assert finished[0].payload["ok"] is False
    assert finished[0].payload["value"] is None
    assert "deadline" in finished[0].payload["error"]
    assert finished[0].payload["timeout"]["kind"] == "tool"
    failed = session.events()[-1]
    assert failed.payload["phase"] == "tool"
    assert failed.payload["timeout"]["target"] == "slow_tool"


def test_timeout_values_are_visible_in_the_session_started_payload() -> None:
    session = AgentSession(
        task="config visibility",
        provider=FakeModelProvider(
            [
                ModelResponse(
                    message=ModelMessage(role="assistant", content="done"),
                    finish_reason="stop",
                )
            ]
        ),
        config=AgentLoopConfig(
            provider_timeout_seconds=1.5,
            tool_timeout_seconds=2.5,
        ),
    )
    session.run()
    payload = session.events()[0].payload
    assert payload["provider_timeout_seconds"] == 1.5
    assert payload["tool_timeout_seconds"] == 2.5


def test_timeouts_default_to_disabled() -> None:
    session = AgentSession(
        task="defaults",
        provider=FakeModelProvider(
            [
                ModelResponse(
                    message=ModelMessage(role="assistant", content="done"),
                    finish_reason="stop",
                )
            ]
        ),
    )
    session.run()
    payload = session.events()[0].payload
    assert payload["provider_timeout_seconds"] is None
    assert payload["tool_timeout_seconds"] is None


def test_provider_own_timeout_error_is_not_misreported_as_policy_breach() -> None:
    session = AgentSession(
        task="upstream timeout",
        provider=FakeModelProvider(
            [TimeoutError("upstream read timed out after 30s")]
        ),
        config=AgentLoopConfig(provider_timeout_seconds=DEADLINE_SECONDS),
    )
    result = session.run()

    assert result.status == "failed"
    failed = session.events()[-1]
    assert failed.payload["phase"] == "provider"
    # The failure came from the provider itself, not from the deadline guard.
    assert "timeout" not in failed.payload
    assert "upstream read timed out" in failed.payload["error"]


def test_tool_failure_with_timeouts_configured_keeps_loop_running() -> None:
    provider = FakeModelProvider(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(ToolCallRequest(call_id="c1", name="explode", arguments={}),),
            ),
            ModelResponse(
                message=ModelMessage(role="assistant", content="recovered"),
                finish_reason="stop",
            ),
        ]
    )

    class ExplodingTool:
        name = "explode"
        description = "Always fails."

        def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
            raise RuntimeError("tool exploded")

    session = AgentSession(
        task="tool error with timeouts on",
        provider=provider,
        tools=[ExplodingTool()],
        config=AgentLoopConfig(
            provider_timeout_seconds=5.0,
            tool_timeout_seconds=5.0,
        ),
    )
    result = session.run()
    assert result.status == "finished"
    assert result.answer == "recovered"


def test_timeout_policy_rejects_calls_after_close() -> None:
    policy = TimeoutPolicy(provider_timeout_seconds=1.0)
    policy.close()
    with pytest.raises(AgentFlowError, match="closed"):
        policy.run_provider("fake-model", lambda: "x")  # type: ignore[arg-type,return-value]
    policy.close()  # idempotent


def test_timeout_diagnostics_are_json_serializable_payloads() -> None:
    session = AgentSession(
        task="payload check",
        provider=SlowProvider(SLOW_SECONDS),
        config=AgentLoopConfig(provider_timeout_seconds=DEADLINE_SECONDS),
    )
    session.run()
    serialized = json.dumps([e.payload for e in session.events()])
    assert "deadline" in serialized
    assert "timeout_seconds" in serialized

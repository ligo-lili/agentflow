"""AgentSession + AgentLoop + ToolRuntime: lifecycle events, bounds, failures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.inmemory import InMemoryEventStore
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession, SessionAlreadyRunError
from packages.runtime.tools import ToolRuntime, ToolRuntimeError


class WordCountTool:
    name = "word_count"
    description = "Count the words in the provided text."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        text = str(arguments.get("text", ""))
        return ToolResult(name=self.name, ok=True, value=len(text.split()))


class ExplodingTool:
    name = "explode"
    description = "Always fails."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        raise RuntimeError("tool exploded")


def final(content: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=content),
        finish_reason="stop",
    )


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=""),
        finish_reason="tool_calls",
        tool_calls=(ToolCallRequest(call_id=call_id, name=name, arguments=arguments),),
    )


def make_session(script: list, tools: list | None = None) -> AgentSession:
    return AgentSession(
        task="demo task",
        provider=FakeModelProvider(script),
        tools=tools if tools is not None else [WordCountTool()],
    )


def test_happy_path_emits_full_lifecycle_in_monotonic_order() -> None:
    session = make_session(
        [
            tool_call("c1", "word_count", {"text": "one two three"}),
            final("The text has 3 words."),
        ]
    )
    result = session.run()

    assert result.status == "finished"
    assert result.answer == "The text has 3 words."
    assert result.steps == 2
    expected = [
        AgentEventType.SESSION_STARTED,
        AgentEventType.LLM_CALL_STARTED,
        AgentEventType.LLM_CALL_FINISHED,
        AgentEventType.TOOL_CALL_STARTED,
        AgentEventType.TOOL_CALL_FINISHED,
        AgentEventType.LLM_CALL_STARTED,
        AgentEventType.LLM_CALL_FINISHED,
        AgentEventType.AGENT_FINISHED,
    ]
    events = session.events()
    assert [e.event_type for e in events] == expected
    assert [e.sequence for e in events] == list(range(len(expected)))
    assert [e.event_id for e in events] == [f"{session.session_id}-{i:04d}" for i in range(8)]


def test_events_reach_the_store_through_the_bus() -> None:
    store = InMemoryEventStore()
    session = AgentSession(
        task="demo task",
        provider=FakeModelProvider([final("done")]),
        store=store,
        session_id="fixed-session",
    )
    session.run()
    assert [e.sequence for e in store.get_session_events("fixed-session")] == [0, 1, 2, 3]
    assert [e.event_type for e in session.events()] == [
        AgentEventType.SESSION_STARTED,
        AgentEventType.LLM_CALL_STARTED,
        AgentEventType.LLM_CALL_FINISHED,
        AgentEventType.AGENT_FINISHED,
    ]


def test_session_started_payload_carries_task_and_model() -> None:
    session = make_session([final("done")])
    session.run()
    payload = session.events()[0].payload
    assert payload["task"] == "demo task"
    assert payload["model"] == "fake-model"
    assert payload["max_steps"] == 8


def test_provider_exception_produces_agent_failed_with_redacted_diagnostic() -> None:
    session = make_session([RuntimeError("upstream 401: api_key=sk-secret-123 leaked")])
    result = session.run()

    assert result.status == "failed"
    assert "sk-secret-123" not in (result.error or "")
    assert result.error is not None and result.error.startswith("RuntimeError: upstream 401")
    failed = session.events()[-1]
    assert failed.event_type == AgentEventType.AGENT_FAILED
    assert failed.payload["phase"] == "provider"
    assert "sk-secret-123" not in json.dumps(failed.payload)
    assert "sk-secret-123" not in json.dumps([e.payload for e in session.events()])


def test_tool_exception_becomes_failed_tool_event_and_loop_continues() -> None:
    session = make_session(
        [tool_call("c1", "explode", {}), final("recovered after tool failure")],
        tools=[ExplodingTool()],
    )
    result = session.run()

    assert result.status == "finished"
    assert result.steps == 2
    finished = [e for e in session.events() if e.event_type == AgentEventType.TOOL_CALL_FINISHED]
    assert finished[0].payload["ok"] is False
    assert "tool exploded" in finished[0].payload["error"]


def test_unknown_tool_fails_explicitly_without_crashing() -> None:
    session = make_session([tool_call("c1", "missing_tool", {}), final("handled")])
    result = session.run()
    assert result.status == "finished"
    finished = [e for e in session.events() if e.event_type == AgentEventType.TOOL_CALL_FINISHED]
    assert finished[0].payload["ok"] is False
    assert "unknown tool" in finished[0].payload["error"]


def test_step_limit_produces_agent_failed() -> None:
    session = AgentSession(
        task="demo task",
        provider=FakeModelProvider(
            [
                tool_call("c1", "word_count", {"text": "loop"}),
                tool_call("c2", "word_count", {"text": "loop"}),
            ]
        ),
        tools=[WordCountTool()],
        config=AgentLoopConfig(max_steps=2),
    )
    result = session.run()

    assert result.status == "failed"
    assert result.steps == 2
    failed = session.events()[-1]
    assert failed.event_type == AgentEventType.AGENT_FAILED
    assert failed.payload["phase"] == "step_limit"
    llm_calls = [e for e in session.events() if e.event_type == AgentEventType.LLM_CALL_STARTED]
    assert len(llm_calls) == 2


def test_transcript_includes_tool_observation_as_json() -> None:
    provider = FakeModelProvider(
        [tool_call("c1", "word_count", {"text": "a b"}), final("2 words")]
    )
    session = AgentSession(task="t", provider=provider, tools=[WordCountTool()])
    session.run()

    second_request = provider.calls[1]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0].content) == {"ok": True, "value": 2, "error": None}
    assert tool_messages[0].tool_call_id == "c1"


def test_double_run_is_rejected() -> None:
    session = make_session([final("done")])
    session.run()
    with pytest.raises(SessionAlreadyRunError):
        session.run()


def test_tool_runtime_rejects_duplicate_names() -> None:
    runtime = ToolRuntime([WordCountTool()])
    with pytest.raises(ToolRuntimeError, match="already registered"):
        runtime.register(WordCountTool())


def test_tool_runtime_specs_expose_name_and_description() -> None:
    specs = ToolRuntime([WordCountTool()]).specs()
    assert len(specs) == 1
    assert specs[0].name == "word_count"
    assert "Count the words" in specs[0].description

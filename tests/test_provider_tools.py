"""Provider and tool contracts: protocols are structural, models serialize."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from packages.core.provider import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    ToolCallRequest,
    ToolSpec,
)
from packages.core.tools import Tool, ToolContext, ToolResult


class DeterministicFakeProvider:
    """Reference FakeModelProvider shape: fixed answer, no network."""

    def __init__(self, reply: str = "done") -> None:
        self._reply = reply
        self.calls: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            message=ModelMessage(role="assistant", content=self._reply),
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=2),
        )


class EchoTool:
    """Reference Tool implementation used across tests."""

    name = "echo"
    description = "Echo the provided text."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        text = str(arguments.get("text", ""))
        return ToolResult(name=self.name, ok=True, value=text)


def test_fake_provider_satisfies_model_provider_protocol() -> None:
    provider: ModelProvider = DeterministicFakeProvider()
    request = ModelRequest(
        model="fake-model",
        messages=(ModelMessage(role="user", content="hi"),),
        tools=(ToolSpec(name="echo", description="Echo."),),
    )
    response = provider.invoke(request)
    assert response.message.content == "done"
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 12
    assert provider.calls == [request]


def test_tool_call_request_carries_arguments() -> None:
    call = ToolCallRequest(call_id="c1", name="echo", arguments={"text": "x"})
    assert call.call_id == "c1"
    assert call.name == "echo"
    assert call.arguments == {"text": "x"}


def test_echo_tool_satisfies_tool_protocol() -> None:
    tool: Tool = EchoTool()
    context = ToolContext(session_id="s-1", trace_id="tr-1", step_index=0)
    result = tool.run({"text": "hello"}, context)
    assert result.ok is True
    assert result.value == "hello"
    assert result.error is None


def test_tool_result_carries_failure_explicitly() -> None:
    result = ToolResult(name="echo", ok=False, error="boom")
    assert result.ok is False
    assert result.error == "boom"


def test_tool_context_validates_step_index() -> None:
    with pytest.raises(ValidationError):
        ToolContext(session_id="s-1", trace_id="tr-1", step_index=-1)


def test_model_request_rejects_unknown_roles() -> None:
    with pytest.raises(ValidationError):
        ModelMessage(role="wizard", content="x")  # type: ignore[arg-type]

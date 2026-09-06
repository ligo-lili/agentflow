"""OpenAI-compatible adapter: offline mocked-HTTP contract tests (review R7).

No network and no API key: every test drives ``httpx.MockTransport``. The
adapter maps wire data to core models only — no SDK types leak out — and
never lets credentials or provider error bodies reach error messages.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import pytest

from packages.core.errors import AgentFlowError
from packages.core.provider import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    ToolSpec,
)
from packages.runtime.openai_provider import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_TIMEOUT_SECONDS,
    OpenAICompatProvider,
    OpenAIProviderConfigError,
    OpenAIProviderError,
    OpenAIProviderTimeoutError,
    OpenAIProviderUnavailableError,
)

API_KEY = "test-key-abcdef123456"


def make_provider(handler: Any) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="http://mock-endpoint/v1",
        api_key=API_KEY,
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def simple_request() -> ModelRequest:
    return ModelRequest(
        model="test-model",
        messages=(
            ModelMessage(role="system", content="You are AgentFlow."),
            ModelMessage(role="user", content="Say hello."),
        ),
    )


def test_provider_satisfies_the_model_provider_protocol() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json={"choices": []}))
    assert isinstance(provider, ModelProvider)


def test_normal_response_maps_to_core_models_and_preserves_usage() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.read())
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello from the mock."},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    provider = make_provider(handler)
    response = provider.invoke(simple_request())

    assert captured["url"].endswith("/chat/completions")
    assert captured["authorization"] == f"Bearer {API_KEY}"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["messages"][0] == {
        "role": "system",
        "content": "You are AgentFlow.",
    }
    assert isinstance(response, ModelResponse)
    assert response.message.content == "Hello from the mock."
    assert response.finish_reason == "stop"
    assert response.tool_calls == ()
    assert isinstance(response.usage, TokenUsage)
    assert response.usage == TokenUsage(prompt_tokens=12, completion_tokens=5)


def test_tool_call_response_and_wire_shape_round_trip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        if len(payload["messages"]) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "word_count",
                                            "arguments": '{"text": "one two"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop",
                     "message": {"role": "assistant", "content": "2 words"}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 3},
            },
        )

    provider = make_provider(handler)
    first = provider.invoke(
        ModelRequest(
            model="test-model",
            messages=(ModelMessage(role="user", content="count"),),
            tools=(
                ToolSpec(name="word_count", description="Count words."),
                ToolSpec(name="other", description="Other.", parameters_schema={"type": "object"}),
            ),
        )
    )

    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].call_id == "call-1"
    assert first.tool_calls[0].name == "word_count"
    assert first.tool_calls[0].arguments == {"text": "one two"}

    # Continuation: the transcript's tool observation must serialize as an
    # assistant tool_calls entry plus a role=tool message.
    continuation = provider.invoke(
        ModelRequest(
            model="test-model",
            messages=(
                ModelMessage(role="user", content="count"),
                ModelMessage(role="assistant", content=""),
                ModelMessage(
                    role="tool",
                    content=json.dumps({"ok": True, "value": 2, "error": None}),
                    name="word_count",
                    tool_call_id="call-1",
                ),
            ),
        )
    )
    assert continuation.message.content == "2 words"
    assert continuation.usage == TokenUsage(prompt_tokens=20, completion_tokens=3)


def test_wire_messages_synthesize_assistant_tool_calls() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json={"choices": []}))
    payload = provider._payload(
        ModelRequest(
            model="test-model",
            messages=(
                ModelMessage(role="user", content="count"),
                ModelMessage(role="assistant", content=""),
                ModelMessage(
                    role="tool",
                    content='{"ok": true, "value": 2, "error": null}',
                    name="word_count",
                    tool_call_id="call-1",
                ),
            ),
        )
    )
    wire = payload["messages"]
    assert wire[1]["tool_calls"] == [
        {"id": "call-1", "type": "function",
         "function": {"name": "word_count", "arguments": "{}"}}
    ]
    assert wire[2] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"ok": true, "value": 2, "error": null}',
    }


def test_http_error_is_typed_and_never_leaks_the_key_or_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text=f"internal error for key {API_KEY}: upstream exploded",
        )

    provider = make_provider(handler)
    with pytest.raises(OpenAIProviderError) as exc_info:
        provider.invoke(simple_request())
    message = str(exc_info.value)
    assert "HTTP 500" in message
    assert API_KEY not in message
    assert "upstream exploded" not in message
    assert isinstance(exc_info.value, AgentFlowError)


def test_transport_error_is_typed_and_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"connection refused near authorization=Bearer {API_KEY}",
            request=request,
        )

    provider = make_provider(handler)
    with pytest.raises(OpenAIProviderError) as exc_info:
        provider.invoke(simple_request())
    assert "ConnectError" in str(exc_info.value)
    assert API_KEY not in str(exc_info.value)


def test_timeout_maps_to_a_typed_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            f"read timed out while auth={API_KEY}", request=request
        )

    provider = make_provider(handler)
    with pytest.raises(OpenAIProviderTimeoutError) as exc_info:
        provider.invoke(simple_request())
    message = str(exc_info.value)
    assert "30s HTTP deadline" in message
    assert API_KEY not in message


def test_from_env_requires_explicit_configuration() -> None:
    with pytest.raises(OpenAIProviderConfigError) as exc_info:
        OpenAICompatProvider.from_env({})
    assert "AGENTFLOW_BASE_URL" in str(exc_info.value)
    assert "AGENTFLOW_API_KEY" in str(exc_info.value)
    assert "AGENTFLOW_MODEL" in str(exc_info.value)

    with pytest.raises(OpenAIProviderConfigError, match="AGENTFLOW_API_KEY"):
        OpenAICompatProvider.from_env(
            {ENV_BASE_URL: "http://x/v1", ENV_MODEL: "m"}
        )


def test_from_env_builds_the_provider_with_optional_timeout() -> None:
    env = {
        ENV_BASE_URL: "http://mock-endpoint/v1/",
        ENV_API_KEY: API_KEY,
        ENV_MODEL: "test-model",
        ENV_TIMEOUT_SECONDS: "1.5",
    }
    provider = OpenAICompatProvider.from_env(env)
    assert provider.base_url == "http://mock-endpoint/v1"
    assert provider.model == "test-model"
    assert provider.timeout_seconds == 1.5

    with pytest.raises(OpenAIProviderConfigError, match="must be a number"):
        OpenAICompatProvider.from_env({**env, ENV_TIMEOUT_SECONDS: "soon"})


def test_adapter_never_leaks_sdk_types_into_core_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": "",
                                 "tool_calls": [
                                     {"id": "c9", "type": "function",
                                      "function": {"name": "t", "arguments": "{}"}}
                                 ]},
                     }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    response = make_provider(handler).invoke(simple_request())
    # Deep inspection: everything in the response graph is a core model.
    dumped = response.model_dump()
    assert dumped["message"]["role"] == "assistant"
    assert dumped["tool_calls"][0]["call_id"] == "c9"
    assert response.usage.total_tokens == 3


def test_unavailable_error_when_httpx_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "httpx", None)
    provider = make_provider(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(OpenAIProviderUnavailableError, match="httpx is not installed"):
        provider.invoke(simple_request())

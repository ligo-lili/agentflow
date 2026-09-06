"""FakeModelProvider: scripted, offline, deterministic."""

from __future__ import annotations

import pytest

from packages.core.errors import AgentFlowError
from packages.core.provider import ModelMessage, ModelRequest, ModelResponse, TokenUsage
from packages.runtime.provider import FakeModelProvider, ScriptExhaustedError


def make_request() -> ModelRequest:
    return ModelRequest(model="fake-model", messages=(ModelMessage(role="user", content="hi"),))


def final(content: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=content),
        finish_reason="stop",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
    )


def test_scripted_responses_are_returned_in_order() -> None:
    provider = FakeModelProvider([final("one"), final("two")])
    assert provider.invoke(make_request()).message.content == "one"
    assert provider.invoke(make_request()).message.content == "two"
    assert len(provider.calls) == 2


def test_provider_records_every_request() -> None:
    provider = FakeModelProvider([final("ok")])
    request = make_request()
    provider.invoke(request)
    assert provider.calls == [request]


def test_injected_exception_is_raised_verbatim() -> None:
    provider = FakeModelProvider([RuntimeError("boom"), final("unused")])
    with pytest.raises(RuntimeError, match="boom"):
        provider.invoke(make_request())


def test_script_exhaustion_raises_typed_error() -> None:
    provider = FakeModelProvider([final("only")])
    provider.invoke(make_request())
    with pytest.raises(ScriptExhaustedError, match="script exhausted after 1 call"):
        provider.invoke(make_request())
    assert isinstance(ScriptExhaustedError("x"), AgentFlowError)


def test_empty_script_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="non-empty script"):
        FakeModelProvider([])

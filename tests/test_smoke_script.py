"""Offline tests for scripts/smoke_openai.py (release task).

The script itself talks to a real endpoint by design and stays outside the
offline matrix; these tests cover only its local behavior — configuration
handling, credential non-disclosure and the fault-check scaffolding — using a
fake provider injected into the loaded module.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, ClassVar

import pytest

from packages.core.provider import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from packages.runtime.openai_provider import (
    OpenAIProviderConfigError,
    OpenAIProviderError,
    OpenAIProviderTimeoutError,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_openai.py"
REQUIRED_ENV_KEYS = ("AGENTFLOW_BASE_URL", "AGENTFLOW_API_KEY", "AGENTFLOW_MODEL")
SECRET = "sk-smoke-secret-key-0000000000"
FULL_ENV: dict[str, str] = {
    "AGENTFLOW_BASE_URL": "https://example-endpoint.invalid/v1",
    "AGENTFLOW_API_KEY": SECRET,
    "AGENTFLOW_MODEL": "smoke-model",
}


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("agentflow_smoke_openai", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


class FakeProvider:
    """Stands in for OpenAICompatProvider; behavior keyed on init overrides."""

    instances: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).instances.append(kwargs)
        self._kwargs = kwargs
        self.model = str(kwargs.get("model") or FULL_ENV["AGENTFLOW_MODEL"])

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> FakeProvider:
        environ = env or {}
        missing = [name for name in REQUIRED_ENV_KEYS if not environ.get(name)]
        if missing:
            raise OpenAIProviderConfigError(
                f"missing environment variables: {', '.join(missing)} (see .env.example)"
            )
        return cls(**environ)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        assert isinstance(request, ModelRequest)
        if self._kwargs.get("timeout_seconds") == SCRIPT.TINY_TIMEOUT_SECONDS:
            raise OpenAIProviderTimeoutError(
                "provider request exceeded its 0.001s HTTP deadline"
            )
        if self._kwargs.get("api_key") == SCRIPT.INVALID_API_KEY:
            raise OpenAIProviderError("provider returned HTTP 401")
        if self._kwargs.get("base_url") == SCRIPT.INVALID_BASE_URL:
            raise OpenAIProviderError(
                "provider request failed: ConnectError: [redacted diagnostic]"
            )
        return ModelResponse(
            message=ModelMessage(role="assistant", content="OK"),
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=3, completion_tokens=1),
        )


@pytest.fixture()
def script(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Script module with OpenAICompatProvider replaced by the fake."""
    FakeProvider.instances = []
    monkeypatch.setattr(SCRIPT, "OpenAICompatProvider", FakeProvider)
    return SCRIPT


def test_missing_config_prints_instructions_and_returns_2(script: Any, capsys: Any) -> None:
    rc = script.main(env={})
    out = capsys.readouterr().out
    assert rc == 2
    for name in REQUIRED_ENV_KEYS:
        assert name in out
    assert ".env.example" in out
    assert SECRET not in out


def test_happy_path_reports_result_without_leaking_key(script: Any, capsys: Any) -> None:
    rc = script.main(env=FULL_ENV)
    out = capsys.readouterr().out
    assert rc == 0
    assert "finish_reason=stop" in out
    assert "content='OK'" in out
    assert "prompt_tokens=3" in out
    assert "happy path OK" in out
    assert SECRET not in out


def test_provider_failure_returns_1_and_keeps_key_hidden(
    script: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_invoke(self: Any, request: ModelRequest) -> ModelResponse:
        raise OpenAIProviderError("provider returned HTTP 401")

    monkeypatch.setattr(FakeProvider, "invoke", failing_invoke)
    rc = script.main(env=FULL_ENV)
    out = capsys.readouterr().out
    assert rc == 1
    assert "provider returned HTTP 401" in out
    assert SECRET not in out


def test_fault_checks_pass_when_failures_are_typed(script: Any, capsys: Any) -> None:
    rc = script.main(["--fault-checks"], env=FULL_ENV)
    out = capsys.readouterr().out
    assert rc == 0
    assert "fault checks OK" in out
    assert len(FakeProvider.instances) == 4  # happy path + three fault providers
    assert any(
        i.get("timeout_seconds") == SCRIPT.TINY_TIMEOUT_SECONDS
        for i in FakeProvider.instances
    )
    assert any(i.get("api_key") == SCRIPT.INVALID_API_KEY for i in FakeProvider.instances)
    assert any(i.get("base_url") == SCRIPT.INVALID_BASE_URL for i in FakeProvider.instances)
    assert SECRET not in out


def test_unknown_argument_returns_2(script: Any, capsys: Any) -> None:
    rc = script.main(["--wat"], env=FULL_ENV)
    out = capsys.readouterr().out
    assert rc == 2
    assert "--fault-checks" in out

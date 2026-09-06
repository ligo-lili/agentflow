"""Offline tests for the explicit provider factory (Phase 6.1)."""

from __future__ import annotations

import pytest

from packages.core.provider import ModelProvider
from packages.runtime.openai_provider import OpenAICompatProvider
from packages.runtime.provider_factory import (
    ENV_PROVIDER,
    ProviderConfigError,
    create_provider,
)


def test_unset_or_fake_provider_is_a_clear_configuration_error() -> None:
    for env in ({}, {ENV_PROVIDER: ""}, {ENV_PROVIDER: "fake"}):
        with pytest.raises(ProviderConfigError) as excinfo:
            create_provider(env)
        assert "openai-compat" in str(excinfo.value)


def test_unknown_provider_name_is_rejected() -> None:
    with pytest.raises(ProviderConfigError) as excinfo:
        create_provider({ENV_PROVIDER: "magic-llm"})
    assert "magic-llm" in str(excinfo.value)
    assert "openai-compat" in str(excinfo.value)


def test_openai_compat_builds_from_explicit_env() -> None:
    env = {
        ENV_PROVIDER: "openai-compat",
        "AGENTFLOW_BASE_URL": "https://example-endpoint.invalid/v1",
        "AGENTFLOW_API_KEY": "sk-offline-test-key-000000",
        "AGENTFLOW_MODEL": "test-model",
    }
    provider = create_provider(env)
    assert isinstance(provider, OpenAICompatProvider)
    assert isinstance(provider, ModelProvider)  # protocol-satisfying
    assert provider.base_url == "https://example-endpoint.invalid/v1"


def test_openai_compat_missing_credentials_names_keys() -> None:
    with pytest.raises(ProviderConfigError) as excinfo:
        create_provider({ENV_PROVIDER: "openai-compat"})
    assert "AGENTFLOW_API_KEY" in str(excinfo.value)


def test_env_missing_uses_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROVIDER, "nope")
    with pytest.raises(ProviderConfigError):
        create_provider()
    monkeypatch.delenv(ENV_PROVIDER)
    with pytest.raises(ProviderConfigError):
        create_provider()


def test_unused_env_keys_do_not_leak_into_errors() -> None:
    secret = "sk-secret-value-1234567890"
    with pytest.raises(ProviderConfigError) as excinfo:
        create_provider({
            ENV_PROVIDER: "openai-compat",
            "AGENTFLOW_BASE_URL": "https://example-endpoint.invalid/v1",
            "AGENTFLOW_API_KEY": secret,
        })
    assert secret not in str(excinfo.value)
    assert "AGENTFLOW_MODEL" in str(excinfo.value)

"""Provider selection: explicit, env-driven factory (Phase 6).

``create_provider`` maps ``AGENTFLOW_PROVIDER`` to a concrete
``ModelProvider``:

- ``openai-compat`` → :class:`OpenAICompatProvider.from_env` (requires the
  ``AGENTFLOW_BASE_URL`` / ``AGENTFLOW_API_KEY`` / ``AGENTFLOW_MODEL``
  variables; missing keys raise a typed config error naming them);
- anything else (including unset/empty) is a configuration error — there is
  deliberately no implicit default provider for arbitrary tasks: the scripted
  ``FakeModelProvider`` only serves the canned scenario runs, where the API
  constructs it directly.

Failure happens at startup, never lazily inside a request.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from packages.core.errors import AgentFlowError
from packages.core.provider import ModelProvider
from packages.runtime.openai_provider import (
    OpenAICompatProvider,
    OpenAIProviderConfigError,
)

ENV_PROVIDER = "AGENTFLOW_PROVIDER"

#: Accepted values for ``AGENTFLOW_PROVIDER`` (documented contract).
SUPPORTED_PROVIDERS = ("openai-compat",)


class ProviderConfigError(AgentFlowError):
    """Raised when ``AGENTFLOW_PROVIDER`` is missing, unknown, or unusable."""


def create_provider(env: Mapping[str, str] | None = None) -> ModelProvider:
    """Build the provider named by ``AGENTFLOW_PROVIDER`` (explicit only)."""
    environ = os.environ if env is None else env
    choice = (environ.get(ENV_PROVIDER) or "").strip().lower()
    if choice in ("", "fake"):
        raise ProviderConfigError(
            f"{ENV_PROVIDER} is not set to a real provider; arbitrary-task runs "
            f"need {SUPPORTED_PROVIDERS[0]} (the scripted fake provider only serves "
            "the canned scenario runs)"
        )
    if choice == "openai-compat":
        try:
            return OpenAICompatProvider.from_env(environ)
        except OpenAIProviderConfigError as exc:
            # One typed error for callers; the message names every missing key.
            raise ProviderConfigError(str(exc)) from exc
    raise ProviderConfigError(
        f"unknown {ENV_PROVIDER}={choice!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )

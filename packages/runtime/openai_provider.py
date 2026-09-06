"""Optional OpenAI-compatible chat-completions adapter (review R7).

Implements the ``ModelProvider`` Protocol against any OpenAI-compatible
``/chat/completions`` endpoint. The adapter is deliberately optional:

- it is outside the default test matrix: the offline suite never touches it
  and requires no API key; the only tests are offline ``httpx.MockTransport``
  contract tests, plus a manual smoke procedure
  (docs/evidence/review-r7/report.md);
- it requires explicit environment configuration (``from_env`` reads
  ``AGENTFLOW_BASE_URL`` / ``AGENTFLOW_API_KEY`` / ``AGENTFLOW_MODEL``);
- ``httpx`` is imported lazily, so a bare install never breaks;
- no SDK types leak into the core models — every response is mapped to
  ``ModelResponse`` / ``TokenUsage`` / ``ToolCallRequest``.

Usage data is preserved; credentials and provider error bodies are redacted
and never logged. Pair with the runtime's ``TimeoutPolicy`` for loop-level
deadlines, or set ``timeout_seconds`` for the HTTP client itself.

Wire-format note: the runtime transcript stores the assistant's tool calls
implicitly (the following ``role="tool"`` messages carry ``tool_call_id``
and ``name``). Serialization synthesizes ``tool_calls`` entries on the
preceding assistant message with ``arguments="{}"`` — the original argument
values are not part of the stored transcript (the model regenerates them on
its next call).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from packages.core.errors import AgentFlowError
from packages.core.provider import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    ToolCallRequest,
)
from packages.runtime.diagnostics import redact_diagnostic

if TYPE_CHECKING:
    import httpx

#: Environment variables (mirrors .env.example); all required for from_env.
ENV_BASE_URL = "AGENTFLOW_BASE_URL"
ENV_API_KEY = "AGENTFLOW_API_KEY"
ENV_MODEL = "AGENTFLOW_MODEL"
ENV_TIMEOUT_SECONDS = "AGENTFLOW_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 30.0


class OpenAIProviderError(AgentFlowError):
    """Base class for OpenAI-compatible adapter failures."""


class OpenAIProviderUnavailableError(OpenAIProviderError):
    """Raised when httpx (the HTTP client) is not installed."""


class OpenAIProviderConfigError(OpenAIProviderError):
    """Raised when the explicit environment configuration is incomplete."""


class OpenAIProviderTimeoutError(OpenAIProviderError):
    """Raised when the provider call exceeds the configured HTTP deadline."""


def _empty_parameters() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


class OpenAICompatProvider:
    """``ModelProvider`` for OpenAI-compatible endpoints (offline by default).

    ``api_key`` is stored privately, never logged, and never included in any
    error message. Pass ``client`` to inject a prepared ``httpx.Client``
    (used by the mocked contract tests via ``httpx.MockTransport``).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise OpenAIProviderConfigError("base_url is required")
        if not api_key:
            raise OpenAIProviderConfigError("api_key is required (it is never logged)")
        if not model:
            raise OpenAIProviderConfigError("model is required")
        if timeout_seconds <= 0:
            raise OpenAIProviderConfigError("timeout_seconds must be positive")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self._client: httpx.Client = client
        else:
            try:
                import httpx as httpx_module
            except ImportError as exc:
                raise OpenAIProviderUnavailableError(
                    "httpx is not installed; install the 'openai-compat' extra "
                    "(or dev extras) to use OpenAICompatProvider"
                ) from exc
            self._client = httpx_module.Client(timeout=timeout_seconds)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OpenAICompatProvider:
        """Build the provider from explicit environment variables.

        Never reads configuration implicitly: missing required variables
        raise ``OpenAIProviderConfigError`` naming every missing key.
        """
        environ = os.environ if env is None else env
        missing = [
            name
            for name in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL)
            if not environ.get(name)
        ]
        if missing:
            raise OpenAIProviderConfigError(
                f"missing environment variables: {', '.join(missing)} (see .env.example)"
            )
        timeout_raw = environ.get(ENV_TIMEOUT_SECONDS, str(DEFAULT_TIMEOUT_SECONDS))
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise OpenAIProviderConfigError(
                f"{ENV_TIMEOUT_SECONDS} must be a number, got {timeout_raw!r}"
            ) from exc
        return cls(
            base_url=environ[ENV_BASE_URL],
            api_key=environ[ENV_API_KEY],
            model=environ[ENV_MODEL],
            timeout_seconds=timeout_seconds,
        )

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Execute one chat completion; failures are typed and redacted."""
        try:
            import httpx as httpx_module
        except ImportError as exc:
            raise OpenAIProviderUnavailableError(
                "httpx is not installed; install the 'openai-compat' extra "
                "(or dev extras) to use OpenAICompatProvider"
            ) from exc
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json=self._payload(request),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx_module.TimeoutException as exc:
            raise OpenAIProviderTimeoutError(
                f"provider request exceeded its {self.timeout_seconds:g}s HTTP deadline"
            ) from exc
        except httpx_module.HTTPError as exc:
            raise OpenAIProviderError(
                redact_diagnostic(f"provider request failed: {type(exc).__name__}: {exc}")
            ) from exc
        if response.status_code >= 400:
            # Status only: provider error bodies may echo request data.
            raise OpenAIProviderError(
                f"provider returned HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise OpenAIProviderError("provider returned a non-JSON body") from exc
        return self._parse(data)

    # -- core models -> wire format ----------------------------------------

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._wire_messages(request.messages),
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters_schema or _empty_parameters(),
                    },
                }
                for spec in request.tools
            ]
        if request.temperature != 0.0:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    def _wire_messages(self, messages: tuple[ModelMessage, ...]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": message.content}
                wire.append(entry)
                continue
            if message.role == "tool":
                call = self._synthesized_tool_call(message)
                target = self._assistant_for_tool_call(wire)
                if target is None:
                    # A tool message without a preceding assistant message:
                    # keep the transcript honest instead of fabricating one.
                    raise OpenAIProviderError(
                        f"tool message for call {call['id']!r} has no preceding assistant message"
                    )
                target.setdefault("tool_calls", []).append(call)
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
                continue
            entry = {"role": message.role, "content": message.content}
            if message.name is not None:
                entry["name"] = message.name
            wire.append(entry)
        return wire

    @staticmethod
    def _synthesized_tool_call(message: ModelMessage) -> dict[str, Any]:
        return {
            "id": message.tool_call_id,
            "type": "function",
            "function": {"name": message.name, "arguments": "{}"},
        }

    @staticmethod
    def _assistant_for_tool_call(wire: list[dict[str, Any]]) -> dict[str, Any] | None:
        for entry in reversed(wire):
            if entry["role"] == "assistant":
                return entry
            if entry["role"] == "tool":
                continue
            return None
        return None

    # -- wire format -> core models ------------------------------------------

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        choices = data.get("choices") or []
        if not choices:
            raise OpenAIProviderError("provider response has no choices")
        choice = choices[0]
        message = choice.get("message") or {}

        tool_calls: list[ToolCallRequest] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            arguments_raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_raw)
            except json.JSONDecodeError as exc:
                raise OpenAIProviderError(
                    "tool call arguments are not valid JSON"
                ) from exc
            if not isinstance(arguments, dict):
                raise OpenAIProviderError("tool call arguments must decode to an object")
            tool_calls.append(
                ToolCallRequest(
                    call_id=str(call.get("id") or f"call-{index}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        usage_data = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens") or 0),
            completion_tokens=int(usage_data.get("completion_tokens") or 0),
        )
        finish_reason = "tool_calls" if choice.get("finish_reason") == "tool_calls" else "stop"
        content = message.get("content") or ""
        return ModelResponse(
            message=ModelMessage(role="assistant", content=str(content)),
            finish_reason=finish_reason,  # type: ignore[arg-type]
            tool_calls=tuple(tool_calls),
            usage=usage,
        )

"""Payload redaction: secrets never reach the event log unredacted."""

from __future__ import annotations

from typing import Any

REDACTED_PLACEHOLDER = "[REDACTED]"

#: Payload keys whose values are always considered secret.
REDACTED_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "secret",
        "password",
        "token",
        "api-key",
        "x-api-key",
        "openai_api_key",
    }
)


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with secret-keyed values replaced.

    Redaction is recursive: nested dicts and dicts inside lists are scanned
    with the same rule. Keys are matched case-insensitively.
    """
    return _redact_value(payload)  # type: ignore[no-any-return]


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in REDACTED_KEYS:
                redacted[key] = REDACTED_PLACEHOLDER
            else:
                redacted[key] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value

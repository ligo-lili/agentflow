"""Redaction contract: secrets never survive into event payloads."""

from __future__ import annotations

from packages.core.redaction import REDACTED_PLACEHOLDER, redact_payload


def test_top_level_secret_keys_are_redacted() -> None:
    payload = {"api_key": "sk-123", "model": "fake-model"}
    redacted = redact_payload(payload)
    assert redacted["api_key"] == REDACTED_PLACEHOLDER
    assert redacted["model"] == "fake-model"


def test_key_matching_is_case_insensitive() -> None:
    redacted = redact_payload({"Authorization": "Bearer abc"})
    assert redacted["Authorization"] == REDACTED_PLACEHOLDER


def test_redaction_is_recursive() -> None:
    payload = {
        "request": {"headers": {"x-api-key": "k"}, "url": "http://x"},
        "items": [{"password": "p"}, {"note": "n"}],
    }
    redacted = redact_payload(payload)
    assert redacted["request"]["headers"]["x-api-key"] == REDACTED_PLACEHOLDER
    assert redacted["request"]["url"] == "http://x"
    assert redacted["items"][0]["password"] == REDACTED_PLACEHOLDER
    assert redacted["items"][1]["note"] == "n"


def test_original_payload_is_not_mutated() -> None:
    payload = {"token": "t"}
    redact_payload(payload)
    assert payload["token"] == "t"

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


def test_diagnostic_redacts_key_value_and_bearer_scheme() -> None:
    from packages.runtime.diagnostics import redact_diagnostic

    # The value may carry an auth scheme; the secret after it must not leak.
    text = "connection refused near authorization=Bearer sk-secret-123456"
    scrubbed = redact_diagnostic(text)
    assert "sk-secret-123456" not in scrubbed
    assert "authorization=[REDACTED]" in scrubbed

    plain = redact_diagnostic("upstream 401: api_key=sk-secret-123 leaked")
    assert "sk-secret-123" not in plain
    assert "api_key=[REDACTED]" in plain


def test_diagnostic_redacts_standalone_long_bearer_tokens_only() -> None:
    from packages.runtime.diagnostics import redact_diagnostic

    scrubbed = redact_diagnostic("Authorization header: Bearer abcdef1234567890abcdef")
    assert "abcdef1234567890abcdef" not in scrubbed
    # Ordinary prose after the word "bearer" is not mangled.
    prose = redact_diagnostic("bearer tokens are issued by the provider")
    assert "tokens are issued" in prose

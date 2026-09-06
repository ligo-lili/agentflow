"""Manual smoke test for the optional OpenAI-compatible adapter (review R7).

Executes the documented manual procedure from docs/evidence/review-r7/report.md
against a REAL endpoint. It is never part of the offline test matrix and runs
only with explicit environment configuration (values stay out of the repo;
see .env.example):

    AGENTFLOW_BASE_URL           e.g. https://<your-endpoint>/v1
    AGENTFLOW_API_KEY            secret; never printed
    AGENTFLOW_MODEL              any model name the endpoint serves
    AGENTFLOW_TIMEOUT_SECONDS    optional HTTP deadline, default 30

Usage:
    python scripts/smoke_openai.py                 # happy path: one real call
    python scripts/smoke_openai.py --fault-checks  # + timeout / 401 / bad-base-url

Exit codes: 0 smoke passed · 1 smoke failed · 2 not configured / unusable.
Credentials never appear in output: the adapter stores the key privately and
provider failures report status codes or redacted diagnostics only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence

from packages.core.provider import ModelMessage, ModelRequest
from packages.runtime.openai_provider import (
    ENV_BASE_URL,
    ENV_MODEL,
    OpenAICompatProvider,
    OpenAIProviderConfigError,
    OpenAIProviderError,
    OpenAIProviderTimeoutError,
    OpenAIProviderUnavailableError,
)

REQUIRED_ENV = (ENV_BASE_URL, "AGENTFLOW_API_KEY", ENV_MODEL)
#: RFC 2606 reserved host: DNS resolution fails fast without touching anyone.
INVALID_BASE_URL = "https://agentflow-smoke.invalid"
#: Static dummy credential for the 401 check — deliberately not derived from
#: the configured key, so no key-derived string is ever sent or printed.
INVALID_API_KEY = "agentflow-smoke-invalid-key"
TINY_TIMEOUT_SECONDS = 0.001

SETUP_INSTRUCTIONS = """\
The OpenAI-compatible smoke path needs explicit configuration (values are \
never committed):

    pip install -e ".[openai-compat]"
    set AGENTFLOW_BASE_URL=https://<your-endpoint>/v1
    set AGENTFLOW_API_KEY=<your-key>
    set AGENTFLOW_MODEL=<your-model>

See .env.example and docs/evidence/review-r7/report.md section 4."""


def _build_provider(env: Mapping[str, str], **overrides: object) -> OpenAICompatProvider:
    """Build a provider from env values with explicit fault-check overrides."""
    settings: dict[str, object] = {
        "base_url": env[ENV_BASE_URL],
        "api_key": env["AGENTFLOW_API_KEY"],
        "model": env[ENV_MODEL],
    }
    settings.update(overrides)
    return OpenAICompatProvider(**settings)  # type: ignore[arg-type]


def _happy_path(provider: OpenAICompatProvider) -> int:
    print("[1/1] happy path: one real /chat/completions call ...")
    response = provider.invoke(
        ModelRequest(
            model=provider.model,
            messages=(ModelMessage(role="user", content="Reply with exactly: OK"),),
        )
    )
    print(f"finish_reason={response.finish_reason}")
    print(f"content={response.message.content!r}")
    print(f"usage={response.usage}")
    if response.finish_reason != "stop" or not response.message.content.strip():
        print("FAIL: expected finish_reason='stop' and non-empty content")
        return 1
    if "OK" not in response.message.content:
        print("NOTE: content does not contain 'OK' (model compliance); "
              "transport and mapping are still verified")
    print("happy path OK")
    return 0


def _fault_checks(env: Mapping[str, str]) -> int:
    """Expect typed, redacted failures from deliberately broken settings."""
    checks: tuple[tuple[str, dict[str, object], type[Exception]], ...] = (
        ("timeout injection",
         {"timeout_seconds": TINY_TIMEOUT_SECONDS},
         OpenAIProviderTimeoutError),
        ("invalid credential (expect HTTP 401)",
         {"api_key": INVALID_API_KEY},
         OpenAIProviderError),
        ("unroutable base URL",
         {"base_url": INVALID_BASE_URL},
         OpenAIProviderError),
    )
    status = 0
    for index, (label, overrides, expected) in enumerate(checks, start=1):
        print(f"[fault {index}/{len(checks)}] {label} ...")
        try:
            provider = _build_provider(env, **overrides)
            provider.invoke(
                ModelRequest(
                    model=env[ENV_MODEL],
                    messages=(ModelMessage(role="user", content="ping"),),
                )
            )
        except expected as exc:
            print(f"  OK: {type(exc).__name__}: {exc}")
            continue
        except OpenAIProviderError as exc:
            print(f"  FAIL: expected {expected.__name__}, got {type(exc).__name__}: {exc}")
            status = 1
            continue
        print("  FAIL: the broken call unexpectedly succeeded")
        status = 1
    if status == 0:
        print("fault checks OK (all failures typed, no key or body leaked)")
    return status


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run the smoke; ``env`` defaults to os.environ (injectable for tests)."""
    environ = os.environ if env is None else env
    arguments = list(argv or [])
    with_fault_checks = "--fault-checks" in arguments
    unknown = [a for a in arguments if a != "--fault-checks"]
    if unknown:
        print(f"unknown arguments: {unknown}; supported: --fault-checks")
        return 2

    try:
        provider = OpenAICompatProvider.from_env(environ)
    except OpenAIProviderConfigError as exc:
        print(f"not configured: {exc}")
        print(SETUP_INSTRUCTIONS)
        return 2
    except OpenAIProviderUnavailableError as exc:
        print(f"unusable: {exc}")
        print(SETUP_INSTRUCTIONS)
        return 2

    try:
        status = _happy_path(provider)
    except OpenAIProviderError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    if status != 0:
        return status
    if with_fault_checks:
        status = _fault_checks(environ)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

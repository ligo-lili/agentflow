"""Server configuration for the API, read once at startup (explicit env).

Every knob is optional with a documented default; invalid values fail at
startup with a message naming the variable — never lazily mid-request.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from packages.observability.sqlite import DEFAULT_DB_PATH

ENV_DB_PATH = "AGENTFLOW_DB_PATH"
ENV_MAX_CONTEXT_TOKENS = "AGENTFLOW_MAX_CONTEXT_TOKENS"
ENV_RESERVED_OUTPUT_TOKENS = "AGENTFLOW_RESERVED_OUTPUT_TOKENS"
ENV_PROVIDER_TIMEOUT_SECONDS = "AGENTFLOW_PROVIDER_TIMEOUT_SECONDS"
ENV_TOOL_TIMEOUT_SECONDS = "AGENTFLOW_TOOL_TIMEOUT_SECONDS"
ENV_RUN_WORKERS = "AGENTFLOW_RUN_WORKERS"

#: Context ceiling for task runs (the offline demos keep their own values).
DEFAULT_MAX_CONTEXT_TOKENS = 4000
DEFAULT_RESERVED_OUTPUT_TOKENS = 200
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 60.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_RUN_WORKERS = 2


def _number(env: Mapping[str, str], name: str, default: float, integer: bool = False) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw!r}")
    return int(value) if integer else value


@dataclass(frozen=True)
class ApiConfig:
    """Effective server configuration (immutable once the app is created)."""

    db_path: Path
    max_context_tokens: int
    reserved_output_tokens: int
    provider_timeout_seconds: float
    tool_timeout_seconds: float
    run_workers: int

    @classmethod
    def from_env(cls, db_path: Path | None = None, env: Mapping[str, str] | None = None) -> ApiConfig:
        environ = os.environ if env is None else env
        configured_path = (environ.get(ENV_DB_PATH) or "").strip()
        return cls(
            db_path=db_path or (Path(configured_path) if configured_path else DEFAULT_DB_PATH),
            max_context_tokens=int(
                _number(environ, ENV_MAX_CONTEXT_TOKENS, DEFAULT_MAX_CONTEXT_TOKENS, integer=True)
            ),
            reserved_output_tokens=int(
                _number(environ, ENV_RESERVED_OUTPUT_TOKENS, DEFAULT_RESERVED_OUTPUT_TOKENS, integer=True)
            ),
            provider_timeout_seconds=_number(
                environ, ENV_PROVIDER_TIMEOUT_SECONDS, DEFAULT_PROVIDER_TIMEOUT_SECONDS
            ),
            tool_timeout_seconds=_number(
                environ, ENV_TOOL_TIMEOUT_SECONDS, DEFAULT_TOOL_TIMEOUT_SECONDS
            ),
            run_workers=int(
                _number(environ, ENV_RUN_WORKERS, DEFAULT_RUN_WORKERS, integer=True)
            ),
        )

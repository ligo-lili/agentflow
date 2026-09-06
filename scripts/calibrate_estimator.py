"""Calibrate token estimators against real provider usage (manual evidence).

Sends a fixed, versioned prompt set through a real OpenAI-compatible endpoint
(single user message per call, no tools, so real ``usage.prompt_tokens``
reflects the messages plus chat-template overhead) and compares, per prompt:

- real ``usage.prompt_tokens`` / ``usage.completion_tokens`` (API accounting)
- ``deterministic-v1`` over the documented counting contract (messages
  serialized as ``"{role}: {content}"``; completion compared on bare content)
- ``tiktoken-cl100k_base`` over the same texts (when importable; the
  endpoint's true tokenizer is unknown, so this is a proxy of a proxy)

Evidence use: quantifies the documented "engineering proxy" limitation
(docs/evidence/portfolio-polish/report.md). Usage:

    python scripts/calibrate_estimator.py
    python scripts/calibrate_estimator.py --env-file .env

Exit codes: 0 calibration ran · 1 provider failure · 2 not configured.
Credentials and the endpoint model name never appear in output.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from packages.context.estimator import (
    DeterministicEstimator,
    EstimatorUnavailableError,
    TiktokenEstimator,
    TokenEstimator,
)
from packages.core.provider import ModelMessage, ModelRequest
from packages.runtime.openai_provider import (
    OpenAICompatProvider,
    OpenAIProviderError,
)

PROMPT_SET_VERSION = "1"

EN_LONG = (
    "Agent observability is the practice of recording what an agent actually "
    "saw and did at every step: the exact prompt that was assembled, the "
    "context window contents, each tool observation, and every compaction "
    "that rewrote the history. Replay then rebuilds the session from the "
    "append-only event log without calling any provider or tool again."
)
ZH_LONG = (
    "智能体可观测性是指把智能体每一步真实看到和做到的事情完整记录下来:"
    "拼装出的提示词、上下文窗口的内容、每一次工具观察结果,以及每一次改写"
    "历史的上下文压缩。重放则仅凭追加式事件日志重建整个会话,期间不再调用"
    "任何模型或工具,从而保证记录即真相。"
)
CODE_SNIPPET = (
    "Explain: def compact(history, limit):\n"
    "    total = sum(count(m) for m in history)\n"
    "    if total <= limit:\n"
    "        return history\n"
    "    return summarize(history[:-1]) + history[-1]\n"
)

PROMPTS: tuple[tuple[str, str], ...] = (
    ("en_short", "Reply with exactly: OK"),
    ("zh_short", "用一句话介绍你自己。"),
    ("json_payload", 'Summarize: {"user": "cjy", "roles": ["admin", "dev"], "active": true}'),
    ("en_long", EN_LONG),
    ("zh_long", ZH_LONG),
    ("code_snippet", CODE_SNIPPET),
)


def _prompt_estimate(count, messages: tuple[ModelMessage, ...]) -> int:
    return sum(count(f"{message.role}: {message.content}") for message in messages)


def _load_estimators() -> list[tuple[str, TokenEstimator]]:
    estimators: list[tuple[str, TokenEstimator]] = [
        ("deterministic-v1", DeterministicEstimator())
    ]
    try:
        estimators.append(("tiktoken-cl100k_base", TiktokenEstimator("cl100k_base")))
    except EstimatorUnavailableError as exc:  # tiktoken is optional; calibration still runs
        print(f"tiktoken unavailable ({type(exc).__name__}); comparing deterministic only")
    return estimators


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    arguments = list(argv or [])
    environ = os.environ if env is None else env
    if arguments:
        if arguments[0] != "--env-file" or len(arguments) != 2:
            print(f"unknown arguments: {arguments}; supported: --env-file <path>")
            return 2
        env_file = arguments[1]
        if not Path(env_file).is_file():
            print(f"env file not found: {env_file}")
            return 2
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from smoke_openai import parse_env_file

        environ = {**environ, **parse_env_file(env_file)}

    try:
        provider = OpenAICompatProvider.from_env(environ)
    except OpenAIProviderError as exc:
        print(f"not configured: {exc}")
        return 2

    estimators = _load_estimators()
    print(f"prompt set version {PROMPT_SET_VERSION}; model=<not printed>")
    print("prompt".ljust(14) + "".join(name.rjust(24) for name, _ in estimators) + "real usage".rjust(24))
    totals = {name: {"prompt": [], "completion": []} for name, _ in estimators}
    for label, content in PROMPTS:
        request = ModelRequest(
            model=provider.model,
            messages=(ModelMessage(role="user", content=content),),
        )
        try:
            response = provider.invoke(request)
        except OpenAIProviderError as exc:
            print(f"FAIL at {label}: {type(exc).__name__}: {exc}")
            return 1
        columns = []
        for name, estimator in estimators:
            prompt_est = _prompt_estimate(estimator.count, request.messages)
            completion_est = estimator.count(response.message.content)
            totals[name]["prompt"].append((prompt_est, response.usage.prompt_tokens))
            totals[name]["completion"].append(
                (completion_est, response.usage.completion_tokens)
            )
            columns.append(f"p={prompt_est}/c={completion_est}".rjust(24))
        real = (
            f"p={response.usage.prompt_tokens}/c={response.usage.completion_tokens}"
            f" [{response.finish_reason}]"
        )
        print(label.ljust(14) + "".join(columns) + real.rjust(24))

    print("\nmean absolute error vs real usage (tokens per case):")
    for name, _ in estimators:
        for side in ("prompt", "completion"):
            pairs = totals[name][side]
            mae = sum(abs(est - real) for est, real in pairs) / len(pairs)
            print(f"  {name:<22} {side:<10} {mae:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Token estimators: how text becomes token counts.

The default estimator is a documented deterministic formula so tests and
demos are offline and reproducible. When tiktoken is installed *and* its
encoding can be loaded, ``TiktokenEstimator`` provides model-accurate counts.
Every snapshot records the estimator name so results stay comparable
(docs/architecture/context-engine.md).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.core.errors import AgentFlowError


class EstimatorUnavailableError(AgentFlowError):
    """Raised when a requested estimator cannot be loaded (e.g. no tiktoken)."""


@runtime_checkable
class TokenEstimator(Protocol):
    """Counts tokens for text; the name travels with every snapshot."""

    @property
    def name(self) -> str: ...

    def count(self, text: str) -> int: ...


class DeterministicEstimator:
    """Documented fallback: 1 token per 4 characters, minimum 1 for non-empty.

    Formula (versioned in the name so historical snapshots stay interpretable):

        count(text) = 0 if text == "" else max(1, ceil(len(text) / 4))

    ``len`` counts Python characters (not bytes), so results are identical
    across platforms and runs. Accuracy is deliberately coarse; the point is
    a stable, explainable baseline.
    """

    @property
    def name(self) -> str:
        return "deterministic-v1"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


class TiktokenEstimator:
    """tiktoken-backed estimator; requires the package and encoding files."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        try:
            import tiktoken
        except ImportError as exc:
            raise EstimatorUnavailableError(
                "tiktoken is not installed; install packages[tokenizer] or use "
                "the deterministic estimator"
            ) from exc
        try:
            self._encoding = tiktoken.get_encoding(encoding_name)
        except Exception as exc:
            raise EstimatorUnavailableError(
                f"cannot load tiktoken encoding {encoding_name!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        self._name = f"tiktoken-{encoding_name}"

    @property
    def name(self) -> str:
        return self._name

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))


def create_estimator(preferred: str = "deterministic") -> TokenEstimator:
    """Create an estimator by name.

    ``deterministic`` (default) and ``tiktoken`` select directly;
    ``tiktoken`` raises ``EstimatorUnavailableError`` when unusable.
    ``auto`` tries tiktoken first and falls back to the deterministic
    estimator — the chosen name is recorded on every snapshot, so the
    fallback is always observable, never silent.
    """
    if preferred == "deterministic":
        return DeterministicEstimator()
    if preferred == "tiktoken":
        return TiktokenEstimator()
    if preferred == "auto":
        try:
            return TiktokenEstimator()
        except EstimatorUnavailableError:
            return DeterministicEstimator()
    raise ValueError(f"unknown estimator preference {preferred!r}")

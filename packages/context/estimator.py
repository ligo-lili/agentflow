"""Token estimators: how text becomes token counts.

The default estimator is a documented deterministic formula so tests and
demos are offline and reproducible. When tiktoken is installed *and* its
encoding can be loaded, ``TiktokenEstimator`` provides model-accurate counts.
Every snapshot records the estimator name so results stay comparable
(docs/architecture/context-engine.md).

Counting contract (what is serialized into a count):

- **Messages**: each conversation message is counted as the rendered text
  ``"{role}: {content}"`` (see ``count_by_role`` in ``packages.context.manager``).
- **Tools**: a tool observation enters the transcript as a ``role="tool"``
  message whose content is the JSON ``{"ok", "value", "error"}`` envelope, so
  it is counted as that JSON text. Tool *specs* (names/descriptions sent to
  the provider) are not part of the transcript and are not counted.
- **Structured state**: a compaction's summary/state message is a rendered
  ``role="system"`` message (fixed template or ``_render_state`` output) and
  is counted as that text.

Estimator implementations may expose a ``metadata`` mapping (encoding name,
model hint, formula) as an optional, duck-typed extension; ContextManager
copies it into the snapshot's ``metadata["estimator_metadata"]`` when present.

Fallback counts are an engineering proxy, **not** provider billing tokens.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.core.errors import AgentFlowError


class EstimatorUnavailableError(AgentFlowError):
    """Raised when a requested estimator cannot be loaded (e.g. no tiktoken)."""


class EstimatorMismatchError(AgentFlowError):
    """Raised when results measured with different estimators are compared.

    Counts from different estimators (e.g. ``deterministic-v1`` vs.
    ``tiktoken-cl100k_base``) are not directly comparable; experiments must
    disclose the estimator identity and reject mismatched comparisons.
    """


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
    across platforms and Python versions. Accuracy is deliberately coarse;
    the point is a stable, explainable baseline — an engineering proxy, not
    provider billing tokens.
    """

    @property
    def name(self) -> str:
        return "deterministic-v1"

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "formula": "0 if text == '' else max(1, ceil(len(text) / 4))",
            "counting_unit": "python_characters",
        }

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


class TiktokenEstimator:
    """tiktoken-backed estimator; requires the package and encoding files.

    ``model`` is optional provenance (which model the encoding is meant to
    approximate). It is recorded in :attr:`metadata` and, when set, in the
    estimator name so snapshots never conflate two model hint choices.
    """

    def __init__(self, encoding_name: str = "cl100k_base", model: str | None = None) -> None:
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
        self._encoding_name = encoding_name
        self._model = model
        self._name = (
            f"tiktoken-{encoding_name}" if model is None else f"tiktoken-{encoding_name}:{model}"
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "encoding": self._encoding_name,
            "model": self._model if self._model is not None else "unspecified",
            "counting_unit": "encoding_tokens",
        }

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

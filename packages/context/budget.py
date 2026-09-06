"""Token budget accounting: component breakdown, output reservation, verdicts.

The budget manager works on already-counted components; it reserves output
tokens from the context limit and explains every verdict with an explicit
reason string. Compaction triggering (threshold semantics) arrives with T4.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BudgetConfig(BaseModel):
    """Frozen budget settings for one run."""

    model_config = ConfigDict(frozen=True)

    max_context_tokens: int = Field(default=4096, gt=0)
    reserved_output_tokens: int = Field(default=256, ge=0)

    @model_validator(mode="after")
    def _reserve_must_leave_room(self) -> BudgetConfig:
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ValueError(
                "reserved_output_tokens must be smaller than max_context_tokens "
                f"(reserved={self.reserved_output_tokens}, max={self.max_context_tokens})"
            )
        return self

    @property
    def available_input_tokens(self) -> int:
        return self.max_context_tokens - self.reserved_output_tokens


class BudgetReport(BaseModel):
    """Verdict for one context build; breakdown sum always equals total."""

    model_config = ConfigDict(frozen=True)

    component_tokens: dict[str, int]
    total_tokens: int
    limit: int
    reserved_output_tokens: int
    fits: bool
    reason: str


class TokenBudgetManager:
    """Evaluates component token counts against the configured budget."""

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self._config = config or BudgetConfig()

    @property
    def config(self) -> BudgetConfig:
        return self._config

    @property
    def available_input_tokens(self) -> int:
        return self._config.available_input_tokens

    def evaluate(self, component_tokens: Mapping[str, int]) -> BudgetReport:
        total = sum(component_tokens.values())
        limit = self._config.available_input_tokens
        if total <= limit:
            reason = f"within_budget total={total} limit={limit}"
            fits = True
        else:
            reason = (
                f"over_budget total={total} limit={limit} "
                f"overage={total - limit}"
            )
            fits = False
        return BudgetReport(
            component_tokens=dict(component_tokens),
            total_tokens=total,
            limit=limit,
            reserved_output_tokens=self._config.reserved_output_tokens,
            fits=fits,
            reason=reason,
        )

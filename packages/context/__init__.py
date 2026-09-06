"""Context engine: estimators, prompt builder, budget, context manager, compaction."""

from packages.context.budget import BudgetConfig, BudgetReport, TokenBudgetManager
from packages.context.compaction import (
    CompactionConfig,
    CompactionDecision,
    CompactionEngine,
    CompactionResult,
)
from packages.context.estimator import (
    DeterministicEstimator,
    EstimatorUnavailableError,
    TiktokenEstimator,
    TokenEstimator,
    create_estimator,
)
from packages.context.fixture import canonical_messages
from packages.context.manager import ContextBuild, ContextManager, count_by_role
from packages.context.prompt import PromptBuilder, PromptSections

__all__ = [
    "BudgetConfig",
    "BudgetReport",
    "CompactionConfig",
    "CompactionDecision",
    "CompactionEngine",
    "CompactionResult",
    "ContextBuild",
    "ContextManager",
    "DeterministicEstimator",
    "EstimatorUnavailableError",
    "PromptBuilder",
    "PromptSections",
    "TiktokenEstimator",
    "TokenBudgetManager",
    "TokenEstimator",
    "canonical_messages",
    "count_by_role",
    "create_estimator",
]

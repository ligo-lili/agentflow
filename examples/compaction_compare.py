"""Phase 2 (T4) demo: two compaction strategies compared on the same fixture.

Run:

    python examples/compaction_compare.py

Both strategies (ADR-004) receive the exact same canonical transcript
(``packages/context/fixture.py``). Everything is deterministic: the same
numbers print on every run. The demo reports, per strategy: trigger
threshold, before/after token counts, what was removed/kept, the summary
message and the preserved state fields.
"""

from __future__ import annotations

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.fixture import canonical_messages
from packages.core.provider import ModelMessage
from packages.experiments import SCENARIO_SUITE_VERSION, compare_strategies, run_suite

BUDGET = BudgetConfig(max_context_tokens=250, reserved_output_tokens=50)  # limit 200


def strategy_header(title: str) -> None:
    print(f"\n=== {title} ===")


def show_result(result) -> None:
    print(f"trigger:   {result.trigger_reason}")
    print(f"tokens:    before={result.before_tokens} after={result.after_tokens} "
          f"threshold={result.threshold} limit={result.limit}")
    print(f"removed:   {len(result.removed_ids)} messages {list(result.removed_ids)}")
    print(f"kept:      {list(result.kept_ids)}")
    if result.summary_message is not None:
        head = result.summary_message.content.splitlines()[0]
        tail = f" (+{len(result.summary_message.content.splitlines()) - 1} more lines)"
        print(f"summary:   {head}{tail}")
    print(f"fits:      {result.fits_budget}")
    if result.preserved_state:
        for key, value in result.preserved_state.items():
            rendered = (
                "; ".join(value)[:100] if isinstance(value, list) else str(value)[:100]
            )
            print(f"  {key}: {rendered}")


def main() -> None:
    estimator = DeterministicEstimator()
    messages: tuple[ModelMessage, ...] = canonical_messages()
    total = sum(estimator.count(f"{m.role}: {m.content}") for m in messages)
    print("=== AgentFlow T4 demo: compaction strategy comparison ===")
    print(f"fixture: {len(messages)} messages, {total} tokens "
          f"(estimator {estimator.name}, budget {BUDGET.max_context_tokens}-"
          f"{BUDGET.reserved_output_tokens}={BUDGET.available_input_tokens})")

    for strategy in ("keep_recent_summary", "semantic_state"):
        engine = CompactionEngine(
            estimator,
            BUDGET,
            CompactionConfig(strategy=strategy, threshold_ratio=0.8, keep_last_messages=3),
        )
        decision = engine.should_compact(
            TokenBudgetManager(BUDGET).evaluate({"fixture": total})
        )
        strategy_header(f"strategy: {strategy}")
        print(f"decision:  should_compact={decision.should} ({decision.reason})")
        if decision.should:
            show_result(engine.compact(messages, decision.reason))

    print("\nDeterminism: both strategies were run twice on the same fixture; "
          "results are byte-identical (see tests/test_compaction.py).")

    print("\n=== A/B benchmark: evaluation over full sessions (same fixture) ===")
    comparison = compare_strategies()
    header = ("strategy", "task", "tool_rate", "steps", "peak", "final",
              "compact", "ctx_eff", "step_eff", "comp_eff", "SCORE")
    print("  " + "  ".join(f"{col:>10}" for col in header))
    for entry in comparison.entries:
        r = entry.report
        row = (entry.strategy[:10], r.task_completed, f"{r.tool_success_rate:.2f}",
               r.execution_steps, r.peak_context_tokens, r.final_context_tokens,
               r.compaction_count, f"{r.context_efficiency:.3f}",
               f"{r.step_efficiency:.2f}", f"{r.compaction_efficiency:.3f}",
               f"{r.score:.4f}")
        print("  " + "  ".join(f"{col!s:>10}" for col in row))
    print(f"\nwinner: {comparison.winner} (score delta {comparison.score_delta:+.4f})")
    print(f"estimator: {comparison.estimator} — counts are an engineering proxy, "
          "not billing tokens; comparisons reject estimator mismatch")
    print("metrics: task_completed 0/1 · tool_success_rate successful/total (no tools → 1.0)"
          " · execution_steps = LLM + tool calls · peak/final context tokens"
          " · context_efficiency = 1 - peak/limit · compaction_efficiency = mean recovery"
          " (no compaction → 1.0)")

    print(f"\n=== scenario suite v{SCENARIO_SUITE_VERSION}: three versioned fixtures ===")
    suite = run_suite()
    for result in suite.scenarios:
        r = result.report
        checks = " ".join(f"{c.code}={'ok' if c.passed else 'FAIL'}" for c in result.checks)
        print(f"  {result.scenario} / {result.strategy}: task={r.task_completed} "
              f"tool_rate={r.tool_success_rate:.2f} peak={r.peak_context_tokens} "
              f"score={r.score:.4f} [{checks}]")
    for aggregate in suite.aggregates:
        print(f"  aggregate {aggregate.strategy}: raw(task={aggregate.mean_task_completed:.2f} "
              f"tool={aggregate.mean_tool_success_rate:.3f} ctx={aggregate.mean_context_efficiency:.3f} "
              f"step={aggregate.mean_step_efficiency:.3f} comp={aggregate.mean_compaction_efficiency:.3f}) "
              f"→ composite {aggregate.composite:.4f}")
    for failure in suite.failures:
        print(f"  FAILURE: {failure.scenario}/{failure.strategy}: {failure.code} "
              f"({failure.description})")
    for entry in suite.sensitivity:
        print(f"  weights[{entry.weights_name}]: winner={entry.winner} "
              f"delta={entry.score_delta:+.4f} {entry.composites}")


if __name__ == "__main__":
    main()

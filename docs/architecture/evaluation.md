# Evaluation Contract

The canonical benchmark is offline, deterministic, includes a tool call, context growth and at least one compaction. Both strategies receive the exact same fixture.

Metrics:

```text
task_completed       = 0 or 1
tool_success_rate    = successful_tool_calls / total_tool_calls
tool_call_count      = total_tool_calls
execution_steps      = LLM calls + Tool calls
peak_context_tokens  = max snapshot total_tokens
final_context_tokens = final snapshot total_tokens
compaction_count     = completed compactions
```

The documented score is:

```text
0.35 * task_completed + 0.20 * tool_success_rate
+ 0.20 * context_efficiency + 0.15 * step_efficiency
+ 0.10 * compaction_efficiency
```

Normalization and denominator behavior for zero-tool and zero-compaction runs must be implemented as named functions and covered by tests. Evaluation is rule-based; no LLM Judge is required for MVP.

## Weight rationale (review R6)

- `task_completed = 0.35` — an incomplete run is useless regardless of the other terms; completion dominates but does not zero out efficiency signals (a failed run still scores up to 0.65).
- `tool_success_rate = 0.20` — reliability of the execution surface; equal in weight to context efficiency because both are core product claims.
- `context_efficiency = 0.20` — the product's central question ("how much context did the run consume?"); headroom against the budget is the measurable form.
- `step_efficiency = 0.15` — punishes thrashing (more steps than the reference plan) without letting a single extra step dominate.
- `compaction_efficiency = 0.10` — compaction is a means, not a goal; a run that never needed it is not penalized (neutral 1.0).

Sensitivity: the suite re-scores the same aggregates under one alternate,
documented weight set (`reliability-heavy-v1`: 0.40/0.25/0.20/0.10/0.05) and
reports both winners and deltas, so the reader sees how the verdict depends
on the weights instead of taking one number on faith.

## Versioned scenario suite (review R6)

`packages.experiments.suite.run_suite` runs both strategies on **three
versioned, identical fixtures** (`SCENARIO_SUITE_VERSION = "1"`):

1. `irrelevant_large_output` — a huge irrelevant tool observation; checks that compaction triggers, the goal survives, the blob does not leak into the answer, and the final answer is intact. Both strategies keep the verbatim blob by contract, so the zero token recovery is recorded honestly.
2. `critical_early_decision` — an early decision must survive compaction; `semantic_state` preserves it in its structured state, `keep_recent_summary` (documented to keep only task + latest tool result) legitimately fails this check and it is reported as a failure case.
3. `repeated_failed_tool` — three failing tool calls; checks that failures are recorded, never become artifacts, and the run still completes.

Reporting order: **raw per-scenario metrics first, composite score second**;
the aggregate lists the mean raw metrics before its composite. Results
include per-scenario metrics, aggregate metrics and failure cases; repeated
runs are byte-for-byte stable (fixed session ids, shared deterministic
estimator). Comparisons disclose the estimator identity and reject mismatches.

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


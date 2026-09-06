# Context Engine Contract

The fixed prompt section order is:

```text
base_system, agent_role, project_context, relevant_memory,
relevant_skills, current_task, runtime_context, recent_messages, tool_results
```

`PromptBuilder` emits a `PromptSnapshot`. `ContextManager` emits a `ContextSnapshot` before each model call. `TokenBudgetManager` estimates every component and reserves output tokens. `CompactionEngine` runs only when the budget threshold is reached and returns a structured `CompactionResult` with removed message IDs and preserved state.

Token estimation uses `tiktoken` when installed and a documented deterministic fallback otherwise. All snapshots record the estimator name so results are comparable.


# Context Engine Contract

The fixed prompt section order is:

```text
base_system, agent_role, project_context, relevant_memory,
relevant_skills, current_task, runtime_context, recent_messages, tool_results
```

`PromptBuilder` emits a `PromptSnapshot`. `ContextManager` emits a `ContextSnapshot` before each model call. `TokenBudgetManager` estimates every component and reserves output tokens. `CompactionEngine` runs only when the budget threshold is reached and returns a structured `CompactionResult` with removed message IDs and preserved state.

## Token counting contract (review R5)

Token estimation uses `tiktoken` when installed and a documented deterministic
fallback otherwise. All snapshots record the estimator name; ContextSnapshots
additionally record the estimator's `metadata` (formula / encoding / model
hint) under `metadata["estimator_metadata"]`.

Exactly what is serialized into a count:

- **Messages**: each message is counted as the rendered text `"{role}: {content}"` (`count_by_role`).
- **Tools**: a tool observation enters the transcript as a `role="tool"` message with the JSON `{"ok", "value", "error"}` envelope and is counted as that JSON text. Tool *specs* sent to the provider are not part of the transcript and are not counted.
- **Structured state**: a compaction summary/state message is a rendered `role="system"` message (fixed template or `_render_state` output) and is counted as that text.

The deterministic fallback formula is versioned in its name (`deterministic-v1`):

```text
count(text) = 0 if text == "" else max(1, ceil(len(text) / 4))
```

`len` counts Python characters (not bytes), so counts are identical across
platforms and supported Python versions. Versioned audit fixtures (Chinese
text, JSON, tool schema, empty content — `packages.context.fixture.AUDIT_TEXTS`)
anchor the exact counts in tests and Evidence.

**Disclosure rule**: fallback counts are an engineering proxy, not provider
billing tokens. Comparisons across different estimators are rejected
(`EstimatorMismatchError`); every comparison report discloses the estimator
identity it was measured with.

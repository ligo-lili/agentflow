# ADR-003: Prompt and Context Snapshots Are First-class

## Status

Accepted

## Decision

Before each model call, persist a PromptSnapshot and ContextSnapshot with section/component token counts, estimator metadata and compaction state.

## Rationale

The primary product question is what the model saw. A mutable message list cannot answer that question after the fact.


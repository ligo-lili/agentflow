# Architecture Overview

```text
apps/api ───────────────┐
apps/web templates ─────┤
                         ▼
observability ◄── runtime ──► context
      │                 │          │
      ▼                 ▼          ▼
   SQLite            tools      snapshots
      │
      ▼
   evals / experiments
```

The runtime owns execution. Context owns prompt construction, budget checks and compaction. Observability consumes events and persists snapshots. API and Inspector are read/query surfaces. Evaluation consumes persisted events and snapshots; it does not alter runtime behavior.

## Dependency rule

`runtime` may depend on typed interfaces from `context` and observability, but must not depend on FastAPI or web templates. API routes depend on stores and DTOs, never on SQLite implementation details. Provider adapters are replaceable.


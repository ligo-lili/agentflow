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

## Write path and read path

```text
WRITE PATH (one run)                                   READ PATH (inspection)
────────────────────                                   ──────────────────────
AgentSession.run()                                     Inspector / API (apps/api)
  │ SessionStarted ──► EventBus ──► SqlitePersistence     │  GET /api/sessions…
  │                                      │                ▼
  ├─► PromptBuilder ─ PromptSnapshot ────┤            SessionTimelineBuilder
  ├─► ContextManager ─ ContextSnapshot ──┤            SessionReplayer ──► SessionReplay
  ├─► AgentLoop                                            │  (events + snapshots only;
  │    ├─ LLMCallStarted/Finished ──► bus                  │   never a Provider/Tool)
  │    ├─ ToolCallStarted/Finished ─► bus                  ▼
  │    └─ AgentFinished/AgentFailed ► bus              Evaluator ──► EvaluationReport
  │                                                    experiments.suite ──► SuiteReport
  └─ every event: append-only events row
     + session projection (one transaction)
```

Both paths meet only at the append-only event log and the snapshot stores:
the write path never reads them back mid-run, and the read path never
executes a provider or tool. The one write-side projection rule is atomic
(persistence contract): a terminal session status cannot exist without its
terminal event, and `rebuild_projections()` re-derives the projection from
the log.

## Dependency rule

`runtime` may depend on typed interfaces from `context` and observability, but must not depend on FastAPI or web templates. API routes depend on stores and DTOs, never on SQLite implementation details. Provider adapters are replaceable (FakeModelProvider by default; optional OpenAI-compatible adapter).

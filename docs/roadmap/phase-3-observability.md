# Phase 3 — Observatory, Replay and Inspector (T5/T6)

## Goal

Allow a developer to open a session, inspect its timeline and snapshots, and replay recorded events without executing the Agent again.

## API

Implement:

```text
POST /api/runs
GET /api/sessions
GET /api/sessions/{id}
GET /api/sessions/{id}/events
GET /api/sessions/{id}/context-snapshots
GET /api/sessions/{id}/prompt-snapshots
GET /api/sessions/{id}/replay
GET /api/sessions/{id}/evaluation
```

## Inspector acceptance

The local page can create an offline run and display Session Timeline, Prompt Sections, Context Breakdown, snapshot history, Tool Calls, Compaction before/after, Replay and Evaluation Summary. Replay must not call a provider or tool.

Evidence: `docs/evidence/phase-3/replay-report.md` and `docs/evidence/phase-3/inspector-report.md`.


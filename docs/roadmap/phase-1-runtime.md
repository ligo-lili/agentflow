# Phase 1 — Runtime and Persistence (T1/T2)

## Goal

Run one deterministic Agent task, execute a tool, emit an ordered trace and reload it after process restart.

## Runtime acceptance

- Session creation and bounded Agent Loop.
- Fake Provider returns a scripted Tool Call and final answer.
- Tool success and failure are represented by events.
- Required lifecycle events appear in monotonic sequence order.
- Provider exceptions produce `AgentFailed` with a redacted diagnostic.

## Persistence acceptance

- SQLite initializes at `.agentflow/agentflow.db`.
- Sessions, events and JSON payloads round-trip after restart.
- Event order is preserved by `sequence`.
- Missing session IDs produce a typed not-found error.

## Demo

```bash
python examples/simple_agent.py
```

Evidence: `docs/evidence/phase-1/runtime-report.md` and `docs/evidence/phase-1/persistence-report.md`.


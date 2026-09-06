# API Contract

The FastAPI app (`apps/api/main.py`) is a read/query surface over the
stores. Routes depend on Store Protocols and typed DTOs — never on SQLite
details or the Agent Loop. Request/response models live in
`apps/api/schemas.py`; query objects are the package models (`SessionTimeline`,
`SessionReplay`, `PromptSnapshot`, `ContextSnapshot`, `EvaluationReport`).

## Endpoints

| Method | Path | Success model | Errors |
|---|---|---|---|
| POST | `/api/runs` | `RunResponse` | 422, 500 |
| GET | `/api/sessions` | `list[SessionSummary]` | 500 |
| GET | `/api/sessions/{id}` | `SessionDetail` | 404, 500 |
| GET | `/api/sessions/{id}/events` | `SessionTimeline` | 404, 500 |
| GET | `/api/sessions/{id}/prompt-snapshots` | `list[PromptSnapshot]` | 404, 500 |
| GET | `/api/sessions/{id}/context-snapshots` | `list[ContextSnapshot]` | 404, 500 |
| GET | `/api/sessions/{id}/replay` | `SessionReplay` | 404, 409, 500 |
| GET | `/api/sessions/{id}/evaluation` | `EvaluationReport` | 404, 409, 500 |

Every route declares a `response_model`, so the OpenAPI document contains
the full success and error schemas.

## Run endpoint semantics

`POST /api/runs` executes **synchronously inside the request** and is
**offline-only in the MVP**: the session runs on the deterministic
`FakeModelProvider` — no network, no API key, no background jobs. The
`task` field is bounded (`max_length = 2000`); invalid input returns `422`.

## Error envelope

All errors share one shape with stable machine-readable codes:

```json
{"error": {"code": "REPLAY_INVALID", "message": "replay integrity check failed",
           "details": {"integrity": {"valid": false, "errors": [...], "...": "..."}}}}
```

| Code | Status | Meaning |
|---|---|---|
| `SESSION_NOT_FOUND` | 404 | unknown session id |
| `REPLAY_INVALID` | 409 | event log fails the replay integrity check (details carry the `ReplayIntegrity` report) |
| `VALIDATION_ERROR` | 422 | request body invalid (field locations only — raw input is never echoed) |
| `STORE_UNAVAILABLE` | 500 | storage backend failure |
| `NOT_FOUND` / `HTTP_ERROR` | — | unmatched routes / framework errors |

Redaction rule: internal diagnostics (which may contain file paths or
implementation details) are logged server-side through the redaction
scrubber; clients only ever receive the safe public message. Tests prove
that paths, secrets and raw exception strings do not reach responses.

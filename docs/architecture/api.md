# API Contract

The FastAPI app (`apps/api/main.py`) is a read/query surface over the
stores. Routes depend on Store Protocols and typed DTOs — never on SQLite
details or the Agent Loop. Request/response models live in
`apps/api/schemas.py`; query objects are the package models (`SessionTimeline`,
`SessionReplay`, `PromptSnapshot`, `ContextSnapshot`, `EvaluationReport`).

## Endpoints

| Method | Path | Success model | Errors |
|---|---|---|---|
| POST | `/api/runs` | `RunResponse` (**202**) | 409, 422, 429, 500 |
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

`POST /api/runs` accepts two mutually exclusive modes (`scenario` **xor**
`task`, otherwise `422`) and executes **in the background** (Phase 6.2):

- **`scenario`** (offline demo): deterministic runs on the scripted
  `FakeModelProvider` — no network, no API key. Unchanged MVP behavior:
  `simple` / `compaction` scripts, bounded 500-token budget.
- **`task`** (custom run, Phase 6): the submitted `task` (bounded at 2000
  characters) executes on the provider configured at startup via
  `AGENTFLOW_PROVIDER=openai-compat` (credentials from env). Optional
  `tools` (names resolved against the operator-declared registry from
  `AGENTFLOW_TOOLS_MODULE`), `system_prompt`, and `max_steps` (1–32) tune
  the run. Provider/tool timeouts and the context budget come from the
  documented env defaults (`docs/architecture/deployment.md`).

Accept flow: the request pre-creates the session projection row
(`status="running"`), submits the run to the bounded queue
(`AGENTFLOW_RUN_WORKERS`, default 2), and returns **202** with
`{"session_id", "scenario", "status": "running"}`. Clients poll
`GET /api/sessions/{id}` until the status is `finished`/`failed`. When every
worker is busy the run is rejected with **429 `RUN_QUEUE_FULL`** — clients
retry; nothing is queued invisibly. On startup, sessions still `running`
from a previous process receive a terminal `AgentFailed`
(`phase="startup_sweep"`) in the same transaction as the projection flip.

| Code | Status | Meaning (run endpoint) |
|---|---|---|
| `PROVIDER_NOT_CONFIGURED` | 409 | task mode requested but no real provider is configured at startup |
| `VALIDATION_ERROR` | 422 | body invalid, or requested tool names not in the registry (details list `unknown` and `available`) |
| `RUN_QUEUE_FULL` | 429 | all run workers busy; retry shortly |

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
| `PROVIDER_NOT_CONFIGURED` | 409 | task runs need a real provider (`AGENTFLOW_PROVIDER`) |
| `RUN_QUEUE_FULL` | 429 | all run workers are busy |
| `VALIDATION_ERROR` | 422 | request body invalid (field locations only — raw input is never echoed) |
| `STORE_UNAVAILABLE` | 500 | storage backend failure |
| `NOT_FOUND` / `HTTP_ERROR` | — | unmatched routes / framework errors |

Redaction rule: internal diagnostics (which may contain file paths or
implementation details) are logged server-side through the redaction
scrubber; clients only ever receive the safe public message. Tests prove
that paths, secrets and raw exception strings do not reach responses.

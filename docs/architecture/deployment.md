# Deployment Guide (single machine)

AgentFlow ships as one self-contained service: FastAPI app + SQLite storage
+ background run queue. This guide covers the supported deployment boundary
and every environment variable. For architecture contracts see the sibling
documents; for the roadmap scope (what is deliberately NOT here) see
`docs/roadmap/phase-6-production.md`.

## Run modes

Local (development / offline demos):

```bash
python -m pip install -e ".[dev]"
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Docker (single host, survives restarts via a named volume):

```bash
docker compose up -d --build
```

The compose file reads `.env` (never committed) and mounts `agentflow-data`
at `/data`; `AGENTFLOW_DB_PATH=/data/agentflow.db` is set in the image.

## Environment variables (complete table)

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFLOW_PROVIDER` | *(unset)* | Provider for `POST /api/runs` task mode. `openai-compat` enables real runs; unset ⇒ task mode answers `409 PROVIDER_NOT_CONFIGURED`. Scenario mode is unaffected. |
| `AGENTFLOW_BASE_URL` | — | OpenAI-compatible endpoint base URL, e.g. `https://host/v1` (required when provider is set). |
| `AGENTFLOW_API_KEY` | — | Endpoint API key; stored privately, never logged (required when provider is set). |
| `AGENTFLOW_MODEL` | — | Model name the endpoint serves (required when provider is set). |
| `AGENTFLOW_TIMEOUT_SECONDS` | `30` | HTTP deadline inside the OpenAI-compatible adapter. |
| `AGENTFLOW_TOOLS_MODULE` | *(unset)* | Python module (dotted path or `.py` file) exposing `TOOLS: Sequence[Tool]`; loaded at startup, validated strictly. See `examples/agentflow_tools.py`. |
| `AGENTFLOW_MAX_CONTEXT_TOKENS` | `4000` | Context budget ceiling for task runs (deterministic estimator tokens). |
| `AGENTFLOW_RESERVED_OUTPUT_TOKENS` | `200` | Output reservation inside the budget. |
| `AGENTFLOW_PROVIDER_TIMEOUT_SECONDS` | `60` | Per-model-call deadline for task runs (loop-level). |
| `AGENTFLOW_TOOL_TIMEOUT_SECONDS` | `30` | Per-tool-call deadline for task runs. |
| `AGENTFLOW_DB_PATH` | `.agentflow/agentflow.db` | SQLite file location. The directory is created on demand; the file holds events, snapshots and the session projection. |
| `AGENTFLOW_RUN_WORKERS` | `2` | Background run queue width. Capacity = workers; overflow gets `429 RUN_QUEUE_FULL`. |
| `AGENTFLOW_AUTH_TOKEN` | *(unset)* | When set, every `/api/*` request needs `Authorization: Bearer <token>` (constant-time compare). `GET /healthz` and the Inspector page stay reachable; the browser prompts once and stores the token in localStorage. Unset ⇒ no auth (localhost usage). |
| `AGENTFLOW_CORS_ORIGINS` | *(unset)* | Comma-separated allowed origins for the CORS middleware (e.g. `https://ops.example.com`). Unset ⇒ same-origin only. |

Invalid values (non-numeric numbers, bad tools module, bad provider
credentials) fail at **startup** with a message naming the variable — never
mid-request.

## Trust boundaries

- **Tools module = operator code.** `AGENTFLOW_TOOLS_MODULE` is imported by
  the server process and may do anything the process can. That is the
  design: tools are declared by the person who owns the deployment, never
  submitted by API clients. Treat the module like server configuration.
- **Auth is transport-level only.** One bearer token, no per-user identity:
  suited to a single operator or a trusted small team. Multi-user/RBAC is a
  recorded non-goal.
- **SQLite = single-process storage.** WAL + a lock-guarded connection
  serve one API process. Do not point two containers at the same file; do
  not place the DB on a network filesystem. Scale-up path is a Postgres
  backend behind the same Store Protocols (future work, recorded).
- **Backup = copy the file.** With the queue drained (no `running`
  sessions), copying `AGENTFLOW_DB_PATH` (plus `-wal`/`-shm` if present)
  captures a consistent snapshot.

## Health and logs

- `GET /healthz` — liveness, never authenticated (used by the Docker
  HEALTHCHECK).
- Run crashes are logged redacted via the `agentflow.api` logger; clients
  only ever see typed envelopes.
- Sessions still `running` after a crash/restart receive a terminal
  `AgentFailed (phase="startup_sweep")` on the next startup.

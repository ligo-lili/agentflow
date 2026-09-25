# AgentFlow

**The Context Debugger and Observatory for AI Agents.**

[![CI](https://github.com/ligo-lili/agentflow/actions/workflows/ci.yml/badge.svg)](https://github.com/ligo-lili/agentflow/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

AgentFlow is a job-portfolio MVP for inspecting and evaluating an Agent's runtime behavior: prompt assembly, context composition, tool calls, context growth, compaction and execution trajectory.

## MVP flow

```text
offline task → Agent Runtime → Prompt/Context snapshots → Tool calls
→ Context growth → Compaction → persisted trace → Inspector → Replay
→ trajectory evaluation → strategy comparison
```

All of the above is implemented and offline-deterministic. The runtime uses a scripted `FakeModelProvider` by default, so every demo, test and benchmark runs without network access or an API key.

Recorded runs are guarded by explicit contracts: provider/tool deadlines that can never report timed-out work as success, versioned SQLite schema with an atomic session projection, formal replay integrity (corrupt or truncated traces are rejected, live ones still replay), typed API responses with a stable error envelope, and auditable token counting with estimator-mismatch rejection (docs/architecture/{runtime,persistence,event-model,api,context-engine,evaluation}.md). The A/B evaluation runs a versioned three-scenario suite with semantic survival checks and weight-sensitivity reporting.

## What it looks like

The local Inspector — per-step timeline, prompt sections with token
breakdown, context budget history, compaction before/after, replay from the
event log alone:

![Inspector session view](docs/evidence/review-r9-r10/screenshots/desktop-deeplink-compaction-session.png)

A corrupted trace is flagged, never silently replayed:

![Replay integrity](docs/evidence/review-r9-r10/screenshots/corrupt-trace-replay-panel.png)

## What is inside

| Task | Deliverable | Where |
|---|---|---|
| T0 | Typed contracts: events, provider, tools, snapshots, stores | `packages/core`, `packages/observability` |
| T1 | Single-agent runtime: `AgentSession` → bounded `AgentLoop` → `ToolRuntime` | `packages/runtime` |
| T2 | SQLite persistence (events, snapshots, sessions) + reload | `packages/observability/sqlite.py` |
| T3 | Prompt/Context snapshots, token budget (`PromptBuilder`, `ContextManager`) | `packages/context` |
| T4 | Two deterministic compaction strategies (`keep_recent_summary`, `semantic_state`) | `packages/context/compaction.py` |
| T5 | Timeline and replay from the event log alone (no re-execution) | `packages/observability/{timeline,replay}.py` |
| T6 | FastAPI query API + local Web Inspector | `apps/api`, `apps/web/templates` |
| T7 | Rule-based trajectory evaluation and A/B strategy comparison | `packages/evals`, `packages/experiments` |

Design decisions are recorded in `docs/adr/`; per-task evidence reports (with actual command output) live in `docs/evidence/`.

## Quick start

```bash
python -m pip install -e ".[dev]"
make test        # 198 offline tests
make lint        # ruff + mypy (strict on packages and apps)
make coverage    # 95% measured, 80% floor enforced
make demo        # simple agent with a tool call, prints the event trace
make web         # Inspector UI at http://127.0.0.1:8000
```

### Demos

```bash
python examples/simple_agent.py          # runtime: event trace, tool call, final answer
python examples/context_growth_demo.py   # per-step context growth under a token budget
python examples/persistence_reload.py    # two real processes: write to SQLite, reload, compare
python examples/replay_demo.py           # replay a session from the event log alone
python examples/compaction_compare.py    # A/B: both compaction strategies, metrics, score, winner
```

### Inspector

`make web` (or `uvicorn apps.api.main:app`) serves the local Inspector. The page creates offline runs, submits custom tasks to the server-configured provider (with named tools), and shows Session Timeline, Prompt Sections, Context Breakdown, snapshot history, Tool Calls, Compaction before/after, Replay (from the event log — never calls a provider or tool) and the Evaluation Summary. The API also exposes the raw surfaces: `GET /api/sessions`, `/events`, `/prompt-snapshots`, `/context-snapshots`, `/replay`, `/evaluation`.

### Deploy (single machine)

Runs execute on a bounded background queue — `POST /api/runs` accepts with `202` and clients poll the session endpoints until the projection reports `finished`/`failed` (the Inspector does this automatically). Full variable table and trust boundaries live in [docs/architecture/deployment.md](docs/architecture/deployment.md).

```bash
docker compose up -d --build      # or: uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Server-side configuration (all optional; see the deployment guide):

- `AGENTFLOW_PROVIDER=openai-compat` + `AGENTFLOW_BASE_URL` / `AGENTFLOW_API_KEY` / `AGENTFLOW_MODEL` — enable real-model task runs;
- `AGENTFLOW_TOOLS_MODULE` — operator-declared tools module (see `examples/agentflow_tools.py`);
- `AGENTFLOW_AUTH_TOKEN` — require a bearer token on `/api/*` (`/healthz` stays open);
- `AGENTFLOW_RUN_WORKERS`, `AGENTFLOW_DB_PATH`, budgets and timeouts.

## Scope

The MVP is an offline-first Python runtime with SQLite persistence, a FastAPI query API and a lightweight web Inspector. It does not include a coding-agent replacement, voice/browser/desktop automation, multi-tenant SaaS, vector-first RAG, model training or complex multi-agent orchestration.

## Status: single-machine ready — bounded on purpose

The four-week MVP scope is **complete and verified** (see
[docs/evidence/final-report.md](docs/evidence/final-report.md)), and Phase 6
added what real single-machine use requires: real-model task runs with
operator-declared tools, background execution with polling, optional bearer
auth, and a Docker deployment ([docs/evidence/phase-6-*/](docs/evidence/README.md)).
That is a different claim from production readiness. Deliberate non-goals
that would gate broader use:

- **Single-process SQLite** behind the Store Protocols (WAL, one writer
  process); the Postgres backend is future work.
- **One bearer token, no multi-tenancy**: suited to an operator or a small
  trusted team, not the public internet.
- **Token counts are an engineering proxy** (documented deterministic
  formula, or tiktoken when installed) — never provider billing tokens;
  the gap against real usage is calibrated and documented.
- **Rule-based task completion**: evaluation checks runtime completion, not
  semantic answer correctness; no LLM judge by design.
- **No streaming**: providers are single-shot `invoke`; timelines are
  poll-based.

## Documentation map

- [Product vision](docs/product/vision.md), [target user](docs/product/target-user.md), [scope](docs/product/scope.md), [interview script](docs/product/interview-script.md)
- [Architecture overview](docs/architecture/overview.md), [runtime](docs/architecture/runtime.md), [persistence](docs/architecture/persistence.md), [context engine](docs/architecture/context-engine.md), [event model](docs/architecture/event-model.md), [API](docs/architecture/api.md), [evaluation](docs/architecture/evaluation.md), [deployment](docs/architecture/deployment.md)
- [Roadmap and task ownership](docs/roadmap/README.md)
- [Architecture decisions](docs/adr/)
- [Evidence index](docs/evidence/README.md) — per-task reports and the [final portfolio report](docs/evidence/final-report.md)

## Success criteria

The project succeeds when a developer can explain why an Agent behaved a certain way, inspect prompt/context lifecycle, replay recorded events without re-running tools or models, and prove that two context strategies are measurably different. All four are demonstrated in `docs/evidence/final-report.md`.

## License

[MIT](LICENSE)

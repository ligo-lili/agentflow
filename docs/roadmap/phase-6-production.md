# Phase 6 — Production usability (post-MVP, approved scope)

Goal: make AgentFlow usable for real work on a single machine — a
self-hosted service where an operator configures one OpenAI-compatible
endpoint and one tools module, then submits real tasks through the API or
Inspector, runs them against a real model in the background, and inspects /
replays them like any recorded session.

Deliberate non-goals (recorded, revisit only as new phases): PostgreSQL
backend, multi-user/RBAC auth, SSE streaming timelines, client-supplied tool
code execution.

## Tasks

| ID | Deliverable | Depends on | Evidence |
|---|---|---|---|
| T9 | Real-provider task runs: provider factory (`AGENTFLOW_PROVIDER`), tool parameter schemas, operator tools module (`AGENTFLOW_TOOLS_MODULE`), task mode on `POST /api/runs`, env-configured budgets/timeouts/DB path, faithful tool-call continuation | T8 | `docs/evidence/phase-6-1-task-runs/report.md` |
| T10 | Background runs: 202 + polling, bounded run queue, stale-running sweep, Inspector auto-refresh | T9 | `docs/evidence/phase-6-2-background-runs/report.md` |
| T11 | Deployment: optional bearer-token auth, CORS, `/healthz`, Dockerfile + compose, deployment docs | T10 | `docs/evidence/phase-6-3-deployment/report.md` |

Contract guards that stay in force: offline tests remain the default and
never need a provider or key; scenario runs stay byte-compatible; a bad
provider/tools configuration fails at startup, never mid-request; tool
modules are operator-trusted code (documented boundary), never
client-supplied.

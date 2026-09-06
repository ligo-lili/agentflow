# AgentFlow Review Improvement Plan

## 1. Purpose

This document turns the post-MVP review into an executable improvement backlog for coding agents. It is not a new MVP roadmap and must not be used to expand the product into multi-agent, RAG, SaaS or real-time infrastructure.

The goal is to strengthen three claims before using AgentFlow as a job-portfolio project:

1. The implementation matches the documented contracts.
2. Recorded runs remain trustworthy under failures and incompatible data.
3. A reviewer can reproduce the project and understand the experiment without relying on assertions in the Evidence reports.

## 2. Verified Baseline

Verified on 2026-09-06:

```text
Source packages: present
FastAPI/Web Inspector: present
Examples: 5
Tests: 118 passed
Ruff: passed
mypy --strict: passed for 36 source files
Evidence reports: phase 0-4 and final report present
```

Commands used:

```powershell
python -m pytest -q -p no:cacheprovider `
  --basetemp "E:\agentflow\.review-pytest-tmp-20260906"
python -m ruff check .
python -m mypy packages
```

The custom pytest directory was required because the managed review environment could not access the user's default pytest temp directory. This was an environment error, not a test assertion failure.

The earlier preliminary finding that the repository lacked runtime code, examples and tests is obsolete. Those assets now exist and must not be reimplemented.

## 3. Prioritized Findings

### P0 - Resolve Before Calling the Project Release-ready

#### R1. Align the runtime timeout contract with implementation

Problem:

- `docs/architecture/runtime.md` promises explicit timeout handling.
- `AgentLoopConfig` currently has a step limit but no provider/tool timeout.
- `docs/evidence/final-report.md` correctly lists model-call timeout as a known limitation.

Required change:

- Either implement configurable provider and tool deadlines, or remove the timeout claim from the architecture contract.
- Preferred portfolio choice: implement injectable timeout policy without coupling the core runtime to a specific provider SDK.
- Emit `AgentFailed` with `phase`, timeout category and redacted diagnostic.

Acceptance:

- Provider timeout and tool timeout have separate tests.
- Timed-out work cannot be reported as success.
- Timeout values are visible in configuration and Evidence.
- Offline tests remain deterministic and do not use long sleeps.

#### R2. Make SQLite consistency and schema evolution explicit

Problem:

- Three stores maintain separate SQLite connections.
- Session status and event append are separate commits, so a failure can leave projections inconsistent.
- Schema DDL has no metadata/migration version table.
- Snapshot writes use `INSERT OR REPLACE`, which weakens the claim that recorded evidence is immutable.
- WAL, foreign keys and cross-process write behavior are not specified.

Required change:

- Add a schema metadata table and a single migration entry point.
- Enable and verify foreign keys; decide whether WAL is enabled for the local API workload.
- Make event append and related session projection update atomic, or document and test a recovery/rebuild path.
- Reject duplicate snapshot IDs instead of silently replacing evidence.
- Document the supported concurrency boundary: local single process unless explicitly expanded.

Acceptance:

- Fresh database and upgrade-from-previous-schema tests pass.
- Injected write failure cannot produce a terminal session without its terminal event, or the projection can be deterministically rebuilt.
- Duplicate event/snapshot IDs produce typed store errors.
- `PRAGMA integrity_check` passes in an integration test.

#### R3. Strengthen Replay integrity semantics

Problem:

- Replay checks sequence gaps, but does not yet expose a formal integrity result.
- It does not visibly validate mixed `trace_id`, unsupported event schema versions, duplicate lifecycle events, unmatched tool/compaction pairs or missing terminal events.
- A partial trace can therefore look like a valid replay with `status=running` or incomplete step data.

Required change:

- Add an integrity model with `valid`, `warnings`, `errors`, event range and supported schema versions.
- Validate one session/trace identity, contiguous unique sequence, supported event version, balanced start/finish pairs and terminal-state consistency.
- Distinguish a legitimate running session from a corrupted or truncated session.
- Keep Replay read-only: never call a Provider or Tool during validation.

Acceptance:

- Tests cover sequence gap, duplicate sequence, mixed trace IDs, unsupported schema, missing tool finish, missing compaction finish and conflicting terminal events.
- API returns `409` for invalid replay data using a stable error code.
- Valid historic traces still replay deterministically.

#### R4. Publish stable API DTO and error contracts

Problem:

- API handlers return generic `dict[str, Any]` values rather than declared response models.
- Error responses use FastAPI's raw `detail` shape.
- `StoreError` text is returned to clients, which may leak paths or implementation details.
- Request size/task limits and synchronous run behavior are not documented.

Required change:

- Define Pydantic request/response DTOs for all eight endpoints.
- Define one error envelope with stable codes such as `SESSION_NOT_FOUND`, `REPLAY_INVALID` and `STORE_UNAVAILABLE`.
- Log redacted internal diagnostics while returning a safe public message.
- Document that `POST /api/runs` is synchronous and offline-only in the MVP.
- Bound user task length and reject invalid inputs with `422`.

Acceptance:

- Every route declares `response_model` and documented status codes.
- OpenAPI contains the success and error schemas.
- Tests prove file paths, secrets and raw exception strings are not returned.
- Existing Inspector behavior remains compatible.

### P1 - High-value Portfolio Improvements

#### R5. Make token measurements auditable

Current strength:

- `DeterministicEstimator` documents its formula and versions the estimator as `deterministic-v1`.
- Snapshots record the estimator name.

Remaining change:

- Document exactly which serialized content is counted for messages, tools and structured state.
- Record model/encoding metadata when using `tiktoken`.
- Add fixtures containing Chinese text, JSON, tool schemas and empty content.
- Prevent experiments using different estimators from being presented as directly comparable.

Acceptance:

- The same fixture produces identical deterministic counts across supported Python versions.
- Comparison rejects or clearly flags estimator mismatch.
- Evidence states that fallback counts are an engineering proxy, not provider billing tokens.

#### R6. Improve evaluation validity beyond one fixture

Problem:

- The current A/B result is deterministic but the score delta is small and based on one canonical scenario.
- Default `task_completed` checks runtime completion, not semantic answer correctness.
- A single weighted score can obscure trade-offs among quality, steps and context use.

Required change:

- Keep the current rule-based benchmark, but add a small versioned suite with at least three scenarios: irrelevant large tool output, critical early decision preservation and repeated failed tool output.
- Add scenario-specific deterministic assertions for semantic artifacts that must survive compaction.
- Report raw metrics first and composite score second.
- Document why each weight exists and perform a simple sensitivity comparison with at least one alternate weight set.

Acceptance:

- Both strategies run on identical versioned fixtures.
- Results include per-scenario metrics, aggregate metrics and failure cases.
- Repeated runs are byte-for-byte stable apart from generated IDs/timestamps.
- No LLM Judge or network access is introduced.

#### R7. Add one optional real-provider smoke path

Problem:

- The Fake Provider is correct for deterministic tests, but the portfolio does not yet prove that the Provider protocol maps cleanly to a real OpenAI-compatible endpoint.

Required change:

- Implement one optional OpenAI-compatible adapter behind the existing Protocol.
- Keep it outside default tests and require explicit environment configuration.
- Add a mocked HTTP contract test and a manual smoke-test document.
- Preserve request/response usage data and redact credentials/errors.

Acceptance:

- The offline suite remains the default and passes without API keys.
- Mocked tests cover normal response, tool call, provider error and timeout.
- The adapter does not leak SDK types into core models.

#### R8. Raise release verification quality

Problem:

- CI covers Python 3.11/3.13 only on Ubuntu, while the project is also developed and demonstrated on Windows.
- `pytest-cov` is installed but no coverage threshold is enforced.
- The current environment surfaced a Starlette/TestClient dependency deprecation warning.

Required change:

- Add Windows to a focused CI job or matrix entry.
- Enforce a meaningful core-package coverage floor, starting at 80%, without chasing coverage on templates or trivial DTOs.
- Pin/test a compatible FastAPI, Starlette and HTTP client set; remove the deprecation warning through supported dependency versions.
- Add an import/build smoke test for the existing `packages` layout.

Acceptance:

- Linux and Windows CI are green on supported Python versions.
- CI runs tests, Ruff, mypy strict and package build/import checks.
- Coverage is reported and the agreed threshold is enforced.
- No known dependency deprecation warning appears in the standard test run.

### P2 - Improve Later, Without Blocking the Portfolio

#### R9. Inspector polish and visual verification

- Add stable deep links or URL state for selected sessions.
- Improve empty, loading, corrupt-trace and API-error states.
- Add responsive checks for desktop and narrow screens.
- Add a screenshot-based portfolio walkthrough using real local run data.

Do not replace Jinja/vanilla JavaScript with React solely for appearance.

#### R10. Documentation and interview narrative

- Add one architecture diagram showing write path and read path.
- Add a five-minute interview script centered on one failed run and the A/B result.
- Keep limitations prominent: deterministic estimator, single process, no auth and rule-based task completion.
- Separate "MVP complete" from "production-ready" throughout README and Evidence.

#### R11. Package layout decision

The current `packages/` layout works and passes type checks. Do not migrate to `src/agentflow/` during stabilization unless publishing/import ergonomics become a concrete problem. If a migration is later approved, perform it as one isolated mechanical task with import, wheel and example smoke tests.

## 4. Recommended Execution Order

```text
R1 Contract alignment
├── R2 SQLite integrity
├── R3 Replay integrity
└── R4 API contracts

R2 + R3 + R4
├── R5 Token auditability
├── R6 Evaluation suite
└── R8 Release verification

R1-R6 stable
└── R7 Optional real provider

After technical stabilization
└── R9-R10 Portfolio polish
```

Recommended release sequence:

1. Complete R1-R4 and tag a correctness-focused release candidate.
2. Complete R5, R6 and R8 and regenerate all Evidence from clean CI.
3. Add R7 only if there is enough time; it must not weaken offline reproducibility.
4. Finish with R9-R10 for the job-portfolio presentation.

## 5. Coding-agent Task Template

Assign one recommendation ID per task. Do not ask an agent to "fix all review issues" in one turn.

```text
START REVIEW TASK RX

Read:
- AGENTS.md
- docs/review-improvement-plan.md
- the architecture/ADR files relevant to RX
- the implementation and tests relevant to RX

Before coding, report:
- verified current behavior
- owned files
- public interfaces affected
- compatibility risks
- tests to add

Implement only RX. Run focused tests, then the full test/lint/type-check suite.
Update the relevant architecture docs and add actual verification output to a
new evidence section. Do not start another recommendation automatically.
```

## 6. Required Test Matrix

| Area | Required scenarios |
|---|---|
| Runtime | success, Provider failure, Tool failure, max steps, Provider timeout, Tool timeout |
| Context | fixed section order, empty components, multilingual text, oversized Tool output, estimator mismatch |
| Compaction | both strategies, preserved fields, removed IDs, still-over-budget failure, paired events |
| Persistence | fresh schema, migration, restart, duplicate IDs, injected write failure, integrity check |
| Replay | no execution, gap, duplicate, mixed trace, unsupported schema, unmatched pairs, terminal conflict |
| Evaluation | zero tools, zero compactions, failed task, semantic fixture assertions, repeatability |
| API | 404/409/422/500 envelopes, response schemas, task limits, diagnostic redaction |
| Release | Linux/Windows CI, wheel build/import, coverage threshold, five offline demos |

## 7. Final Acceptance Gate

The improvement cycle is complete only when:

```text
[ ] Architecture contracts match actual behavior.
[ ] SQLite writes and schema upgrades have deterministic integrity behavior.
[ ] Replay distinguishes valid, running, truncated and corrupt traces.
[ ] Every API endpoint has typed success/error contracts.
[ ] Token comparisons disclose estimator identity and reject mismatches.
[ ] A/B results cover multiple versioned deterministic scenarios.
[ ] Linux and Windows CI pass with an enforced coverage floor.
[ ] Standard tests emit no known dependency deprecation warning.
[ ] README and Evidence distinguish MVP completeness from production readiness.
[ ] No Multi-Agent, SaaS, RAG or real-time scope was added.
```

After completing the gate, regenerate `docs/evidence/final-report.md` from actual clean-run output rather than editing old result numbers by hand.

# Roadmap and Task Ownership

The MVP is a four-week, dependency-gated delivery. Agents must stop at the end of their assigned task.

```text
T0 Foundation
├── T1 Runtime
├── T2 Persistence
└── T3 Context
    └── T4 Compaction
T1+T2+T3+T4
├── T5 Observatory/Replay
├── T6 API/Web Inspector
└── T7 Evaluation/Experiment
T1-T7 → T8 Integration
```

## Task contract

Every task must provide owned files, interfaces, tests, demo command, evidence path and known limitations. Do not alter another task's owned files without reporting an interface issue first.

## Tasks

| ID | Deliverable | Depends on | Evidence |
|---|---|---|---|
| T0 | Project setup, contracts, CI, docs | — | `docs/evidence/phase-0/report.md` |
| T1 | Single-Agent runtime, tools, fake provider | T0 | `docs/evidence/phase-1/runtime-report.md` |
| T2 | SQLite stores and reload | T0 | `docs/evidence/phase-1/persistence-report.md` |
| T3 | Prompt/context snapshots and budget | T0 | `docs/evidence/phase-2/prompt-context-report.md` |
| T4 | Two compaction strategies | T3 | `docs/evidence/phase-2/compaction-report.md` |
| T5 | Timeline and replay | T1,T2,T3,T4 | `docs/evidence/phase-3/replay-report.md` |
| T6 | FastAPI + Web Inspector | T2,T5 | `docs/evidence/phase-3/inspector-report.md` |
| T7 | Evaluation and A/B comparison | T1,T3,T4,T5 | `docs/evidence/phase-4/report.md` |
| T8 | Integration, README, portfolio evidence | T1-T7 | `docs/evidence/final-report.md` |

Detailed acceptance criteria live in the phase files below.


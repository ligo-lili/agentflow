# Phase 2 — Context Engine and Compaction (T3/T4)

## Goal

Make every model-call context inspectable and prove that budget pressure triggers explainable compaction.

## Deliverables

- Fixed prompt section ordering and `PromptSnapshot`.
- `ContextSnapshot` before every model call.
- Token budget with estimator name, reserved output tokens and component breakdown.
- `keep_recent_summary` and `semantic_state` strategies.
- Compaction events, before/after token counts, removed IDs and structured preserved state.

## Acceptance

- Breakdown sum equals total tokens.
- Threshold trigger has an explicit reason.
- After compaction fits the configured budget.
- Both strategies are deterministic on the canonical fixture.
- Goal, current state, decisions, artifacts, tool findings and pending tasks are preserved as fields.

## Demo

```bash
python examples/context_growth_demo.py
```

Evidence: `docs/evidence/phase-2/prompt-context-report.md` and `docs/evidence/phase-2/compaction-report.md`.


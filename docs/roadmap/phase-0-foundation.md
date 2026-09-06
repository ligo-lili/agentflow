# Phase 0 — Foundation (T0)

## Goal

Create an installable, typed, testable repository and freeze the public contracts.

## Deliverables

- `pyproject.toml`, `Makefile`, `.env.example`, `.gitignore`, `AGENTS.md`.
- Package skeleton for runtime, context, memory, observability, evals and experiments.
- Product/architecture/ADR documentation and CI running test + lint.
- Pydantic models and Protocol interfaces for events, provider, tools, snapshots and stores.

## Acceptance

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check .
mypy packages
```

All commands pass on a clean checkout. `evidence/phase-0/report.md` records commands, output and repository tree.


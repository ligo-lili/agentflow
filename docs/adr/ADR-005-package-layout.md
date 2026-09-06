# ADR-005: Keep the `packages/` layout during stabilization

Date: 2026-09-06
Status: Accepted (review improvement R11)

## Context

The review improvement plan asked whether to migrate the repository layout
from `packages/` (namespace-style packages directly at the repo root) to a
`src/agentflow/` single-distribution layout.

## Decision

Keep the current `packages/` layout. Do not migrate during the
stabilization window. If a migration is later approved (e.g. publishing to
PyPI under one distribution name becomes a concrete need), perform it as one
isolated mechanical task with import, wheel and example smoke tests.

## Rationale

- The layout works: `mypy --strict` passes over all packages, the wheel
  build is verified by a content smoke check (`scripts/check_wheel.py`,
  review R8), and every module imports cleanly
  (`tests/test_package_layout.py`).
- The project is a portfolio MVP, not a published library; import ergonomics
  (`packages.core.events`) are acceptable and explicit.
- A layout migration touches every import in code, tests and docs for zero
  behavioral benefit — exactly the "fake complexity" the engineering rules
  forbid during stabilization.

## Consequences

- `pyproject.toml` keeps `packages = ["packages"]` for the wheel target.
- Consumers continue importing `packages.<domain>...`; `apps/` stays outside
  the wheel (application layer, run from source).

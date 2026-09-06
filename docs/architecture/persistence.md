# Persistence Contract

SQLite is the durable backend for events, snapshots and session projections
(`packages/observability/sqlite.py`). The guarantees below are enforced by
tests in `tests/test_sqlite_integrity.py`.

## Schema versioning and migration

- The current schema version is stamped in the `schema_metadata` table
  (`key = 'schema_version'`, value `2`).
- `apply_migrations(conn)` is the single migration entry point; every
  connection runs it on open. Versioned steps run in one immediate
  transaction, so a failed upgrade leaves the previous schema untouched.
- Legacy databases (tables without `schema_metadata`) upgrade in place:
  version 2 adds the metadata table plus a denormalized `events.event_type`
  column backfilled from the stored JSON payloads.

## Immutability of recorded evidence

- Events are append-only; duplicate `event_id`s raise `StoreError`.
- Snapshots are immutable evidence: duplicate `snapshot_id`s raise
  `StoreError` instead of silently replacing the recorded row (both SQLite
  and the in-memory reference store).

## Atomic projection

- `SqlitePersistence` writes the session projection (created at
  `SessionStarted`, terminal status at `AgentFinished`/`AgentFailed`) and the
  event row in **one transaction on one shared connection**. An injected
  write failure can never produce a terminal session status without its
  terminal event — both writes roll back together.
- `SqlitePersistence.rebuild_projections()` is the deterministic recovery
  path: it re-derives every session row from the append-only event log
  (identity from `SessionStarted`, status from the last terminal event,
  `running` when none exists) and is itself atomic.

## PRAGMAs and concurrency boundary

- `foreign_keys = ON` is enabled and verified on every connection. No
  cross-table FK constraints are declared because the stores are
  independently usable (e.g. snapshot stores without a session row);
  cross-store integrity is maintained by the projection layer above.
- `journal_mode = WAL` and `synchronous = NORMAL` are set for the local API
  workload: read-heavy, short single-writer transactions, readers never
  block the writer. `busy_timeout = 5000` absorbs lock contention.
- **Supported boundary: one process.** Connections are shared safely across
  threads through the owning store lock (the API serves sync endpoints from
  a threadpool). Multi-process writers and network filesystems are out of
  scope; `PRAGMA integrity_check` must pass after full sessions.

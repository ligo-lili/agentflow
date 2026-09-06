# Event Model

Every event has `event_id`, `session_id`, `trace_id`, monotonic `sequence`, UTC `timestamp`, `event_type`, JSON payload and `schema_version`.

Required lifecycle events are `SessionStarted`, `PromptBuilt`, `ContextBuilt`, `LLMCallStarted`, `LLMCallFinished`, `ToolCallStarted`, `ToolCallFinished`, `ContextCompactionStarted`, `ContextCompactionFinished`, `AgentFinished` and `AgentFailed`.

The EventBus fans events to an in-process subscriber and the EventStore. Persistence is append-only for the MVP. Consumers sort by sequence, not wall-clock timestamp. Payloads must be serializable and secrets must be redacted before persistence.

## Replay integrity contract

Replay (`packages/observability/replay.py`) is a read-only query: it never
calls a Provider or Tool. Every load first runs a formal integrity check
(`check_integrity`) that returns a `ReplayIntegrity` with:

- `valid` — true when there are no errors;
- `errors` / `warnings` — typed issues with stable codes;
- `event_range` — `(min_sequence, max_sequence)` of the log;
- `schema_versions` — distinct versions seen, validated against
  `SUPPORTED_SCHEMA_VERSIONS` (currently `{"1.0"}`).

Error codes (an invalid log raises `ReplayError` carrying the full report;
the API maps this to `409 REPLAY_INVALID`):

| Code | Meaning |
|---|---|
| `SEQUENCE_GAP` | a sequence in `0..n-1` is missing |
| `DUPLICATE_SEQUENCE` | two events claim one sequence |
| `MIXED_TRACE_IDS` | events from more than one trace |
| `UNSUPPORTED_SCHEMA_VERSION` | version outside the supported set |
| `MISSING_SESSION_START` | the log has no `SessionStarted` |
| `CONFLICTING_TERMINAL_EVENTS` | both `AgentFinished` and `AgentFailed` |
| `DUPLICATE_TERMINAL_EVENT` | more than one terminal event of one kind |
| `UNMATCHED_TOOL_CALL` | tool start without finish in a terminated log |
| `UNMATCHED_COMPACTION` | compaction start without finish in a terminated log |

Warnings mark legitimate or recoverable shapes and keep `valid = true`:
`NO_TERMINAL_EVENT` (a contiguous log without terminal event is a running
session, replayed with `status="running"`), `UNMATCHED_TOOL_CALL` /
`UNMATCHED_COMPACTION` while the session is still in flight,
`UNMATCHED_LLM_CALL` (a provider failure or timeout legitimately ends the
session without an `LLMCallFinished`), and `ORPHAN_*_FINISH` (replay can
reconstruct from the finish payload alone).

This distinction means a truncated or corrupted trace can never look like a
valid replay, while a live session paused mid-step still replays.

# Event Model

Every event has `event_id`, `session_id`, `trace_id`, monotonic `sequence`, UTC `timestamp`, `event_type`, JSON payload and `schema_version`.

Required lifecycle events are `SessionStarted`, `PromptBuilt`, `ContextBuilt`, `LLMCallStarted`, `LLMCallFinished`, `ToolCallStarted`, `ToolCallFinished`, `ContextCompactionStarted`, `ContextCompactionFinished`, `AgentFinished` and `AgentFailed`.

The EventBus fans events to an in-process subscriber and the EventStore. Persistence is append-only for the MVP. Consumers sort by sequence, not wall-clock timestamp. Payloads must be serializable and secrets must be redacted before persistence.


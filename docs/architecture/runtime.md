# Runtime Contract

The runtime consists of `AgentSession`, `AgentLoop`, `ToolRuntime`, `ModelProvider` and stores.

```python
class ModelProvider(Protocol):
    def invoke(self, request: ModelRequest) -> ModelResponse: ...

class Tool(Protocol):
    name: str
    description: str
    def run(self, arguments: dict, context: ToolContext) -> ToolResult: ...
```

The loop is: build prompt, build context, emit snapshots, invoke model, execute requested tools, append observations, repeat, then emit `AgentFinished` or `AgentFailed`. It has a bounded step limit and explicit timeout/error handling. Provider and tool failures are represented in events and never silently swallowed.

## Tool schemas and faithful continuation (Phase 6)

- Tools may optionally declare `parameters_schema` (a JSON-schema mapping).
  `ToolRuntime.specs()` validates it (mapping, else `ToolRuntimeError`) and
  passes it to the provider, so real models can issue meaningful tool calls;
  tools without the attribute keep the empty default schema.
- The transcript stores the assistant's actual `tool_calls` on the assistant
  `ModelMessage` (`ModelMessage.tool_calls`). Continuation requests therefore
  replay the model's real arguments. The OpenAI-compatible adapter
  serializes stored calls verbatim; legacy transcripts recorded before
  Phase 6 (no stored calls) are still serialized by synthesizing
  `arguments="{}"` on the preceding assistant message — documented legacy
  behavior, degraded but compatible.

## Provider selection (Phase 6)

`packages/runtime/provider_factory.create_provider(env)` maps
`AGENTFLOW_PROVIDER` to a concrete provider at startup:

- `openai-compat` → `OpenAICompatProvider.from_env` (credentials via env);
- unset/`fake`/unknown → `ProviderConfigError` — there is no implicit real
  provider; the scripted `FakeModelProvider` is constructed directly by the
  offline scenario paths, never chosen for arbitrary tasks.

A configured-but-invalid provider fails at startup, never lazily
mid-request. Operator-declared tools come from
`AGENTFLOW_TOOLS_MODULE` (a Python module with `TOOLS: Sequence[Tool]`,
validated by `packages/runtime/tool_loader.py`); loading executes that
module — it is operator-configured, trusted code by design.

## Timeout contract

Deadlines are explicit configuration on `AgentLoopConfig`, enforced by an
injectable policy (`packages/runtime/timeouts.py`) that never imports a
provider SDK:

```python
class AgentLoopConfig(BaseModel):
    provider_timeout_seconds: float | None = None   # None disables the guard
    tool_timeout_seconds: float | None = None       # None disables the guard
```

- A deadline breach is **terminal**: the loop emits `AgentFailed` with
  `phase` (`"provider"` or `"tool"`), a redacted diagnostic, and a
  `timeout` object carrying the category (`kind`), the target (model or
  tool name) and the configured `timeout_seconds`. Timed-out work can never
  be reported as success — no `AgentFinished` is emitted for a timed-out run.
- A timed-out tool call still closes its boundary: a `ToolCallFinished`
  event with `ok=false` and the timeout diagnostic is recorded before the
  `AgentFailed`, so the event log keeps its start/finish pairing.
- A `TimeoutError` raised by the provider itself is *not* misreported as a
  policy breach: the failure carries no `timeout` object.
- Enforcement wraps the guarded call in a worker thread and abandons it on
  expiry (threads cannot be interrupted); the abandoned result is discarded.
- The timeout values are part of the recorded configuration: the
  `SessionStarted` payload carries `provider_timeout_seconds` and
  `tool_timeout_seconds`, so Evidence can show which deadlines were active.
- Offline tests use short sleeps (1 s work vs. 0.2 s deadline) and stay
  deterministic; with no timeouts configured, behavior is unchanged.

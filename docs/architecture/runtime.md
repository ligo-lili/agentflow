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


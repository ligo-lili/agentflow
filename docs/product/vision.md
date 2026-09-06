# Product Vision

AgentFlow turns an Agent Context Lifecycle from a black box into a visible, replayable and evaluable object.

The product records the chain `Agent Run → Prompt Assembly → Context Construction → Model/Tool Execution → Context Growth → Compaction → Event Trace → Inspection → Replay → Evaluation`. The primary outcome is diagnosis: a developer can identify whether a failure came from prompt design, injected context, memory, tool output, budget pressure, compaction or trajectory.

## Product principles

- Context is a first-class engineering artifact.
- Events are the integration boundary between runtime and tooling.
- Every optimization should be supported by reproducible evidence.
- One honest, inspectable runtime is more valuable than many simulated agents.


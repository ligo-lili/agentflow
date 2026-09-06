# AgentFlow Engineering Instructions

## Mission

AgentFlow is an Agent Runtime and Context Engineering workbench. Its core question is:

> Why did the Agent behave this way?

The MVP makes Prompt, Context, Memory, Tool Results, Context Growth, Compaction and Execution Trajectory inspectable, replayable and measurable. It is not a coding-agent replacement, chatbot, Agent marketplace or commercial LangSmith clone.

## Current delivery target

The current target is the four-week, offline-first MVP described in `docs/roadmap/`. The product surface is:

- typed Python runtime and context packages;
- CLI examples that are deterministic and reproducible;
- FastAPI read/query API;
- lightweight FastAPI + Jinja + vanilla JavaScript Inspector.

Use SQLite + JSON persistence. Use `FakeModelProvider` by default; the OpenAI-compatible adapter is optional and must never be required by tests.

## Non-negotiable rules

1. Implement only the currently assigned task and phase. Do not start future phases automatically.
2. Read the relevant product, architecture and roadmap document before coding.
3. Plan interfaces, data models, tests and risks before implementation.
4. Important lifecycle transitions emit `AgentEvent`; UI, replay and evaluation consume events instead of reaching into the Agent Loop.
5. Prompt and Context are first-class snapshots. Never hide context construction in `messages.append(...)` calls.
6. Avoid framework lock-in, hidden global state, fake complexity and untestable side effects.
7. Default tests and demos are offline, deterministic and runnable without an API key.
8. Do not log API keys, Authorization headers or unredacted secrets.
9. A feature is done only when implemented, tested, demonstrated, documented and backed by an evidence report.

## Ownership and coordination

Task boundaries and dependencies are defined in `docs/roadmap/`. Agents must modify only owned files. If an interface is insufficient, record the issue and report it before changing another task's files.

Before work, return:

```text
TASK PLAN
Goal:
Owned Files:
Interfaces Used:
Data Models:
Tests:
Risks:
Dependencies:
```

After work, return:

```text
# Task Completion Report
Task:
Goal:
Files Changed:
Interfaces Added or Used:
Tests:
Demo Command:
Evidence Path:
Known Limitations:
Follow-up Needed:
```

## Definition of done

Run the applicable tests, lint and example. Update the relevant `docs/evidence/phase-x/` report. Stop after the assigned task and wait for the next instruction.


# MVP Scope and Non-goals

## In scope

- Single-Agent runtime with model and tool loops.
- Prompt and Context snapshots before every model call.
- Token budget accounting and two compaction strategies.
- Event log, SQLite persistence and session reload.
- Timeline, snapshot query, replay without model/tool re-execution.
- Rule-based trajectory evaluation and A/B experiment comparison.
- Offline CLI demos, FastAPI API and lightweight Web Inspector.

## Out of scope

Voice, browser or desktop automation; coding-agent workflows; enterprise auth and multi-tenancy; vector-first RAG; automatic prompt mutation; model training; self-evolution; marketplace features; real-time collaboration; production cloud deployment; complex multi-agent orchestration. Multi-Agent exploration is a post-MVP proposal only.

## Fixed defaults

Python 3.11+, Pydantic 2, FastAPI, Jinja/vanilla JS, SQLite + JSON, deterministic Fake Provider and optional OpenAI-compatible Provider. The MVP is four weeks and optimized for Agent backend/infrastructure interview signal.


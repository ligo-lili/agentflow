"""FastAPI app: query the event log and snapshots, launch offline runs.

The API is a read/query surface over the stores (architecture overview):
routes depend on Store Protocols and the T5 query objects, never on SQLite
details or the Agent Loop. The only write endpoint is ``POST /api/runs``,
which launches a deterministic offline session through the standard runtime
wiring (FakeModelProvider, no network, no API key).

Evaluation (``GET /api/sessions/{id}/evaluation``) is part of the Phase 3
surface but its scoring engine is task T7; until then the endpoint responds
501 with a diagnostic instead of pretending to have results.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.errors import StoreError
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.evals.evaluator import EvaluationConfig, Evaluator
from packages.observability.eventbus import EventBus
from packages.observability.replay import ReplayError, SessionReplayer
from packages.observability.sqlite import (
    DEFAULT_DB_PATH,
    SqliteEventStore,
    SqlitePersistence,
    SqliteSessionStore,
    SqliteSnapshotStore,
)
from packages.observability.timeline import SessionTimelineBuilder
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "web" / "templates"),
    autoescape=select_autoescape(["html"]),
)

_REPORT_TEXT = (
    "AgentFlow makes agent behavior inspectable: prompts, context, tool "
    "results and compaction become measurable artifacts instead of hidden state."
)


class RunRequest(BaseModel):
    """Options for one offline run; both scenarios are fully deterministic."""

    scenario: Literal["simple", "compaction"] = "simple"
    task: str | None = None


class WordCountTool:
    name = "word_count"
    description = "Count the words in the provided text."

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value=len(str(arguments.get("text", "")).split()))


class LongTool:
    name = "long_tool"
    description = "Returns a long observation."

    def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value="x" * 1500)


def _simple_script() -> list[ModelResponse]:
    return [
        ModelResponse(
            message=ModelMessage(role="assistant", content=""),
            finish_reason="tool_calls",
            tool_calls=(
                ToolCallRequest(call_id="c1", name="word_count", arguments={"text": _REPORT_TEXT}),
            ),
        ),
        ModelResponse(
            message=ModelMessage(
                role="assistant", content=f"The report contains {len(_REPORT_TEXT.split())} words."
            ),
            finish_reason="stop",
        ),
    ]


def _compaction_script() -> list[ModelResponse]:
    return [
        ModelResponse(
            message=ModelMessage(role="assistant", content=""),
            finish_reason="tool_calls",
            tool_calls=(ToolCallRequest(call_id="c1", name="long_tool", arguments={}),),
        ),
        ModelResponse(
            message=ModelMessage(role="assistant", content="Report digested."),
            finish_reason="stop",
        ),
    ]


def run_offline_session(db_path: Path, request: RunRequest) -> dict[str, Any]:
    """Run one deterministic offline session and persist it via SQLite."""
    scenario = request.scenario
    task = request.task or (
        "Digest the long report." if scenario == "compaction" else "Count the words of the report."
    )
    provider = FakeModelProvider(
        _compaction_script() if scenario == "compaction" else _simple_script()
    )
    tools: tuple[Any, ...] = (LongTool(),) if scenario == "compaction" else (WordCountTool(),)

    bus = EventBus()
    persistence = SqlitePersistence(bus, db_path)
    snapshot_store = SqliteSnapshotStore(db_path)
    try:
        estimator = DeterministicEstimator()
        budget = TokenBudgetManager(BudgetConfig(max_context_tokens=500, reserved_output_tokens=50))
        session = AgentSession(
            task=task,
            provider=provider,
            tools=tools,
            config=AgentLoopConfig(max_steps=4, system_prompt="You are AgentFlow."),
            prompt_builder=PromptBuilder(estimator, snapshot_store),
            context_manager=ContextManager(
                estimator,
                budget,
                snapshot_store,
                compaction_engine=CompactionEngine(
                    estimator, budget, CompactionConfig(strategy="semantic_state")
                )
                if scenario == "compaction"
                else None,
            ),
            bus=bus,
        )
        result = session.run()
        return {
            "session_id": session.session_id,
            "scenario": scenario,
            "status": result.status,
            "answer": result.answer,
            "steps": result.steps,
            "event_count": len(session.events()),
        }
    finally:
        persistence.close()
        snapshot_store.close()


def create_app(db_path: Path | None = None) -> FastAPI:
    """Create the API app over one SQLite database (default ``.agentflow``)."""
    path = db_path or DEFAULT_DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.event_store = SqliteEventStore(path)
        app.state.snapshot_store = SqliteSnapshotStore(path)
        app.state.session_store = SqliteSessionStore(path)
        try:
            yield
        finally:
            app.state.event_store.close()
            app.state.snapshot_store.close()
            app.state.session_store.close()

    app = FastAPI(title="AgentFlow Inspector API", version="0.1.0", lifespan=lifespan)

    def _require_session(request: Request, session_id: str) -> None:
        events = request.app.state.event_store.get_session_events(session_id)
        if not events:
            raise HTTPException(
                status_code=404, detail=f"session {session_id!r} not found"
            )

    @app.post("/api/runs")
    def create_run(body: RunRequest, request: Request) -> dict[str, Any]:
        try:
            return run_offline_session(path, body)
        except StoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/sessions")
    def list_sessions(request: Request) -> list[dict[str, Any]]:
        event_store = request.app.state.event_store
        session_store = request.app.state.session_store
        sessions: list[dict[str, Any]] = []
        for session_id in event_store.session_ids():
            record = session_store.get_session(session_id)
            events = event_store.get_session_events(session_id)
            sessions.append(
                {
                    "session_id": session_id,
                    "task": record.task if record else "",
                    "model": record.model if record else "",
                    "status": record.status if record else "unknown",
                    "event_count": len(events),
                    "created_at": record.created_at.isoformat() if record else None,
                }
            )
        return sessions

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str, request: Request) -> dict[str, Any]:
        _require_session(request, session_id)
        record = request.app.state.session_store.get_session(session_id)
        events = request.app.state.event_store.get_session_events(session_id)
        counts: dict[str, int] = {}
        for event in events:
            counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
        return {
            "session_id": session_id,
            "task": record.task if record else "",
            "model": record.model if record else "",
            "status": record.status if record else "unknown",
            "created_at": record.created_at.isoformat() if record else None,
            "event_count": len(events),
            "event_type_counts": counts,
        }

    @app.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, request: Request) -> dict[str, Any]:
        _require_session(request, session_id)
        timeline = SessionTimelineBuilder(request.app.state.event_store).build(session_id)
        return timeline.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/prompt-snapshots")
    def prompt_snapshots(session_id: str, request: Request) -> list[dict[str, Any]]:
        _require_session(request, session_id)
        return [
            s.model_dump(mode="json")
            for s in request.app.state.snapshot_store.get_prompt_snapshots(session_id)
        ]

    @app.get("/api/sessions/{session_id}/context-snapshots")
    def context_snapshots(session_id: str, request: Request) -> list[dict[str, Any]]:
        _require_session(request, session_id)
        return [
            s.model_dump(mode="json")
            for s in request.app.state.snapshot_store.get_context_snapshots(session_id)
        ]

    @app.get("/api/sessions/{session_id}/replay")
    def session_replay(session_id: str, request: Request) -> dict[str, Any]:
        _require_session(request, session_id)
        try:
            replay = SessionReplayer(
                request.app.state.event_store, request.app.state.snapshot_store
            ).load(session_id)
        except ReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return replay.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/evaluation")
    def session_evaluation(session_id: str, request: Request) -> dict[str, Any]:
        _require_session(request, session_id)
        replay = SessionReplayer(
            request.app.state.event_store, request.app.state.snapshot_store
        ).load(session_id)
        report = Evaluator(EvaluationConfig()).evaluate(replay)
        return report.model_dump(mode="json")

    @app.get("/", response_class=HTMLResponse)
    def inspector() -> str:
        template = _TEMPLATES.get_template("inspector.html")
        return str(template.render())

    return app


app = create_app()

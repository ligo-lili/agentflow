"""FastAPI app: query the event log and snapshots, launch runs.

The API is a read/query surface over the stores (architecture overview):
routes depend on Store Protocols and typed DTOs (``apps.api.schemas`` and the
package query models), never on SQLite details or the Agent Loop.

``POST /api/runs`` has two modes. ``scenario`` runs are deterministic offline
demos on the scripted ``FakeModelProvider`` (no network, no API key). ``task``
runs execute a submitted task on the server-configured provider
(``AGENTFLOW_PROVIDER=openai-compat``, credentials via env) with optional
named tools from the operator-declared registry (``AGENTFLOW_TOOLS_MODULE``);
they are guarded by configured provider/tool timeouts and run synchronously
inside the request (background execution arrives with Phase 6.2).

Every route declares a ``response_model`` and documented error statuses. All
errors use the stable ``{"error": {"code", "message", "details"}}`` envelope
(review plan R4): internal diagnostics are logged redacted server-side while
clients receive safe public messages.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.config import ApiConfig
from apps.api.schemas import (
    ErrorResponse,
    RunRequest,
    RunResponse,
    SessionDetail,
    SessionSummary,
)
from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import CompactionConfig, CompactionEngine
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder
from packages.core.errors import StoreError
from packages.core.provider import ModelMessage, ModelProvider, ModelResponse, ToolCallRequest
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.tools import Tool, ToolContext, ToolResult
from packages.evals.evaluator import EvaluationConfig, EvaluationReport, Evaluator
from packages.observability.eventbus import EventBus
from packages.observability.replay import ReplayError, SessionReplay, SessionReplayer
from packages.observability.sqlite import (
    SqliteEventStore,
    SqlitePersistence,
    SqliteSessionStore,
    SqliteSnapshotStore,
)
from packages.observability.timeline import SessionTimeline, SessionTimelineBuilder
from packages.runtime.diagnostics import redact_diagnostic
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.provider_factory import ENV_PROVIDER, create_provider
from packages.runtime.session import AgentSession
from packages.runtime.tool_loader import load_tools

_LOGGER = logging.getLogger("agentflow.api")

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "web" / "templates"),
    autoescape=select_autoescape(["html"]),
)

_REPORT_TEXT = (
    "AgentFlow makes agent behavior inspectable: prompts, context, tool "
    "results and compaction become measurable artifacts instead of hidden state."
)


class ApiError(Exception):
    """One API failure with a stable error code and a safe public message."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


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


DEFAULT_SYSTEM_PROMPT = "You are AgentFlow."


def _execute_session(
    db_path: Path,
    *,
    task: str,
    provider: ModelProvider,
    tools: Sequence[Any],
    loop_config: AgentLoopConfig,
    budget_config: BudgetConfig,
    with_compaction: bool,
    scenario_label: str,
) -> RunResponse:
    """Run one session against its own SQLite files and persist everything.

    Each run owns its bus/persistence/snapshot-store connections and closes
    them afterwards, so concurrent runs never share SQLite connections.
    """
    bus = EventBus()
    persistence = SqlitePersistence(bus, db_path)
    snapshot_store = SqliteSnapshotStore(db_path)
    try:
        estimator = DeterministicEstimator()
        budget = TokenBudgetManager(budget_config)
        session = AgentSession(
            task=task,
            provider=provider,
            tools=tools,
            config=loop_config,
            prompt_builder=PromptBuilder(estimator, snapshot_store),
            context_manager=ContextManager(
                estimator,
                budget,
                snapshot_store,
                compaction_engine=CompactionEngine(
                    estimator, budget, CompactionConfig(strategy="semantic_state")
                )
                if with_compaction
                else None,
            ),
            bus=bus,
        )
        result = session.run()
        return RunResponse(
            session_id=session.session_id,
            scenario=scenario_label,
            status=result.status,
            answer=result.answer,
            steps=result.steps,
            event_count=len(session.events()),
        )
    finally:
        persistence.close()
        snapshot_store.close()


def run_offline_session(db_path: Path, request: RunRequest) -> RunResponse:
    """Run one deterministic offline scenario on the scripted fake provider."""
    scenario = request.scenario
    if scenario is None:
        raise ValueError("run_offline_session requires a scenario request")
    task = request.task or (
        "Digest the long report." if scenario == "compaction" else "Count the words of the report."
    )
    provider = FakeModelProvider(
        _compaction_script() if scenario == "compaction" else _simple_script()
    )
    tools: tuple[Any, ...] = (LongTool(),) if scenario == "compaction" else (WordCountTool(),)
    return _execute_session(
        db_path,
        task=task,
        provider=provider,
        tools=tools,
        loop_config=AgentLoopConfig(max_steps=4, system_prompt=DEFAULT_SYSTEM_PROMPT),
        budget_config=BudgetConfig(max_context_tokens=500, reserved_output_tokens=50),
        with_compaction=scenario == "compaction",
        scenario_label=scenario,
    )


def run_task_session(state: Any, body: RunRequest) -> RunResponse:
    """Run one custom task on the server-configured provider and registry tools."""
    provider: ModelProvider | None = state.provider
    if provider is None:
        raise ApiError(
            409,
            "PROVIDER_NOT_CONFIGURED",
            "no real provider is configured for task runs; set "
            f"{ENV_PROVIDER}=openai-compat (and its credentials) at startup",
        )
    registry: dict[str, Tool] = state.tool_registry
    unknown = [name for name in body.tools if name not in registry]
    if unknown:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "unknown tool name(s) requested",
            {"unknown": unknown, "available": sorted(registry)},
        )
    config: ApiConfig = state.config
    selected = [registry[name] for name in body.tools]
    return _execute_session(
        config.db_path,
        task=body.task or "",
        provider=provider,
        tools=selected,
        loop_config=AgentLoopConfig(
            model=getattr(provider, "model", "custom"),
            max_steps=body.max_steps or 8,
            system_prompt=body.system_prompt or DEFAULT_SYSTEM_PROMPT,
            provider_timeout_seconds=config.provider_timeout_seconds,
            tool_timeout_seconds=config.tool_timeout_seconds,
        ),
        budget_config=BudgetConfig(
            max_context_tokens=config.max_context_tokens,
            reserved_output_tokens=config.reserved_output_tokens,
        ),
        with_compaction=True,
        scenario_label="task",
    )


def _error_payload(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def _provider_from_env() -> ModelProvider | None:
    """Build the task-run provider at startup; ``None`` when not configured.

    A configured-but-invalid provider fails startup (explicit configuration:
    never discover a bad provider mid-request).
    """
    if not (os.environ.get(ENV_PROVIDER) or "").strip():
        return None
    return create_provider()


def create_app(
    db_path: Path | None = None,
    provider: ModelProvider | None = None,
    tools: Sequence[Tool] | None = None,
) -> FastAPI:
    """Create the API app over one SQLite database (default ``.agentflow``).

    ``provider``/``tools`` inject the task-run dependencies (tests and
    embedders); by default they come from ``AGENTFLOW_PROVIDER`` and
    ``AGENTFLOW_TOOLS_MODULE`` — a bad configuration fails at startup.
    """
    config = ApiConfig.from_env(db_path)
    registry_tools = load_tools() if tools is None else tools

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.config = config
        app.state.provider = provider if provider is not None else _provider_from_env()
        app.state.tool_registry = {tool.name: tool for tool in registry_tools}
        app.state.event_store = SqliteEventStore(config.db_path)
        app.state.snapshot_store = SqliteSnapshotStore(config.db_path)
        app.state.session_store = SqliteSessionStore(config.db_path)
        try:
            yield
        finally:
            app.state.event_store.close()
            app.state.snapshot_store.close()
            app.state.session_store.close()

    app = FastAPI(title="AgentFlow Inspector API", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field locations and messages only: the raw input is never echoed.
        details = [
            {"loc": [str(part) for part in error["loc"]], "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                "VALIDATION_ERROR", "request validation failed", {"errors": details}
            ),
        )

    @app.exception_handler(StoreError)
    async def handle_store_error(request: Request, exc: StoreError) -> JSONResponse:
        # The diagnostic (which may contain paths) stays in the server log,
        # redacted; clients get a stable code and a safe message only.
        _LOGGER.error("store failure: %s", redact_diagnostic(str(exc)))
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                "STORE_UNAVAILABLE", "storage backend temporarily unavailable"
            ),
        )

    def _require_session(request: Request, session_id: str) -> None:
        events = request.app.state.event_store.get_session_events(session_id)
        if not events:
            raise ApiError(
                404,
                "SESSION_NOT_FOUND",
                f"session {session_id!r} not found",
            )

    @app.post(
        "/api/runs",
        response_model=RunResponse,
        responses={
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
        summary="Run one session: offline demo scenario, or a custom task on the configured provider",
        description=(
            "Two mutually exclusive modes. scenario: deterministic offline demo on "
            "the scripted FakeModelProvider (no network). task: a custom task "
            "executed by the server-configured provider (AGENTFLOW_PROVIDER="
            "openai-compat + credentials), optionally calling named tools from the "
            "server-declared registry (AGENTFLOW_TOOLS_MODULE). Task runs are "
            "guarded by the configured provider/tool timeouts and context budget."
        ),
    )
    def create_run(body: RunRequest, request: Request) -> RunResponse:
        try:
            if body.scenario is not None:
                return run_offline_session(config.db_path, body)
            return run_task_session(request.app.state, body)
        except StoreError as exc:
            raise ApiError(
                500,
                "STORE_UNAVAILABLE",
                "storage backend temporarily unavailable",
            ) from exc

    @app.get(
        "/api/sessions",
        response_model=list[SessionSummary],
        responses={500: {"model": ErrorResponse}},
    )
    def list_sessions(request: Request) -> list[SessionSummary]:
        event_store = request.app.state.event_store
        session_store = request.app.state.session_store
        sessions: list[SessionSummary] = []
        for session_id in event_store.session_ids():
            record = session_store.get_session(session_id)
            events = event_store.get_session_events(session_id)
            sessions.append(
                SessionSummary(
                    session_id=session_id,
                    task=record.task if record else "",
                    model=record.model if record else "",
                    status=record.status if record else "unknown",
                    event_count=len(events),
                    created_at=record.created_at.isoformat() if record else None,
                )
            )
        return sessions

    @app.get(
        "/api/sessions/{session_id}",
        response_model=SessionDetail,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    def session_detail(session_id: str, request: Request) -> SessionDetail:
        _require_session(request, session_id)
        record = request.app.state.session_store.get_session(session_id)
        events = request.app.state.event_store.get_session_events(session_id)
        counts: dict[str, int] = {}
        for event in events:
            counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
        return SessionDetail(
            session_id=session_id,
            task=record.task if record else "",
            model=record.model if record else "",
            status=record.status if record else "unknown",
            created_at=record.created_at.isoformat() if record else None,
            event_count=len(events),
            event_type_counts=counts,
        )

    @app.get(
        "/api/sessions/{session_id}/events",
        response_model=SessionTimeline,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    def session_events(session_id: str, request: Request) -> SessionTimeline:
        _require_session(request, session_id)
        return SessionTimelineBuilder(request.app.state.event_store).build(session_id)

    @app.get(
        "/api/sessions/{session_id}/prompt-snapshots",
        response_model=list[PromptSnapshot],
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    def prompt_snapshots(session_id: str, request: Request) -> list[PromptSnapshot]:
        _require_session(request, session_id)
        store: SqliteSnapshotStore = request.app.state.snapshot_store
        return store.get_prompt_snapshots(session_id)

    @app.get(
        "/api/sessions/{session_id}/context-snapshots",
        response_model=list[ContextSnapshot],
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    def context_snapshots(session_id: str, request: Request) -> list[ContextSnapshot]:
        _require_session(request, session_id)
        store: SqliteSnapshotStore = request.app.state.snapshot_store
        return store.get_context_snapshots(session_id)

    @app.get(
        "/api/sessions/{session_id}/replay",
        response_model=SessionReplay,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def session_replay(session_id: str, request: Request) -> SessionReplay:
        _require_session(request, session_id)
        try:
            return SessionReplayer(
                request.app.state.event_store, request.app.state.snapshot_store
            ).load(session_id)
        except ReplayError as exc:
            raise ApiError(
                409,
                "REPLAY_INVALID",
                "replay integrity check failed",
                {"integrity": exc.integrity.model_dump(mode="json")},
            ) from exc

    @app.get(
        "/api/sessions/{session_id}/evaluation",
        response_model=EvaluationReport,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                   500: {"model": ErrorResponse}},
    )
    def session_evaluation(session_id: str, request: Request) -> EvaluationReport:
        _require_session(request, session_id)
        try:
            replay = SessionReplayer(
                request.app.state.event_store, request.app.state.snapshot_store
            ).load(session_id)
        except ReplayError as exc:
            raise ApiError(
                409,
                "REPLAY_INVALID",
                "replay integrity check failed",
                {"integrity": exc.integrity.model_dump(mode="json")},
            ) from exc
        return Evaluator(EvaluationConfig()).evaluate(replay)

    @app.get("/", response_class=HTMLResponse)
    def inspector() -> str:
        template = _TEMPLATES.get_template("inspector.html")
        return str(template.render())

    return app


app = create_app()

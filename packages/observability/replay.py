"""Session replay: rebuild what happened from the event log alone.

Replay answers "what did the model see and why did it act" for a recorded
session without calling a provider or executing a tool — the replayer reads
events and snapshots, nothing else (ADR-002). Events are paired by ``step``
and ``call_id``; snapshots are attached by ``snapshot_id`` when a snapshot
store is wired.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.core.errors import AgentFlowError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.stores import EventStore, SessionStore, SnapshotStore
from packages.observability.timeline import UnknownSessionError


class ReplayError(AgentFlowError):
    """Raised when the event log is inconsistent (e.g. a sequence gap)."""


class ToolCallReplay(BaseModel):
    """One recorded tool invocation and its outcome."""

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None
    value: Any = None
    error: str | None = None


class CompactionReplay(BaseModel):
    """One recorded compaction, before and after."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    trigger: str
    before_tokens: int
    after_tokens: int | None = None
    threshold: int | None = None
    removed_ids: list[str] = Field(default_factory=list)
    fits_budget: bool | None = None
    preserved_state: dict[str, Any] = Field(default_factory=dict)


class ReplayStep(BaseModel):
    """One model-call step: what the model saw, answered, and invoked."""

    model_config = ConfigDict(frozen=True)

    step: int
    model: str | None = None
    context_snapshot_id: str | None = None
    context_snapshot: ContextSnapshot | None = None
    context_total_tokens: int | None = None
    context_fits: bool | None = None
    response_content: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    tool_calls: tuple[ToolCallReplay, ...] = ()
    compaction: CompactionReplay | None = None


class SessionReplay(BaseModel):
    """The reconstructed session: state, per-step detail, terminal outcome."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    trace_id: str
    task: str = ""
    model: str = ""
    max_steps: int | None = None
    status: str = "unknown"  # running | finished | failed | unknown
    final_answer: str | None = None
    error: str | None = None
    prompt_snapshot_id: str | None = None
    prompt_snapshot: PromptSnapshot | None = None
    steps: tuple[ReplayStep, ...] = ()
    compactions: tuple[CompactionReplay, ...] = ()


class SessionReplayer:
    """Loads a recorded session from stores; execution is impossible here."""

    def __init__(
        self,
        event_store: EventStore,
        snapshot_store: SnapshotStore | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._events = event_store
        self._snapshots = snapshot_store
        self._sessions = session_store

    def load(self, session_id: str) -> SessionReplay:
        events = self._events.get_session_events(session_id)
        if not events:
            raise UnknownSessionError(f"no events recorded for session {session_id!r}")
        _validate_sequence(events)

        prompts = _by_id(self._snapshots.get_prompt_snapshots(session_id) if self._snapshots else [])
        contexts = _by_id(self._snapshots.get_context_snapshots(session_id) if self._snapshots else [])

        replay = SessionReplay(session_id=session_id, trace_id=events[0].trace_id)
        steps: dict[int, dict[str, Any]] = {}
        pending_compaction: CompactionReplay | None = None
        compactions: list[CompactionReplay] = []

        for event in events:
            payload = event.payload
            kind = event.event_type
            if kind == AgentEventType.SESSION_STARTED:
                replay = replay.model_copy(
                    update={
                        "task": str(payload.get("task", "")),
                        "model": str(payload.get("model", "")),
                        "max_steps": _optional_int(payload.get("max_steps")),
                        "status": "running",
                    }
                )
            elif kind == AgentEventType.PROMPT_BUILT:
                snapshot_id = payload.get("snapshot_id")
                replay = replay.model_copy(
                    update={
                        "prompt_snapshot_id": snapshot_id,
                        "prompt_snapshot": prompts.get(snapshot_id)
                        if isinstance(snapshot_id, str)
                        else None,
                    }
                )
            elif kind == AgentEventType.CONTEXT_COMPACTION_STARTED:
                pending_compaction = CompactionReplay(
                    strategy=str(payload.get("strategy", "")),
                    trigger=str(payload.get("trigger", "")),
                    before_tokens=int(payload.get("before_tokens", 0)),
                    threshold=_optional_int(payload.get("threshold")),
                )
            elif kind == AgentEventType.CONTEXT_COMPACTION_FINISHED:
                finished = payload
                compaction = pending_compaction or CompactionReplay(
                    strategy=str(finished.get("strategy", "")),
                    trigger="",
                    before_tokens=int(finished.get("before_tokens", 0)),
                )
                compaction = compaction.model_copy(
                    update={
                        "after_tokens": _optional_int(finished.get("after_tokens")),
                        "removed_ids": list(finished.get("removed_ids", [])),
                        "fits_budget": finished.get("fits_budget"),
                        "preserved_state": dict(finished.get("preserved_state", {})),
                    }
                )
                compactions.append(compaction)
                pending_compaction = compaction
            elif kind == AgentEventType.CONTEXT_BUILT:
                step = _step(payload)
                state = steps.setdefault(step, {"step": step, "tool_calls": {}})
                snapshot_id = payload.get("snapshot_id")
                state["context_snapshot_id"] = snapshot_id
                state["context_snapshot"] = (
                    contexts.get(snapshot_id) if isinstance(snapshot_id, str) else None
                )
                state["context_total_tokens"] = _optional_int(payload.get("total_tokens"))
                state["context_fits"] = payload.get("fits")
                state["compaction"] = pending_compaction
                pending_compaction = None
            elif kind == AgentEventType.LLM_CALL_STARTED:
                step = _step(payload)
                state = steps.setdefault(step, {"step": step, "tool_calls": {}})
                state["model"] = payload.get("model")
            elif kind == AgentEventType.LLM_CALL_FINISHED:
                step = _step(payload)
                state = steps.setdefault(step, {"step": step, "tool_calls": {}})
                state["response_content"] = payload.get("content")
                state["finish_reason"] = payload.get("finish_reason")
                state["usage"] = dict(payload.get("usage", {}))
            elif kind == AgentEventType.TOOL_CALL_STARTED:
                step = _step(payload)
                state = steps.setdefault(step, {"step": step, "tool_calls": {}})
                calls: dict[str, dict[str, Any]] = state["tool_calls"]
                calls[str(payload.get("call_id"))] = {
                    "call_id": str(payload.get("call_id")),
                    "name": payload.get("name"),
                    "arguments": dict(payload.get("arguments", {})),
                }
            elif kind == AgentEventType.TOOL_CALL_FINISHED:
                step = _step(payload)
                state = steps.setdefault(step, {"step": step, "tool_calls": {}})
                call = state["tool_calls"].setdefault(
                    str(payload.get("call_id")),
                    {"call_id": str(payload.get("call_id")), "name": payload.get("name")},
                )
                call["ok"] = payload.get("ok")
                call["value"] = payload.get("value")
                call["error"] = payload.get("error")
            elif kind == AgentEventType.AGENT_FINISHED:
                replay = replay.model_copy(
                    update={"status": "finished", "final_answer": payload.get("answer")}
                )
            elif kind == AgentEventType.AGENT_FAILED:
                replay = replay.model_copy(
                    update={"status": "failed", "error": payload.get("error")}
                )

        ordered = tuple(
            _build_step(steps[step]) for step in sorted(steps)
        )
        return replay.model_copy(
            update={"steps": ordered, "compactions": tuple(compactions)}
        )


def _validate_sequence(events: list[AgentEvent]) -> None:
    for expected, event in enumerate(events):
        if event.sequence != expected:
            raise ReplayError(
                f"event log has a sequence gap: expected {expected}, "
                f"got {event.sequence} ({event.event_type.value})"
            )


def _by_id(snapshots: list[Any]) -> dict[str, Any]:
    return {s.snapshot_id: s for s in snapshots}


def _step(payload: dict[str, Any]) -> int:
    step = payload.get("step")
    return int(step) if isinstance(step, int) else 0


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _build_step(state: dict[str, Any]) -> ReplayStep:
    calls = state.get("tool_calls", {})
    return ReplayStep(
        step=state["step"],
        model=state.get("model"),
        context_snapshot_id=state.get("context_snapshot_id"),
        context_snapshot=state.get("context_snapshot"),
        context_total_tokens=state.get("context_total_tokens"),
        context_fits=state.get("context_fits"),
        response_content=state.get("response_content"),
        finish_reason=state.get("finish_reason"),
        usage=state.get("usage", {}),
        compaction=state.get("compaction"),
        tool_calls=tuple(
            ToolCallReplay.model_validate(calls[call_id]) for call_id in sorted(calls)
        ),
    )

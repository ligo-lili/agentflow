"""Session replay: rebuild what happened from the event log alone.

Replay answers "what did the model see and why did it act" for a recorded
session without calling a provider or executing a tool — the replayer reads
events and snapshots, nothing else (ADR-002). Events are paired by ``step``
and ``call_id``; snapshots are attached by ``snapshot_id`` when a snapshot
store is wired.

Every load runs an integrity check first (:class:`ReplayIntegrity`): a log
with structural errors (sequence gaps, duplicate sequences, mixed trace ids,
unsupported schema versions, missing lifecycle pairs, conflicting terminal
events) raises :class:`ReplayError` carrying the report, so a truncated or
corrupted trace can never look like a valid replay. A contiguous log without
a terminal event is a legitimate in-flight session: it replays with
``status="running"`` and a warning instead of an error.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.core.errors import AgentFlowError
from packages.core.events import AgentEvent, AgentEventType
from packages.core.snapshots import ContextSnapshot, PromptSnapshot
from packages.core.stores import EventStore, SessionStore, SnapshotStore
from packages.observability.timeline import UnknownSessionError

#: Event schema versions this replayer understands. Logs written by any other
#: version are rejected as an integrity error instead of misinterpreted.
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})


class ReplayError(AgentFlowError):
    """Raised when the event log fails the integrity check.

    Carries the full :class:`ReplayIntegrity` report so API consumers can
    return structured diagnostics under a stable error code.
    """

    def __init__(self, message: str, integrity: ReplayIntegrity) -> None:
        super().__init__(message)
        self.integrity = integrity


class ReplayIssue(BaseModel):
    """One integrity finding, with a stable machine-readable code."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: Literal["error", "warning"]
    message: str
    sequence: int | None = None


class ReplayIntegrity(BaseModel):
    """Formal integrity result for one event log."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    errors: tuple[ReplayIssue, ...] = ()
    warnings: tuple[ReplayIssue, ...] = ()
    event_range: tuple[int, int] | None = None
    schema_versions: tuple[str, ...] = ()


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
    integrity: ReplayIntegrity


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

        integrity = check_integrity(events)
        if integrity.errors:
            summary = "; ".join(issue.message for issue in integrity.errors)
            raise ReplayError(
                f"replay integrity check failed for session {session_id!r}: {summary}",
                integrity,
            )

        prompts = _by_id(self._snapshots.get_prompt_snapshots(session_id) if self._snapshots else [])
        contexts = _by_id(self._snapshots.get_context_snapshots(session_id) if self._snapshots else [])

        replay = SessionReplay(
            session_id=session_id,
            trace_id=events[0].trace_id,
            integrity=integrity,
        )
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


def check_integrity(events: list[AgentEvent]) -> ReplayIntegrity:
    """Validate one event log and return the formal integrity result.

    Read-only by contract: no provider or tool is ever consulted. Errors mark
    a log that cannot be trusted (structural corruption or truncation);
    warnings mark recoverable or legitimate in-flight shapes.
    """
    errors: list[ReplayIssue] = []
    warnings: list[ReplayIssue] = []

    sequences = [event.sequence for event in events]
    event_range = (min(sequences), max(sequences))
    counts = Counter(sequences)
    for sequence in sorted(s for s, n in counts.items() if n > 1):
        errors.append(
            ReplayIssue(
                code="DUPLICATE_SEQUENCE",
                severity="error",
                message=f"duplicate event sequence {sequence}",
                sequence=sequence,
            )
        )
    for expected in range(len(events)):
        if expected not in counts:
            errors.append(
                ReplayIssue(
                    code="SEQUENCE_GAP",
                    severity="error",
                    message=f"sequence gap: no event with sequence {expected}",
                    sequence=expected,
                )
            )

    reference_trace = events[0].trace_id
    for event in events:
        if event.trace_id != reference_trace:
            errors.append(
                ReplayIssue(
                    code="MIXED_TRACE_IDS",
                    severity="error",
                    message=(
                        f"mixed trace ids: event at sequence {event.sequence} has "
                        f"trace {event.trace_id!r}, expected {reference_trace!r}"
                    ),
                    sequence=event.sequence,
                )
            )

    versions = sorted({event.schema_version for event in events})
    for event in events:
        if event.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            errors.append(
                ReplayIssue(
                    code="UNSUPPORTED_SCHEMA_VERSION",
                    severity="error",
                    message=(
                        f"unsupported event schema version {event.schema_version!r} "
                        f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
                    ),
                    sequence=event.sequence,
                )
            )
            break

    if not any(event.event_type == AgentEventType.SESSION_STARTED for event in events):
        errors.append(
            ReplayIssue(
                code="MISSING_SESSION_START",
                severity="error",
                message="no SessionStarted event; the log cannot be attributed to a session",
                sequence=events[0].sequence,
            )
        )

    finished = [e for e in events if e.event_type == AgentEventType.AGENT_FINISHED]
    failed = [e for e in events if e.event_type == AgentEventType.AGENT_FAILED]
    if finished and failed:
        errors.append(
            ReplayIssue(
                code="CONFLICTING_TERMINAL_EVENTS",
                severity="error",
                message=(
                    "conflicting terminal events: both AgentFinished "
                    f"(sequence {finished[0].sequence}) and AgentFailed "
                    f"(sequence {failed[0].sequence}) present"
                ),
                sequence=finished[0].sequence,
            )
        )
    for duplicated in (finished, failed):
        if len(duplicated) > 1:
            errors.append(
                ReplayIssue(
                    code="DUPLICATE_TERMINAL_EVENT",
                    severity="error",
                    message=(
                        f"{len(duplicated)} {duplicated[0].event_type.value} events "
                        "(exactly one terminal event is required)"
                    ),
                    sequence=duplicated[1].sequence,
                )
            )
    terminal_present = bool(finished or failed)

    # Tool calls are paired by call_id; the loop always closes a tool boundary
    # before the session ends, so an open call in a terminated log is
    # corruption/truncation. Without a terminal event the session may simply
    # still be executing that tool.
    open_tools: dict[str, int] = {}
    for event in events:
        call_id = str(event.payload.get("call_id", ""))
        if event.event_type == AgentEventType.TOOL_CALL_STARTED:
            open_tools[call_id] = event.sequence
        elif (
            event.event_type == AgentEventType.TOOL_CALL_FINISHED
            and open_tools.pop(call_id, None) is None
        ):
            warnings.append(
                ReplayIssue(
                    code="ORPHAN_TOOL_CALL_FINISH",
                    severity="warning",
                    message=(
                        f"ToolCallFinished for call {call_id!r} has no matching start"
                    ),
                    sequence=event.sequence,
                )
            )
    for call_id, sequence in sorted(open_tools.items(), key=lambda item: item[1]):
        _report_pair_issue(
            errors,
            warnings,
            terminal_present,
            code="UNMATCHED_TOOL_CALL",
            message=f"tool call {call_id!r} started at sequence {sequence} but never finished",
            sequence=sequence,
        )

    # Compactions pair sequentially: every STARTED needs one FINISHED.
    open_compactions: list[int] = []
    for event in events:
        if event.event_type == AgentEventType.CONTEXT_COMPACTION_STARTED:
            open_compactions.append(event.sequence)
        elif event.event_type == AgentEventType.CONTEXT_COMPACTION_FINISHED:
            if open_compactions:
                open_compactions.pop()
            else:
                warnings.append(
                    ReplayIssue(
                        code="ORPHAN_COMPACTION_FINISH",
                        severity="warning",
                        message="ContextCompactionFinished has no matching start",
                        sequence=event.sequence,
                    )
                )
    for sequence in open_compactions:
        _report_pair_issue(
            errors,
            warnings,
            terminal_present,
            code="UNMATCHED_COMPACTION",
            message=f"compaction started at sequence {sequence} but never finished",
            sequence=sequence,
        )

    # An LLM call may legitimately stay open: a provider failure or timeout
    # fails the session without an LLMCallFinished event.
    open_llm: dict[int, int] = {}
    for event in events:
        step = event.payload.get("step")
        if isinstance(step, int):
            if event.event_type == AgentEventType.LLM_CALL_STARTED:
                open_llm[step] = event.sequence
            elif event.event_type == AgentEventType.LLM_CALL_FINISHED:
                open_llm.pop(step, None)
    for step, sequence in sorted(open_llm.items(), key=lambda item: item[1]):
        warnings.append(
            ReplayIssue(
                code="UNMATCHED_LLM_CALL",
                severity="warning",
                message=(
                    f"LLM call for step {step} started at sequence {sequence} but never "
                    "finished (provider failure, timeout, or the session is in flight)"
                ),
                sequence=sequence,
            )
        )

    if not terminal_present:
        warnings.append(
            ReplayIssue(
                code="NO_TERMINAL_EVENT",
                severity="warning",
                message=(
                    "no terminal event; the session may still be running or the log "
                    "may be truncated"
                ),
                sequence=events[-1].sequence,
            )
        )

    return ReplayIntegrity(
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        event_range=event_range,
        schema_versions=tuple(versions),
    )


def _report_pair_issue(
    errors: list[ReplayIssue],
    warnings: list[ReplayIssue],
    terminal_present: bool,
    *,
    code: str,
    message: str,
    sequence: int,
) -> None:
    """File an unmatched lifecycle pair: corruption in a terminated log,
    an in-flight warning otherwise."""
    issue = ReplayIssue(
        code=code,
        severity="error" if terminal_present else "warning",
        message=message,
        sequence=sequence,
    )
    (errors if terminal_present else warnings).append(issue)


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

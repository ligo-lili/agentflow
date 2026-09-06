"""AgentLoop: the bounded execution loop defined by the runtime contract.

Loop shape per docs/architecture/runtime.md: assemble messages, invoke the
model, execute requested tools, append observations, repeat; finish with
``AgentFinished`` or ``AgentFailed``.

T3 integration: when a ``PromptBuilder``/``ContextManager`` is wired, the
loop emits ``PromptBuilt`` (once per run) and ``ContextBuilt`` (before every
model call) and persists the corresponding snapshots. Without them the loop
behaves exactly as in Phase 1 — the transcript stays minimal until T3's
components are injected.
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder, PromptSections
from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelProvider, ModelRequest
from packages.core.tools import ToolContext, ToolResult
from packages.runtime.diagnostics import redact_diagnostic
from packages.runtime.recorder import EventRecorder
from packages.runtime.timeouts import TimeoutExceededError, TimeoutPolicy
from packages.runtime.tools import ToolRuntime


class AgentLoopConfig(BaseModel):
    """Bounded-loop configuration.

    ``provider_timeout_seconds`` / ``tool_timeout_seconds`` enforce explicit
    wall-clock deadlines (``None`` disables the guard). A deadline breach is
    terminal: the loop emits ``AgentFailed`` with the timeout category and
    never reports timed-out work as success.
    """

    model_config = ConfigDict(frozen=True)

    model: str = "fake-model"
    max_steps: int = Field(default=8, ge=1)
    system_prompt: str | None = None
    provider_timeout_seconds: float | None = Field(default=None, gt=0)
    tool_timeout_seconds: float | None = Field(default=None, gt=0)


class AgentRunResult(BaseModel):
    """Terminal outcome of one session run."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    trace_id: str
    status: str  # "finished" | "failed"
    answer: str | None = None
    error: str | None = None
    steps: int = Field(default=0, ge=0)


class AgentLoop:
    """Runs one task to completion under a hard step limit."""

    def __init__(
        self,
        recorder: EventRecorder,
        provider: ModelProvider,
        tool_runtime: ToolRuntime,
        config: AgentLoopConfig | None = None,
        prompt_builder: PromptBuilder | None = None,
        context_manager: ContextManager | None = None,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> None:
        self._recorder = recorder
        self._provider = provider
        self._tools = tool_runtime
        self._config = config or AgentLoopConfig()
        self._prompt_builder = prompt_builder
        self._context_manager = context_manager
        # An injected policy is owned by the caller; a policy derived from the
        # config is owned (and closed) by this loop.
        self._owns_timeout_policy = timeout_policy is None
        self._timeouts = timeout_policy or TimeoutPolicy(
            self._config.provider_timeout_seconds,
            self._config.tool_timeout_seconds,
        )

    def run(self, task: str) -> AgentRunResult:
        try:
            return self._run(task)
        finally:
            if self._owns_timeout_policy:
                self._timeouts.close()

    def _run(self, task: str) -> AgentRunResult:
        messages: list[ModelMessage] = []
        if self._config.system_prompt is not None:
            messages.append(ModelMessage(role="system", content=self._config.system_prompt))
        messages.append(ModelMessage(role="user", content=task))

        if self._prompt_builder is not None:
            self._build_prompt_snapshot(task, tuple(messages))

        steps = 0
        while steps < self._config.max_steps:
            steps += 1
            if self._context_manager is not None:
                # Compaction (if triggered) rewrites the transcript before the
                # request: the model call must see the compacted context.
                messages = list(self._observe_context(steps, tuple(messages)))
            request = ModelRequest(
                model=self._config.model,
                messages=tuple(messages),
                tools=self._tools.specs(),
            )
            self._recorder.emit(
                AgentEventType.LLM_CALL_STARTED,
                {"step": steps, "model": request.model, "message_count": len(messages)},
            )

            # Provider failures and deadline breaches are diagnosable, never
            # silent: both terminate the loop with a redacted AgentFailed.
            try:
                response = self._timeouts.run_provider(
                    request.model, partial(self._provider.invoke, request)
                )
            except TimeoutExceededError as exc:
                return self._fail_timeout(steps=steps, phase="provider", exc=exc)
            except Exception as exc:  # noqa: BLE001 - reported as AgentFailed below
                return self._fail(
                    steps=steps,
                    phase="provider",
                    error=redact_diagnostic(f"{type(exc).__name__}: {exc}"),
                )

            tool_calls = response.tool_calls
            self._recorder.emit(
                AgentEventType.LLM_CALL_FINISHED,
                {
                    "step": steps,
                    "finish_reason": response.finish_reason,
                    "content": response.message.content,
                    "tool_calls": [
                        {"call_id": c.call_id, "name": c.name, "arguments": dict(c.arguments)}
                        for c in tool_calls
                    ],
                    "usage": response.usage.model_dump(),
                },
            )
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.message.content,
                )
            )

            if response.finish_reason == "stop" or not tool_calls:
                self._recorder.emit(
                    AgentEventType.AGENT_FINISHED,
                    {"answer": response.message.content, "steps": steps},
                )
                return AgentRunResult(
                    session_id=self._recorder.session_id,
                    trace_id=self._recorder.trace_id,
                    status="finished",
                    answer=response.message.content,
                    steps=steps,
                )

            for call in tool_calls:
                try:
                    result = self._execute_tool_call(call, steps)
                except TimeoutExceededError as exc:
                    return self._fail_timeout(steps=steps, phase="tool", exc=exc)
                messages.append(
                    ModelMessage(
                        role="tool",
                        content=json.dumps(
                            {"ok": result.ok, "value": result.value, "error": result.error},
                            ensure_ascii=False,
                        ),
                        name=result.name,
                        tool_call_id=call.call_id,
                    )
                )

        return self._fail(
            steps=steps,
            phase="step_limit",
            error=f"step limit exceeded after {self._config.max_steps} model call(s)",
        )

    def _build_prompt_snapshot(self, task: str, messages: tuple[ModelMessage, ...]) -> None:
        assert self._prompt_builder is not None
        snapshot = self._prompt_builder.build(
            sections=PromptSections(
                base_system=self._config.system_prompt,
                current_task=task,
                recent_messages=messages,
            ),
            session_id=self._recorder.session_id,
            trace_id=self._recorder.trace_id,
        )
        self._recorder.emit(
            AgentEventType.PROMPT_BUILT,
            {
                "snapshot_id": snapshot.snapshot_id,
                "sections": [s.value for s in snapshot.sections],
                "total_tokens": snapshot.total_tokens,
                "estimator": snapshot.estimator,
            },
        )

    def _observe_context(self, step: int, messages: tuple[ModelMessage, ...]) -> tuple[ModelMessage, ...]:
        """Run the context build, emit compaction/ContextBuilt events.

        Returns the transcript the model call must use: the compacted one when
        compaction triggered, the original otherwise.
        """
        assert self._context_manager is not None
        context_build = self._context_manager.build(
            session_id=self._recorder.session_id,
            trace_id=self._recorder.trace_id,
            step=step,
            messages=messages,
        )
        if context_build.compaction is not None:
            compaction = context_build.compaction
            self._recorder.emit(
                AgentEventType.CONTEXT_COMPACTION_STARTED,
                {
                    "strategy": compaction.strategy,
                    "trigger": compaction.trigger_reason,
                    "before_tokens": compaction.before_tokens,
                    "threshold": compaction.threshold,
                },
            )
            self._recorder.emit(
                AgentEventType.CONTEXT_COMPACTION_FINISHED,
                {
                    "strategy": compaction.strategy,
                    "before_tokens": compaction.before_tokens,
                    "after_tokens": compaction.after_tokens,
                    "removed_ids": list(compaction.removed_ids),
                    "fits_budget": compaction.fits_budget,
                    "preserved_state": compaction.preserved_state,
                },
            )
        self._recorder.emit(
            AgentEventType.CONTEXT_BUILT,
            {
                "snapshot_id": context_build.snapshot.snapshot_id,
                "step": step,
                "total_tokens": context_build.snapshot.total_tokens,
                "fits": context_build.report.fits,
                "reason": context_build.report.reason,
            },
        )
        if context_build.compaction is not None:
            return context_build.compaction.messages
        return messages

    def _execute_tool_call(self, call: Any, step: int) -> ToolResult:
        arguments = dict(call.arguments)
        self._recorder.emit(
            AgentEventType.TOOL_CALL_STARTED,
            {"step": step, "call_id": call.call_id, "name": call.name, "arguments": arguments},
        )
        context = ToolContext(
            session_id=self._recorder.session_id,
            trace_id=self._recorder.trace_id,
            step_index=step,
        )
        try:
            result = self._timeouts.run_tool(
                call.name,
                partial(self._tools.execute, call.name, arguments, context),
            )
        except TimeoutExceededError as exc:
            # Close the tool boundary explicitly: the call was abandoned, so
            # its recorded outcome is a failed ToolResult, never a success.
            self._recorder.emit(
                AgentEventType.TOOL_CALL_FINISHED,
                {
                    "step": step,
                    "call_id": call.call_id,
                    "name": call.name,
                    "ok": False,
                    "value": None,
                    "error": redact_diagnostic(str(exc)),
                    "timeout": {
                        "kind": exc.kind,
                        "target": exc.target,
                        "timeout_seconds": exc.timeout_seconds,
                    },
                },
            )
            raise
        self._recorder.emit(
            AgentEventType.TOOL_CALL_FINISHED,
            {
                "step": step,
                "call_id": call.call_id,
                "name": result.name,
                "ok": result.ok,
                "value": result.value,
                "error": result.error,
            },
        )
        return result

    def _fail(self, steps: int, phase: str, error: str) -> AgentRunResult:
        self._recorder.emit(
            AgentEventType.AGENT_FAILED,
            {"phase": phase, "error": error, "steps": steps},
        )
        return AgentRunResult(
            session_id=self._recorder.session_id,
            trace_id=self._recorder.trace_id,
            status="failed",
            error=error,
            steps=steps,
        )

    def _fail_timeout(self, steps: int, phase: str, exc: TimeoutExceededError) -> AgentRunResult:
        error = redact_diagnostic(str(exc))
        self._recorder.emit(
            AgentEventType.AGENT_FAILED,
            {
                "phase": phase,
                "error": error,
                "steps": steps,
                "timeout": {
                    "kind": exc.kind,
                    "target": exc.target,
                    "timeout_seconds": exc.timeout_seconds,
                },
            },
        )
        return AgentRunResult(
            session_id=self._recorder.session_id,
            trace_id=self._recorder.trace_id,
            status="failed",
            error=error,
            steps=steps,
        )

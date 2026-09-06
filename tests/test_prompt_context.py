"""Prompt/Context snapshots: section order, breakdown invariants, runtime wiring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.estimator import DeterministicEstimator
from packages.context.manager import ContextManager
from packages.context.prompt import PromptBuilder, PromptSections
from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.snapshots import PROMPT_SECTION_ORDER, PromptSection
from packages.core.tools import ToolContext, ToolResult
from packages.observability.inmemory import InMemorySnapshotStore
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession
from packages.runtime.tools import ToolRuntime

SECTION_TEXTS = {
    PromptSection.BASE_SYSTEM: "You are AgentFlow.",
    PromptSection.AGENT_ROLE: "You debug agent behavior.",
    PromptSection.PROJECT_CONTEXT: "The repo is an offline MVP.",
    PromptSection.RELEVANT_MEMORY: "The user prefers concise answers.",
    PromptSection.RELEVANT_SKILLS: "token accounting, replay",
    PromptSection.CURRENT_TASK: "Summarize the report.",
    PromptSection.RUNTIME_CONTEXT: "step=1 max_steps=8",
    PromptSection.RECENT_MESSAGES: "user: hi",
    PromptSection.TOOL_RESULTS: "word_count -> 18",
}


def all_sections() -> PromptSections:
    return PromptSections(
        base_system=SECTION_TEXTS[PromptSection.BASE_SYSTEM],
        agent_role=SECTION_TEXTS[PromptSection.AGENT_ROLE],
        project_context=SECTION_TEXTS[PromptSection.PROJECT_CONTEXT],
        relevant_memory=SECTION_TEXTS[PromptSection.RELEVANT_MEMORY],
        relevant_skills=SECTION_TEXTS[PromptSection.RELEVANT_SKILLS],
        current_task=SECTION_TEXTS[PromptSection.CURRENT_TASK],
        runtime_context=SECTION_TEXTS[PromptSection.RUNTIME_CONTEXT],
        recent_messages=(ModelMessage(role="user", content="hi"),),
        tool_results=SECTION_TEXTS[PromptSection.TOOL_RESULTS],
    )


def test_prompt_builder_preserves_the_frozen_section_order() -> None:
    store = InMemorySnapshotStore()
    builder = PromptBuilder(DeterministicEstimator(), store)
    snapshot = builder.build(all_sections(), session_id="s-1", trace_id="tr-1")
    assert snapshot.sections == PROMPT_SECTION_ORDER
    assert tuple(snapshot.section_content.keys()) == PROMPT_SECTION_ORDER


def test_prompt_builder_skips_empty_sections_and_keeps_relative_order() -> None:
    builder = PromptBuilder(DeterministicEstimator())
    snapshot = builder.build(
        PromptSections(current_task="Summarize.", tool_results="count -> 18"),
        session_id="s-1",
        trace_id="tr-1",
    )
    assert snapshot.sections == (PromptSection.CURRENT_TASK, PromptSection.TOOL_RESULTS)


def test_prompt_snapshot_token_counts_sum_to_total_with_estimator_name() -> None:
    builder = PromptBuilder(DeterministicEstimator())
    snapshot = builder.build(all_sections(), session_id="s-1", trace_id="tr-1")
    assert sum(snapshot.token_counts.values()) == snapshot.total_tokens
    assert snapshot.estimator == "deterministic-v1"
    assert snapshot.token_counts["current_task"] == DeterministicEstimator().count(
        SECTION_TEXTS[PromptSection.CURRENT_TASK]
    )


def test_prompt_builder_assigns_ordered_ids_and_sequences() -> None:
    builder = PromptBuilder(DeterministicEstimator())
    first = builder.build(all_sections(), session_id="s-9", trace_id="tr-1")
    second = builder.build(all_sections(), session_id="s-9", trace_id="tr-1")
    assert (first.sequence, second.sequence) == (0, 1)
    assert first.snapshot_id == "s-9-prompt-0000"
    assert second.snapshot_id == "s-9-prompt-0001"
    assert first.snapshot_id != second.snapshot_id


def test_context_manager_breakdown_sums_to_total_and_records_budget() -> None:
    manager = ContextManager(
        DeterministicEstimator(),
        TokenBudgetManager(BudgetConfig(max_context_tokens=4096, reserved_output_tokens=256)),
    )
    messages = (
        ModelMessage(role="system", content="You are AgentFlow."),
        ModelMessage(role="user", content="Summarize the report."),
        ModelMessage(role="tool", content="word_count -> 18"),
    )
    build = manager.build(session_id="s-1", trace_id="tr-1", step=1, messages=messages)

    assert sum(build.snapshot.component_tokens.values()) == build.snapshot.total_tokens
    assert set(build.snapshot.component_tokens) == {"system", "user", "tool"}
    assert build.snapshot.total_tokens == build.report.total_tokens
    assert build.snapshot.reserved_output_tokens == 256
    assert build.snapshot.budget_limit == 4096 - 256
    assert build.snapshot.compaction_state == "none"
    assert build.snapshot.estimator == "deterministic-v1"
    assert build.snapshot.metadata == {"step": 1}
    assert build.snapshot.messages == messages


def test_context_manager_persists_snapshots_in_sequence_order() -> None:
    store = InMemorySnapshotStore()
    manager = ContextManager(DeterministicEstimator(), snapshot_store=store)
    for step in (1, 2, 3):
        manager.build(
            session_id="s-1",
            trace_id="tr-1",
            step=step,
            messages=(ModelMessage(role="user", content=f"step {step}"),),
        )
    snapshots = store.get_context_snapshots("s-1")
    assert [s.sequence for s in snapshots] == [0, 1, 2]
    assert [s.snapshot_id for s in snapshots] == [
        "s-1-ctx-0000",
        "s-1-ctx-0001",
        "s-1-ctx-0002",
    ]
    assert [s.metadata["step"] for s in snapshots] == [1, 2, 3]


class EchoTool:
    name = "echo"
    description = "Echo the provided text."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value=str(arguments.get("text", "")))


def make_wired_session(script: list) -> tuple[AgentSession, InMemorySnapshotStore]:
    store = InMemorySnapshotStore()
    estimator = DeterministicEstimator()
    session = AgentSession(
        task="Summarize the report.",
        provider=FakeModelProvider(script),
        tools=[EchoTool()],
        config=None,
        prompt_builder=PromptBuilder(estimator, store),
        context_manager=ContextManager(
            estimator, TokenBudgetManager(BudgetConfig()), store
        ),
        session_id="wired-session",
    )
    return session, store


def test_wired_session_emits_prompt_and_context_events_in_exact_order() -> None:
    session, _ = make_wired_session([ModelResponse(
        message=ModelMessage(role="assistant", content="done"), finish_reason="stop"
    )])
    session.run()
    assert [e.event_type for e in session.events()] == [
        AgentEventType.SESSION_STARTED,
        AgentEventType.PROMPT_BUILT,
        AgentEventType.CONTEXT_BUILT,
        AgentEventType.LLM_CALL_STARTED,
        AgentEventType.LLM_CALL_FINISHED,
        AgentEventType.AGENT_FINISHED,
    ]
    context_built = session.events()[2]
    assert context_built.payload["fits"] is True
    assert context_built.payload["reason"].startswith("within_budget")
    assert context_built.payload["total_tokens"] > 0


def test_wired_session_persists_prompt_and_context_snapshots() -> None:
    session, store = make_wired_session(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(
                    ToolCallRequest(call_id="c1", name="echo", arguments={"text": "hi"}),
                ),
            ),
            ModelResponse(
                message=ModelMessage(role="assistant", content="done"),
                finish_reason="stop",
            ),
        ]
    )
    session.run()
    prompts = store.get_prompt_snapshots("wired-session")
    contexts = store.get_context_snapshots("wired-session")
    assert len(prompts) == 1
    assert len(contexts) == 2  # one per model call
    assert [s.metadata["step"] for s in contexts] == [1, 2]
    # Context grows once the assistant reply is appended to the transcript.
    assert contexts[1].total_tokens >= contexts[0].total_tokens
    assert prompts[0].section_content[PromptSection.CURRENT_TASK] == "Summarize the report."


def test_unwired_session_keeps_phase_1_event_shape() -> None:
    session = AgentSession(
        task="t",
        provider=FakeModelProvider(
            [ModelResponse(message=ModelMessage(role="assistant", content="done"),
                           finish_reason="stop")]
        ),
        tools=[],
    )
    session.run()
    types = [e.event_type for e in session.events()]
    assert AgentEventType.PROMPT_BUILT not in types
    assert AgentEventType.CONTEXT_BUILT not in types


def test_wired_session_needs_no_tools_for_specs() -> None:
    # ToolRuntime with no tools must produce an empty spec tuple, not an error.
    assert ToolRuntime().specs() == ()

"""Compaction: threshold trigger, both strategies, determinism, integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.context.budget import BudgetConfig, TokenBudgetManager
from packages.context.compaction import (
    CompactionConfig,
    CompactionEngine,
    preview,
)
from packages.context.estimator import DeterministicEstimator
from packages.context.fixture import canonical_messages
from packages.context.manager import ContextManager
from packages.core.events import AgentEventType
from packages.core.provider import ModelMessage, ModelResponse, ToolCallRequest
from packages.core.tools import ToolContext, ToolResult
from packages.observability.inmemory import InMemorySnapshotStore
from packages.runtime.loop import AgentLoopConfig
from packages.runtime.provider import FakeModelProvider
from packages.runtime.session import AgentSession

ESTIMATOR = DeterministicEstimator()


def make_engine(
    strategy: str,
    max_tokens: int = 2000,
    reserved: int = 256,
    threshold_ratio: float = 0.8,
    keep_last: int = 4,
) -> CompactionEngine:
    return CompactionEngine(
        ESTIMATOR,
        TokenBudgetManager(
            BudgetConfig(max_context_tokens=max_tokens, reserved_output_tokens=reserved)
        ),
        CompactionConfig(
            strategy=strategy,  # type: ignore[arg-type]
            threshold_ratio=threshold_ratio,
            keep_last_messages=keep_last,
        ),
    )


def count_tokens(messages: tuple[ModelMessage, ...]) -> int:
    return sum(ESTIMATOR.count(f"{m.role}: {m.content}") for m in messages)


def test_threshold_decision_within_and_exceeded() -> None:
    engine = make_engine("keep_recent_summary", max_tokens=1000, reserved=100)
    assert engine.threshold_tokens() == 720

    within = engine.should_compact(
        TokenBudgetManager(
            BudgetConfig(max_context_tokens=1000, reserved_output_tokens=100)
        ).evaluate({"system": 100, "user": 100})
    )
    assert within.should is False
    assert within.reason == "within_threshold total=200 threshold=720"

    over = engine.should_compact(
        TokenBudgetManager(
            BudgetConfig(max_context_tokens=1000, reserved_output_tokens=100)
        ).evaluate({"user": 800})
    )
    assert over.should is True
    assert over.reason == (
        "threshold_exceeded total=800 threshold=720 limit=900 strategy=keep_recent_summary"
    )


def test_keep_recent_summary_keeps_system_plus_tail_and_reports_removed_ids() -> None:
    engine = make_engine("keep_recent_summary", keep_last=3)
    messages = canonical_messages()
    result = engine.compact(messages, "threshold_exceeded total=999 threshold=700 limit=700")

    assert result.strategy == "keep_recent_summary"
    assert result.trigger_reason == "threshold_exceeded total=999 threshold=700 limit=700"
    # 10 non-system messages, keep the last 3 -> 7 removed, replaced by 1 summary.
    assert result.removed_ids == tuple(f"msg-{i:04d}" for i in range(1, 8))
    assert result.kept_ids == ("msg-0000", "msg-0008", "msg-0009", "msg-0010")
    assert result.summary_message is not None
    assert result.summary_message.name == "compaction_summary"
    assert result.messages[0] == messages[0]  # original system message stays first
    assert result.messages[1] == result.summary_message
    assert result.messages[2:] == messages[8:]
    assert result.before_tokens == count_tokens(messages)
    assert result.after_tokens == count_tokens(result.messages)
    assert result.after_tokens < result.before_tokens
    assert result.fits_budget is True


def test_keep_recent_summary_template_is_exact() -> None:
    engine = make_engine("keep_recent_summary", keep_last=3)
    summary = engine.compact(canonical_messages(), "trigger").summary_message
    assert summary is not None
    finding_b = (
        "risk scan: concentration=0.38 support_gap=14 oncall=2 "
        "regression=5 security_patch=2 vendor_lock=medium hiring_gap=3 "
        "roadmap_slip=2w budget_var=-4%"
    )
    assert summary.content == (
        "[Context summary] 7 earlier messages removed "
        "(assistant=3, tool=2, user=2); "
        "earliest task: Summarize the Q3 report and list risks.; "
        f"latest tool result: {preview(finding_b)}."
    )
    assert preview(finding_b).endswith("…")  # documented 80-char preview rule


def test_semantic_state_preserves_all_six_fields() -> None:
    engine = make_engine("semantic_state")
    result = engine.compact(canonical_messages(), "trigger")

    state = result.preserved_state
    assert set(state) == {
        "goal",
        "current_state",
        "decisions",
        "artifacts",
        "tool_findings",
        "pending_tasks",
    }
    assert state["goal"] == "Summarize the Q3 report and list risks."
    assert state["current_state"] == "Drafting the mitigation plan."
    assert state["decisions"] == [
        "I will pull the report first.",
        "Revenue is 1200 with churn at 3.2%.",
        "Top risks: concentration and support gap.",
    ]
    # artifacts store each distinct value once; findings reference them.
    assert len(state["artifacts"]) == 2
    assert state["tool_findings"] == [
        "fetch_report=artifact#0",
        "scan_risks=artifact#1",
        "fetch_report=artifact#0",
    ]
    assert "revenue=1200" in state["artifacts"][0]
    assert "concentration=0.38" in state["artifacts"][1]
    assert state["pending_tasks"] == ["Now scan for delivery risks.", "Draft the mitigation plan next."]


def test_semantic_state_replaces_all_non_system_messages() -> None:
    engine = make_engine("semantic_state")
    messages = canonical_messages()
    result = engine.compact(messages, "trigger")

    assert result.summary_message is not None
    assert result.summary_message.name == "semantic_state"
    assert "[Conversation state]" in result.summary_message.content
    assert "goal: Summarize the Q3 report and list risks." in result.summary_message.content
    assert result.messages == (messages[0], result.summary_message)
    assert result.removed_ids == tuple(f"msg-{i:04d}" for i in range(1, 11))
    assert result.after_tokens < result.before_tokens
    assert result.fits_budget is True


def test_both_strategies_are_deterministic_on_the_canonical_fixture() -> None:
    for strategy in ("keep_recent_summary", "semantic_state"):
        engine = make_engine(strategy)  # type: ignore[arg-type]
        first = engine.compact(canonical_messages(), "reason")
        second = engine.compact(canonical_messages(), "reason")
        assert first == second
    keep = make_engine("keep_recent_summary").compact(canonical_messages(), "reason")
    semantic = make_engine("semantic_state").compact(canonical_messages(), "reason")
    assert keep.summary_message != semantic.summary_message
    assert len(semantic.removed_ids) > len(keep.removed_ids)


def test_nothing_to_remove_leaves_transcript_intact_and_honest() -> None:
    engine = make_engine("keep_recent_summary", max_tokens=250, reserved=50, keep_last=10)
    messages = canonical_messages()
    result = engine.compact(messages, "trigger")
    assert result.removed_ids == ()
    assert result.summary_message is None
    assert result.messages == messages
    assert result.after_tokens == result.before_tokens
    # before (228 tokens) exceeds the limit (200): the verdict stays honest.
    assert result.fits_budget is False


class LongTool:
    name = "long_tool"
    description = "Returns a long observation."

    def run(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(name=self.name, ok=True, value="x" * 1500)


def make_compacting_session(strategy: str) -> tuple[AgentSession, InMemorySnapshotStore, FakeModelProvider]:
    store = InMemorySnapshotStore()
    # limit 450, threshold 360; a 1500-char tool result (~385 tokens) crosses it.
    engine = make_engine(strategy, max_tokens=500, reserved=50, keep_last=1)
    manager = ContextManager(
        ESTIMATOR,
        TokenBudgetManager(BudgetConfig(max_context_tokens=500, reserved_output_tokens=50)),
        store,
        compaction_engine=engine,
    )
    provider = FakeModelProvider(
        [
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(ToolCallRequest(call_id="c1", name="long_tool", arguments={}),),
            ),
            ModelResponse(
                message=ModelMessage(role="assistant", content=""),
                finish_reason="tool_calls",
                tool_calls=(ToolCallRequest(call_id="c2", name="long_tool", arguments={}),),
            ),
            ModelResponse(
                message=ModelMessage(role="assistant", content="digested"),
                finish_reason="stop",
            ),
        ]
    )
    session = AgentSession(
        task="digest report",
        provider=provider,
        tools=[LongTool()],
        config=AgentLoopConfig(max_steps=4, system_prompt="You are AgentFlow."),
        context_manager=manager,
        session_id=f"compacted-{strategy}",
    )
    return session, store, provider


def test_live_session_triggers_compaction_events_in_order() -> None:
    session, _, _ = make_compacting_session("keep_recent_summary")
    session.run()

    types = [e.event_type for e in session.events()]
    assert types.count(AgentEventType.CONTEXT_COMPACTION_STARTED) == 2
    assert types.count(AgentEventType.CONTEXT_COMPACTION_FINISHED) == 2
    # Every compaction is sandwiched right before its ContextBuilt + LLM call.
    for index, event in enumerate(session.events()):
        if event.event_type == AgentEventType.CONTEXT_COMPACTION_STARTED:
            assert types[index + 1] == AgentEventType.CONTEXT_COMPACTION_FINISHED
            assert types[index + 2] == AgentEventType.CONTEXT_BUILT
            assert types[index + 3] == AgentEventType.LLM_CALL_STARTED

    started = session.events()[6]  # no PromptBuilder wired: no PromptBuilt event
    assert started.event_type == AgentEventType.CONTEXT_COMPACTION_STARTED
    assert started.payload["strategy"] == "keep_recent_summary"
    assert started.payload["trigger"].startswith("threshold_exceeded total=")

    first_finished = session.events()[7].payload
    assert first_finished["removed_ids"] == ["msg-0001", "msg-0002"]  # user + assistant
    assert first_finished["fits_budget"] is True
    assert first_finished["preserved_state"]["removed_role_counts"] == {
        "user": 1,
        "assistant": 1,
    }


def test_compacted_transcript_is_adopted_by_the_next_model_call() -> None:
    session, store, provider = make_compacting_session("keep_recent_summary")
    session.run()

    # Step-2 request: the compacted transcript (user + assistant summarized;
    # the recent tool observation is kept by design of keep_recent_summary).
    request2 = provider.calls[1]
    assert [m.role for m in request2.messages] == ["system", "system", "tool"]
    assert request2.messages[1].name == "compaction_summary"
    assert not any(m.content == "digest report" for m in request2.messages)

    # Snapshot documents exactly what the request carried.
    snapshots = store.get_context_snapshots("compacted-keep_recent_summary")
    assert snapshots[0].compaction_state == "none"
    assert snapshots[1].compaction_state == "keep_recent_summary"
    assert snapshots[1].messages == request2.messages

    # After two compactions the old summary was superseded, not accumulated.
    final_request = provider.calls[2]
    summary_names = [
        m.name for m in final_request.messages if m.name == "compaction_summary"
    ]
    assert summary_names == ["compaction_summary"]


def test_semantic_state_compaction_in_live_session() -> None:
    session, store, provider = make_compacting_session("semantic_state")
    session.run()

    compactions = [
        e
        for e in session.events()
        if e.event_type == AgentEventType.CONTEXT_COMPACTION_FINISHED
    ]
    assert len(compactions) == 2
    first = compactions[0].payload
    assert first["strategy"] == "semantic_state"
    assert first["removed_ids"] == ["msg-0001", "msg-0002", "msg-0003"]
    assert set(first["preserved_state"]) == {
        "goal",
        "current_state",
        "decisions",
        "artifacts",
        "tool_findings",
        "pending_tasks",
    }
    assert first["preserved_state"]["goal"] == "digest report"
    # One compaction cannot shrink a context dominated by a single large
    # artifact, but it must fit the budget — the guarantee we commit to.
    assert first["fits_budget"] is True

    request2 = provider.calls[1]
    assert [m.role for m in request2.messages] == ["system", "system"]
    assert request2.messages[1].name == "semantic_state"
    snapshots = store.get_context_snapshots("compacted-semantic_state")
    assert snapshots[1].compaction_state == "semantic_state"
    assert snapshots[1].messages == request2.messages

    # The second compaction deduplicates the repeated artifact and shrinks.
    second = compactions[1].payload
    assert second["after_tokens"] < second["before_tokens"]
    assert second["fits_budget"] is True

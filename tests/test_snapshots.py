"""Snapshot contracts: fixed section order, estimator metadata, immutability."""

from __future__ import annotations

from packages.core.provider import ModelMessage
from packages.core.snapshots import (
    PROMPT_SECTION_ORDER,
    ContextSnapshot,
    PromptSection,
    PromptSnapshot,
)
from tests.helpers import FIXED_TIME

EXPECTED_ORDER = (
    "base_system",
    "agent_role",
    "project_context",
    "relevant_memory",
    "relevant_skills",
    "current_task",
    "runtime_context",
    "recent_messages",
    "tool_results",
)


def test_prompt_section_order_is_frozen_by_contract() -> None:
    assert tuple(s.value for s in PROMPT_SECTION_ORDER) == EXPECTED_ORDER


def make_prompt_snapshot() -> PromptSnapshot:
    return PromptSnapshot(
        snapshot_id="ps-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=1,
        created_at=FIXED_TIME,
        sections=(PromptSection.BASE_SYSTEM, PromptSection.CURRENT_TASK),
        section_content={
            PromptSection.BASE_SYSTEM: "You are AgentFlow.",
            PromptSection.CURRENT_TASK: "Summarize a file.",
        },
        estimator="deterministic-fallback",
        token_counts={"base_system": 4, "current_task": 4},
        total_tokens=8,
    )


def make_context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        snapshot_id="cs-1",
        session_id="s-1",
        trace_id="tr-1",
        sequence=2,
        created_at=FIXED_TIME,
        messages=(ModelMessage(role="user", content="Summarize a file."),),
        component_tokens={"recent_messages": 6},
        total_tokens=6,
        reserved_output_tokens=256,
        budget_limit=4096,
        estimator="deterministic-fallback",
    )


def test_prompt_snapshot_records_estimator() -> None:
    snapshot = make_prompt_snapshot()
    assert snapshot.estimator == "deterministic-fallback"
    assert snapshot.total_tokens == sum(snapshot.token_counts.values())


def test_context_snapshot_records_budget_fields() -> None:
    snapshot = make_context_snapshot()
    assert snapshot.reserved_output_tokens == 256
    assert snapshot.budget_limit == 4096
    assert snapshot.compaction_state == "none"
    assert snapshot.messages[0].content == "Summarize a file."


def test_snapshots_are_json_serializable() -> None:
    prompt = make_prompt_snapshot()
    context = make_context_snapshot()
    prompt_dumped = prompt.model_dump(mode="json")
    context_dumped = context.model_dump(mode="json")
    assert prompt_dumped["sections"] == ["base_system", "current_task"]
    assert PromptSnapshot.model_validate(prompt_dumped) == prompt
    assert ContextSnapshot.model_validate(context_dumped) == context

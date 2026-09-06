"""Event contract: all lifecycle types, serialization, schema version."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from packages.core.events import SCHEMA_VERSION, AgentEvent, AgentEventType
from tests.helpers import FIXED_TIME, make_event

EXPECTED_EVENT_TYPES = {
    "SessionStarted",
    "PromptBuilt",
    "ContextBuilt",
    "LLMCallStarted",
    "LLMCallFinished",
    "ToolCallStarted",
    "ToolCallFinished",
    "ContextCompactionStarted",
    "ContextCompactionFinished",
    "AgentFinished",
    "AgentFailed",
}


def test_all_required_lifecycle_event_types_exist() -> None:
    assert {t.value for t in AgentEventType} == EXPECTED_EVENT_TYPES


def test_event_has_all_contract_fields() -> None:
    event = make_event(payload={"step": 1})
    assert event.event_id == "ev-0000"
    assert event.session_id == "s-1"
    assert event.trace_id == "tr-1"
    assert event.sequence == 0
    assert event.timestamp == FIXED_TIME
    assert event.event_type == AgentEventType.SESSION_STARTED
    assert event.schema_version == SCHEMA_VERSION == "1.0"
    assert event.payload == {"step": 1}


def test_event_is_json_serializable_and_roundtrips() -> None:
    event = make_event(payload={"prompt_tokens": 12})
    dumped = json.loads(event.model_dump_json())
    assert dumped["event_type"] == "SessionStarted"
    assert dumped["timestamp"].startswith("2026-01-01T12:00:00")
    restored = AgentEvent.model_validate(dumped)
    assert restored == event


def test_event_is_frozen() -> None:
    event = make_event()
    with pytest.raises(ValidationError):
        event.sequence = 99  # type: ignore[misc]


def test_sequence_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        make_event(sequence=-1)

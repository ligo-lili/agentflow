"""FastAPI surface: typed DTOs, error envelopes, offline runs, no execution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.schemas import MAX_TASK_LENGTH
from packages.core.errors import StoreError
from packages.core.events import AgentEvent, AgentEventType


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(tmp_path / "agentflow.db")
    with TestClient(app) as test_client:
        yield test_client, app  # type: ignore[misc]


@pytest.fixture()
def api(client):  # type: ignore[no-untyped-def]
    test_client, _app = client
    return test_client


def run_simple(api: TestClient) -> dict:
    response = api.post("/api/runs", json={"scenario": "simple"})
    assert response.status_code == 200
    return response.json()


def run_compaction(api: TestClient) -> dict:
    response = api.post("/api/runs", json={"scenario": "compaction"})
    assert response.status_code == 200
    return response.json()


def test_post_run_creates_a_persisted_offline_session(api: TestClient) -> None:
    body = run_simple(api)
    assert body["status"] == "finished"
    assert body["scenario"] == "simple"
    assert body["steps"] == 2
    assert body["event_count"] >= 6
    assert "The report contains" in str(body["answer"])


def test_post_run_compaction_scenario_records_compaction(api: TestClient) -> None:
    body = run_compaction(api)
    session_id = str(body["session_id"])
    replay = api.get(f"/api/sessions/{session_id}/replay").json()
    assert replay["status"] == "finished"
    assert len(replay["compactions"]) == 1
    assert replay["compactions"][0]["strategy"] == "semantic_state"


def test_sessions_list_contains_created_sessions(api: TestClient) -> None:
    created = run_simple(api)
    sessions = api.get("/api/sessions").json()
    match = [s for s in sessions if s["session_id"] == created["session_id"]]
    assert len(match) == 1
    assert match[0]["status"] == "finished"
    assert "Count the words" in match[0]["task"]
    assert match[0]["event_count"] == created["event_count"]


def test_session_detail_returns_metadata_and_counts(api: TestClient) -> None:
    created = run_simple(api)
    session_id = str(created["session_id"])
    detail = api.get(f"/api/sessions/{session_id}").json()
    assert detail["session_id"] == session_id
    assert detail["model"] == "fake-model"
    assert detail["event_type_counts"]["AgentFinished"] == 1
    assert detail["event_type_counts"]["ToolCallFinished"] == 1


def test_events_endpoint_returns_the_ordered_timeline(api: TestClient) -> None:
    created = run_simple(api)
    data = api.get(f"/api/sessions/{created['session_id']}/events").json()
    sequences = [e["sequence"] for e in data["entries"]]
    assert sequences == sorted(sequences) == list(range(len(sequences)))
    assert data["entries"][0]["event_type"] == "SessionStarted"
    assert data["event_counts"]["AgentFinished"] == 1


def test_prompt_snapshots_endpoint_returns_section_order(api: TestClient) -> None:
    created = run_simple(api)
    snapshots = api.get(f"/api/sessions/{created['session_id']}/prompt-snapshots").json()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["sections"] == ["base_system", "current_task", "recent_messages"]
    assert sum(snapshot["token_counts"].values()) == snapshot["total_tokens"]
    assert snapshot["estimator"] == "deterministic-v1"


def test_context_snapshots_endpoint_breakdown_sums_to_total(api: TestClient) -> None:
    created = run_simple(api)
    snapshots = api.get(f"/api/sessions/{created['session_id']}/context-snapshots").json()
    assert len(snapshots) == 2  # one per model call
    for snapshot in snapshots:
        assert sum(snapshot["component_tokens"].values()) == snapshot["total_tokens"]
        assert snapshot["reserved_output_tokens"] == 50
    assert snapshots[1]["total_tokens"] >= snapshots[0]["total_tokens"]  # growth


def test_replay_endpoint_rebuilds_without_execution(api: TestClient) -> None:
    created = run_simple(api)
    session_id = str(created["session_id"])
    first = api.get(f"/api/sessions/{session_id}/replay")
    second = api.get(f"/api/sessions/{session_id}/replay")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # pure function of the log
    replay = first.json()
    assert replay["status"] == "finished"
    assert replay["integrity"]["valid"] is True
    assert replay["final_answer"] == created["answer"]
    assert replay["steps"][0]["tool_calls"][0]["name"] == "word_count"
    assert replay["steps"][0]["tool_calls"][0]["value"] == 18


def test_evaluation_endpoint_returns_real_report(api: TestClient) -> None:
    created = run_simple(api)
    response = api.get(f"/api/sessions/{created['session_id']}/evaluation")
    assert response.status_code == 200
    report = response.json()
    assert report["session_id"] == created["session_id"]
    assert report["task_completed"] == 1
    assert 0.0 <= report["score"] <= 1.0
    # The score must be stable across calls (pure function of the log).
    again = api.get(f"/api/sessions/{created['session_id']}/evaluation")
    assert again.json() == report


def test_unknown_session_maps_to_404_envelope_on_every_read_endpoint(
    api: TestClient,
) -> None:
    missing = "no-such-session"
    for url in (
        f"/api/sessions/{missing}",
        f"/api/sessions/{missing}/events",
        f"/api/sessions/{missing}/prompt-snapshots",
        f"/api/sessions/{missing}/context-snapshots",
        f"/api/sessions/{missing}/replay",
        f"/api/sessions/{missing}/evaluation",
    ):
        response = api.get(url)
        assert response.status_code == 404, url
        body = response.json()
        assert body["error"]["code"] == "SESSION_NOT_FOUND", url
        assert "message" in body["error"]
        assert "detail" not in body  # the old raw FastAPI shape is gone


def test_invalid_replay_data_maps_to_409_with_integrity_details(
    client,  # type: ignore[no-untyped-def]
) -> None:
    test_client, app = client
    created = run_simple(test_client)
    session_id = str(created["session_id"])
    # Inject a truncated log shape: a foreign event far beyond the range.
    forged = AgentEvent(
        event_id="forged",
        session_id=session_id,
        trace_id="tr-forged",
        sequence=99,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        event_type=AgentEventType.LLM_CALL_STARTED,
        payload={"step": 1},
    )
    app.state.event_store.append(forged)

    response = test_client.get(f"/api/sessions/{session_id}/replay")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "REPLAY_INVALID"
    codes = [issue["code"] for issue in body["error"]["details"]["integrity"]["errors"]]
    assert "SEQUENCE_GAP" in codes
    # The evaluation endpoint rejects the same corruption the same way.
    assert test_client.get(f"/api/sessions/{session_id}/evaluation").status_code == 409


def test_store_error_is_logged_redacted_and_never_leaked(
    client, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    test_client, app = client
    secret = r"cannot load rows for 'x': db locked at E:\agentflow\secret\path.db"

    def boom() -> list[str]:
        raise StoreError(secret)

    monkeypatch.setattr(app.state.event_store, "session_ids", boom)
    response = test_client.get("/api/sessions")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "STORE_UNAVAILABLE"
    assert body["error"]["message"] == "storage backend temporarily unavailable"
    assert "secret" not in response.text
    assert "db locked" not in response.text


def test_invalid_scenario_rejected_with_422_envelope(api: TestClient) -> None:
    response = api.post("/api/runs", json={"scenario": "chaos"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"]


def test_oversized_task_rejected_with_422(api: TestClient) -> None:
    response = api.post("/api/runs", json={"scenario": "simple", "task": "x" * (MAX_TASK_LENGTH + 1)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_task_at_the_limit_is_accepted(api: TestClient) -> None:
    response = api.post("/api/runs", json={"scenario": "simple", "task": "y" * MAX_TASK_LENGTH})
    assert response.status_code == 200
    assert response.json()["status"] == "finished"


def test_openapi_declares_success_and_error_schemas(api: TestClient) -> None:
    schema = api.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    for name in (
        "RunRequest",
        "RunResponse",
        "SessionSummary",
        "SessionDetail",
        "ErrorResponse",
        "SessionTimeline",
        "PromptSnapshot",
        "ContextSnapshot",
        "SessionReplay",
        "EvaluationReport",
    ):
        assert name in components, name
    run_route = schema["paths"]["/api/runs"]["post"]
    assert "RunResponse" in run_route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "422" in run_route["responses"]
    replay_route = schema["paths"]["/api/sessions/{session_id}/replay"]["get"]
    assert "409" in replay_route["responses"]


def test_inspector_page_renders_all_required_panels(api: TestClient) -> None:
    response = api.get("/")
    assert response.status_code == 200
    html = response.text
    for expected in (
        "Session Timeline",
        "Prompt Sections",
        "Context Breakdown",
        "Tool Calls",
        "Compaction (before / after)",
        "Replay (from the event log",
        "Evaluation Summary",
        "Create offline run",
    ):
        assert expected in html


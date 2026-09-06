"""FastAPI surface: endpoints, error mapping, offline runs, no-replay-execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "agentflow.db")
    with TestClient(app) as test_client:
        yield test_client  # type: ignore[misc]


def run_simple(client: TestClient) -> dict:
    response = client.post("/api/runs", json={"scenario": "simple"})
    assert response.status_code == 200
    return response.json()


def run_compaction(client: TestClient) -> dict:
    response = client.post("/api/runs", json={"scenario": "compaction"})
    assert response.status_code == 200
    return response.json()


def test_post_run_creates_a_persisted_offline_session(client: TestClient) -> None:
    body = run_simple(client)
    assert body["status"] == "finished"
    assert body["scenario"] == "simple"
    assert body["steps"] == 2
    assert body["event_count"] >= 6
    assert "The report contains" in str(body["answer"])


def test_post_run_compaction_scenario_records_compaction(client: TestClient) -> None:
    body = run_compaction(client)
    session_id = str(body["session_id"])
    replay = client.get(f"/api/sessions/{session_id}/replay").json()
    assert replay["status"] == "finished"
    assert len(replay["compactions"]) == 1
    assert replay["compactions"][0]["strategy"] == "semantic_state"


def test_sessions_list_contains_created_sessions(client: TestClient) -> None:
    created = run_simple(client)
    sessions = client.get("/api/sessions").json()
    match = [s for s in sessions if s["session_id"] == created["session_id"]]
    assert len(match) == 1
    assert match[0]["status"] == "finished"
    assert "Count the words" in match[0]["task"]
    assert match[0]["event_count"] == created["event_count"]


def test_session_detail_returns_metadata_and_counts(client: TestClient) -> None:
    created = run_simple(client)
    session_id = str(created["session_id"])
    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["session_id"] == session_id
    assert detail["model"] == "fake-model"
    assert detail["event_type_counts"]["AgentFinished"] == 1
    assert detail["event_type_counts"]["ToolCallFinished"] == 1


def test_events_endpoint_returns_the_ordered_timeline(client: TestClient) -> None:
    created = run_simple(client)
    data = client.get(f"/api/sessions/{created['session_id']}/events").json()
    sequences = [e["sequence"] for e in data["entries"]]
    assert sequences == sorted(sequences) == list(range(len(sequences)))
    assert data["entries"][0]["event_type"] == "SessionStarted"
    assert data["event_counts"]["AgentFinished"] == 1


def test_prompt_snapshots_endpoint_returns_section_order(client: TestClient) -> None:
    created = run_simple(client)
    snapshots = client.get(f"/api/sessions/{created['session_id']}/prompt-snapshots").json()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["sections"] == ["base_system", "current_task", "recent_messages"]
    assert sum(snapshot["token_counts"].values()) == snapshot["total_tokens"]
    assert snapshot["estimator"] == "deterministic-v1"


def test_context_snapshots_endpoint_breakdown_sums_to_total(client: TestClient) -> None:
    created = run_simple(client)
    snapshots = client.get(f"/api/sessions/{created['session_id']}/context-snapshots").json()
    assert len(snapshots) == 2  # one per model call
    for snapshot in snapshots:
        assert sum(snapshot["component_tokens"].values()) == snapshot["total_tokens"]
        assert snapshot["reserved_output_tokens"] == 50
    assert snapshots[1]["total_tokens"] >= snapshots[0]["total_tokens"]  # growth


def test_replay_endpoint_rebuilds_without_execution(client: TestClient) -> None:
    created = run_simple(client)
    session_id = str(created["session_id"])
    first = client.get(f"/api/sessions/{session_id}/replay")
    second = client.get(f"/api/sessions/{session_id}/replay")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # pure function of the log
    replay = first.json()
    assert replay["status"] == "finished"
    assert replay["final_answer"] == created["answer"]
    assert replay["steps"][0]["tool_calls"][0]["name"] == "word_count"
    assert replay["steps"][0]["tool_calls"][0]["value"] == 18


def test_evaluation_endpoint_returns_real_report(client: TestClient) -> None:
    created = run_simple(client)
    response = client.get(f"/api/sessions/{created['session_id']}/evaluation")
    assert response.status_code == 200
    report = response.json()
    assert report["session_id"] == created["session_id"]
    assert report["task_completed"] == 1
    assert 0.0 <= report["score"] <= 1.0
    # The score must be stable across calls (pure function of the log).
    again = client.get(f"/api/sessions/{created['session_id']}/evaluation")
    assert again.json() == report


def test_unknown_session_maps_to_404_on_every_read_endpoint(client: TestClient) -> None:
    missing = "no-such-session"
    assert client.get(f"/api/sessions/{missing}").status_code == 404
    assert client.get(f"/api/sessions/{missing}/events").status_code == 404
    assert client.get(f"/api/sessions/{missing}/prompt-snapshots").status_code == 404
    assert client.get(f"/api/sessions/{missing}/context-snapshots").status_code == 404
    assert client.get(f"/api/sessions/{missing}/replay").status_code == 404
    assert client.get(f"/api/sessions/{missing}/evaluation").status_code == 404


def test_inspector_page_renders_all_required_panels(client: TestClient) -> None:
    response = client.get("/")
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


def test_invalid_scenario_rejected_with_422(client: TestClient) -> None:
    response = client.post("/api/runs", json={"scenario": "chaos"})
    assert response.status_code == 422

"""Background execution: 202 + polling, queue capacity 429, startup sweep."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import apps.api.main as main_module
from apps.api.main import create_app
from packages.observability.eventbus import EventBus
from packages.observability.sqlite import SqlitePersistence


@pytest.fixture()
def api(tmp_path: Path):  # type: ignore[no-untyped-def]
    with TestClient(create_app(tmp_path / "agentflow.db")) as test_client:
        yield test_client


def wait_terminal(client: TestClient, session_id: str, attempts: int = 200) -> dict:
    for _ in range(attempts):
        detail = client.get(f"/api/sessions/{session_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        if body["status"] != "running":
            return body
    raise AssertionError("session did not reach a terminal state in time")


def test_run_is_accepted_202_and_reaches_terminal_state(api: TestClient) -> None:
    response = api.post("/api/runs", json={"scenario": "simple"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    # The projection row exists the instant 202 is returned.
    detail = api.get(f"/api/sessions/{body['session_id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] in ("running", "finished")
    terminal = wait_terminal(api, str(body["session_id"]))
    assert terminal["status"] == "finished"


def test_queue_full_returns_429_with_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With one worker and a blocked run, the next submit gets 429."""
    monkeypatch.setenv("AGENTFLOW_RUN_WORKERS", "1")
    release = threading.Event()

    original = main_module.build_offline_session

    def first_blocks(db_path: Path, request: object) -> object:
        built = original(db_path, request)  # type: ignore[call-arg]
        built.session.run = lambda: release.wait(timeout=30)  # type: ignore[method-assign]
        return built

    monkeypatch.setattr(main_module, "build_offline_session", first_blocks)
    with TestClient(create_app(tmp_path / "agentflow.db")) as client:
        first = client.post("/api/runs", json={"scenario": "simple"})
        assert first.status_code == 202
        # The worker is busy inside the blocked run; the next submission bounces.
        second = client.post("/api/runs", json={"scenario": "simple"})
        assert second.status_code == 429
        body = second.json()
        assert body["error"]["code"] == "RUN_QUEUE_FULL"
        assert body["error"]["details"]["workers"] == 1
        release.set()


def _running_session_row(db: Path, session_id: str) -> None:
    persistence = SqlitePersistence(EventBus(), db)
    try:
        with persistence._db.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, created_at, task, model, status) "
                "VALUES (?, datetime('now'), 'stale task', 'fake-model', 'running')",
                (session_id,),
            )
            conn.execute(
                "INSERT INTO events (event_id, session_id, sequence, event_type, data) "
                "VALUES (?, ?, 0, 'SessionStarted', ?)",
                (
                    f"{session_id}-0000",
                    session_id,
                    (
                        '{"event_id":"'
                        + f"{session_id}-0000"
                        + '","session_id":"'
                        + session_id
                        + '","trace_id":"t","sequence":0,'
                        '"timestamp":"2026-09-06T00:00:00Z","event_type":"SessionStarted",'
                        '"payload":{"task":"stale task","model":"fake-model"},'
                        '"schema_version":"1.0"}'
                    ),
                ),
            )
    finally:
        persistence.close()


def test_startup_sweep_marks_stale_running_failed(tmp_path: Path) -> None:
    db = tmp_path / "agentflow.db"
    stale_id = "stale-session-0001"
    _running_session_row(db, stale_id)

    # A previous process died here; a fresh app must sweep the ghost.
    with TestClient(create_app(db)) as client:
        sessions = client.get("/api/sessions").json()
        match = [s for s in sessions if s["session_id"] == stale_id]
        assert len(match) == 1
        assert match[0]["status"] == "failed"

    # The sweep appended a terminal event (single tx with the projection flip).
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT sequence, event_type FROM events WHERE session_id = ? ORDER BY sequence",
            (stale_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows[-1][1] == "AgentFailed"
    assert rows[-1][0] == 1  # continues the sequence, keeps integrity


def test_sweep_leaves_terminal_sessions_alone(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "agentflow.db")) as client:
        created = client.post("/api/runs", json={"scenario": "simple"})
        assert created.status_code == 202
        assert wait_terminal(client, str(created.json()["session_id"]))["status"] == "finished"

    # A second app over the same DB re-runs the sweep; nothing changes.
    with TestClient(create_app(tmp_path / "agentflow.db")) as client:
        detail = client.get(f"/api/sessions/{created.json()['session_id']}")
        assert detail.json()["status"] == "finished"

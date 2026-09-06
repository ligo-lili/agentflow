"""End-to-end integration: run, persist, restart, replay, evaluate — one chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.evals.evaluator import EvaluationConfig, Evaluator
from packages.observability.replay import SessionReplayer
from packages.observability.sqlite import SqliteEventStore, SqliteSnapshotStore


def test_api_run_flows_through_replay_and_evaluation(tmp_path: Path) -> None:
    """POST /api/runs → SQLite → /replay → /evaluation, one connected chain."""
    app = create_app(tmp_path / "agentflow.db")
    with TestClient(app) as client:
        created = client.post("/api/runs", json={"scenario": "compaction"}).json()
        session_id = str(created["session_id"])

        replay = client.get(f"/api/sessions/{session_id}/replay").json()
        evaluation = client.get(f"/api/sessions/{session_id}/evaluation").json()

    assert created["status"] == "finished"
    assert replay["status"] == "finished"
    assert replay["final_answer"] == created["answer"]
    assert len(replay["compactions"]) >= 1
    assert evaluation["session_id"] == session_id
    assert evaluation["compaction_count"] == len(replay["compactions"])
    assert evaluation["task_completed"] == 1
    assert evaluation["peak_context_tokens"] >= evaluation["final_context_tokens"]


def test_sqlite_restart_roundtrip_feeds_replay_and_evaluation(tmp_path: Path) -> None:
    """The writer process is gone; fresh stores rebuild everything."""
    from packages.observability.eventbus import EventBus
    from packages.observability.sqlite import SqlitePersistence
    from tests.test_replay import run_full_session

    db = tmp_path / "final.db"
    event_bus = EventBus()
    persistence = SqlitePersistence(event_bus, db)
    snapshots_writer = SqliteSnapshotStore(db)
    session = run_full_session("final-e2e", snapshots_writer, bus=event_bus)
    result = session.run()
    persistence.close()
    snapshots_writer.close()
    assert result.status == "finished"

    event_store = SqliteEventStore(db)
    snapshot_store = SqliteSnapshotStore(db)
    try:
        replay = SessionReplayer(event_store, snapshot_store).load("final-e2e")
    finally:
        event_store.close()
        snapshot_store.close()

    report = Evaluator(EvaluationConfig()).evaluate(replay)
    assert result.status == "finished"
    assert report.task_completed == 1
    assert report.final_context_tokens > 0
    assert report.compaction_count >= 1
    assert 0.0 <= report.score <= 1.0


def test_strategy_comparison_is_reproducible_for_the_portfolio() -> None:
    from packages.experiments import CANONICAL_STRATEGIES, compare_strategies

    first = compare_strategies()
    second = compare_strategies()
    assert first == second
    assert [e.strategy for e in first.entries] == list(CANONICAL_STRATEGIES)
    scores = {e.strategy: e.report.score for e in first.entries}
    assert all(0.0 <= s <= 1.0 for s in scores.values())
    assert first.winner in scores
    assert pytest.approx(
        max(scores.values()) - min(scores.values()), abs=1e-6
    ) == first.score_delta

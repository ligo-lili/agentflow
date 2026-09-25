"""Deployment surface: optional bearer auth, CORS, /healthz."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture()
def secured_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    return create_app(tmp_path / "agentflow.db", env={"AGENTFLOW_AUTH_TOKEN": "sekrit-token"})


def test_healthz_is_never_authenticated(secured_app) -> None:  # type: ignore[no-untyped-def]
    with TestClient(secured_app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz").json() == {"status": "ok"}


def test_api_without_token_is_401_envelope(secured_app) -> None:  # type: ignore[no-untyped-def]
    with TestClient(secured_app) as client:
        response = client.get("/api/sessions")
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "UNAUTHORIZED"
        assert "sekrit-token" not in response.text


def test_api_with_wrong_or_malformed_token_is_401(secured_app) -> None:  # type: ignore[no-untyped-def]
    with TestClient(secured_app) as client:
        wrong = client.get("/api/sessions", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        malformed = client.get("/api/sessions", headers={"Authorization": "sekrit-token"})
        assert malformed.status_code == 401


def test_api_with_correct_token_succeeds(secured_app) -> None:  # type: ignore[no-untyped_def]
    with TestClient(secured_app) as client:
        response = client.get("/api/sessions", headers={"Authorization": "Bearer sekrit-token"})
        assert response.status_code == 200
        assert response.json() == []


def test_without_auth_config_no_header_is_needed(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "agentflow.db")) as client:
        assert client.get("/api/sessions").status_code == 200


def test_cors_headers_are_applied_for_configured_origins(tmp_path: Path) -> None:
    app = create_app(
        tmp_path / "agentflow.db",
        env={"AGENTFLOW_CORS_ORIGINS": "https://ops.example.com"},
    )
    with TestClient(app) as client:
        preflight = client.options(
            "/api/sessions",
            headers={
                "Origin": "https://ops.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert (
            preflight.headers.get("access-control-allow-origin")
            == "https://ops.example.com"
        )
        # Unlisted origins are not granted access.
        stranger = client.options(
            "/api/sessions",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert stranger.headers.get("access-control-allow-origin") is None

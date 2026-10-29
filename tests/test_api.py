from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_status(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        status = client.get("/api/status")
        assert status.status_code == 200
        payload = status.json()
        assert [item["id"] for item in payload["streamers"]] == ["alice", "bob"]
        assert payload["config_error"] == ""
        assert payload["active"] is None


def test_index_page(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "py_restream" in response.text


def test_basic_auth(settings, streamers_file) -> None:
    app = create_app(
        replace(
            settings,
            streamers_file=streamers_file,
            dashboard_password="s3cret",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/status", auth=("admin", "s3cret")).status_code == 200
        assert client.get("/api/status", auth=("admin", "wrong")).status_code == 401


def test_reload_endpoint(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post("/api/reload")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["streamers"] == ["alice", "bob"]


def test_logs_unknown_streamer(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        assert client.get("/api/logs/nobody").status_code == 404
        assert client.get("/api/logs/alice").json() == {"lines": []}


def test_start_unknown_streamer(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post("/api/streamers/nobody/start")
        assert response.status_code == 404

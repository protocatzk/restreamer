from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.store import load_streamers


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
        assert 'id="streamer-dialog"' in response.text
        assert 'id="settings-dialog"' in response.text
        assert 'id="playlist-dialog"' in response.text
        assert 'id="playlist-tabs"' in response.text
        assert 'id="playlist-new-btn"' in response.text
        assert 'id="channel-strip"' in response.text


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


def test_create_update_delete_streamer(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        payload = {
            "id": "carol",
            "name": "Carol",
            "tiktok": "@carol",
            "youtube_key": "top-secret-key",
            "video_mode": "copy",
            "enabled": True,
        }
        created = client.post("/api/streamers", json=payload)
        assert created.status_code == 201
        assert "top-secret-key" not in created.text

        status = client.get("/api/status").json()
        carol = next(item for item in status["streamers"] if item["id"] == "carol")
        assert carol["key_source"] == "config"
        assert carol["key_configured"] is True
        assert "top-secret-key" not in str(status)

        updated = client.put(
            "/api/streamers/carol",
            json={
                "id": "carol",
                "name": "Carol neu",
                "tiktok": "carol",
                "youtube_key": "",
                "video_mode": "reencode",
                "enabled": True,
            },
        )
        assert updated.status_code == 200

        streamers = {item.id: item for item in load_streamers(streamers_file)}
        assert streamers["carol"].name == "Carol neu"
        assert streamers["carol"].youtube_key == "top-secret-key"
        assert streamers["carol"].video_mode == "reencode"

        duplicate = client.post("/api/streamers", json=payload)
        assert duplicate.status_code == 409

        deleted = client.delete("/api/streamers/carol")
        assert deleted.status_code == 200
        assert "carol" not in [item.id for item in load_streamers(streamers_file)]


def test_create_streamer_without_key_rejected(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post(
            "/api/streamers",
            json={"id": "carol", "name": "Carol", "tiktok": "carol"},
        )
        assert response.status_code == 400
        assert "youtube_key" in response.text


def test_update_settings(tmp_path, settings, streamers_file) -> None:
    runtime_file = tmp_path / "config" / "settings.yml"
    cookies_file = tmp_path / "data" / "cookies.txt"
    settings = replace(
        settings,
        streamers_file=streamers_file,
        runtime_file=runtime_file,
        cookies_file=cookies_file,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.put(
            "/api/settings",
            json={
                "offline_poll_seconds": 30,
                "auto_start": True,
                "tiktok_session_id": "session-abc",
                "ytdlp_extra_args": ["--foo"],
            },
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["offline_poll_seconds"] == 30
        assert payload["auto_start"] is True
        assert payload["tiktok_session_id_set"] is True
        assert "session-abc" not in response.text
        assert settings.offline_poll_seconds == 30
        assert runtime_file.exists()
        assert (runtime_file.stat().st_mode & 0o777) == 0o600
        assert "session-abc" in cookies_file.read_text(encoding="utf-8")

        invalid = client.put(
            "/api/settings", json={"ffmpeg_loglevel": "loud"}
        )
        assert invalid.status_code == 400

        cleared = client.put(
            "/api/settings",
            json={"clear_tiktok_session": True},
        )
        assert cleared.status_code == 200
        assert cleared.json()["tiktok_session_id_set"] is False
        assert not cookies_file.exists()


def test_playlists_default_empty(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        payload = client.get("/api/playlists").json()
        assert payload["playlists"] == []
        assert payload["active_playlist"] is None

        status = client.get("/api/status").json()
        assert status["playlists"] == []
        assert status["active_playlist"] is None


def test_create_update_delete_playlists(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        created = client.post(
            "/api/playlists",
            json={
                "id": "abend",
                "name": "Abendprogramm",
                "channels": ["bob", "alice"],
                "auto_advance": True,
                "wrap": False,
                "advance_delay_seconds": 4,
                "youtube_key": "shared-secret-key",
            },
        )
        assert created.status_code == 201
        assert created.json()["playlist_id"] == "abend"
        assert "shared-secret-key" not in created.text

        second = client.post(
            "/api/playlists",
            json={"name": "Nachtprogramm", "channels": ["alice"]},
        )
        assert second.status_code == 201
        assert second.json()["playlist_id"] == "nachtprogramm"

        duplicate = client.post(
            "/api/playlists", json={"id": "abend", "name": "Doppelt"}
        )
        assert duplicate.status_code == 409

        payload = client.get("/api/playlists").json()
        assert [item["id"] for item in payload["playlists"]] == [
            "abend",
            "nachtprogramm",
        ]
        abend = payload["playlists"][0]
        assert abend["channel_ids"] == ["bob", "alice"]
        assert abend["shared_key_source"] == "config"
        assert abend["shared_key_stored"] is True
        assert abend["advance_delay_seconds"] == 4
        assert abend["channels"][0]["number"] == 1
        assert abend["channels"][0]["id"] == "bob"

        status = client.get("/api/status").json()
        assert len(status["playlists"]) == 2
        assert "shared-secret-key" not in str(status)

        cleared = client.put(
            "/api/playlists/abend",
            json={"clear_youtube_key": True},
        )
        assert cleared.status_code == 200
        updated = next(
            item
            for item in cleared.json()["playlists"]
            if item["id"] == "abend"
        )
        assert updated["shared_key_source"] == "missing"
        assert updated["shared_key_stored"] is False

        deleted = client.delete("/api/playlists/nachtprogramm")
        assert deleted.status_code == 200
        assert [item["id"] for item in deleted.json()["playlists"]] == ["abend"]

        missing = client.delete("/api/playlists/nobody")
        assert missing.status_code == 404


def test_playlist_rejects_unknown_channel(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post(
            "/api/playlists",
            json={"id": "abend", "channels": ["alice", "nobody"]},
        )
        assert response.status_code == 400
        assert "unbekannten Streamer" in response.text


def test_playlist_start_without_channels(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        created = client.post(
            "/api/playlists", json={"id": "leer", "name": "Leer"}
        )
        assert created.status_code == 201

        response = client.post("/api/playlists/leer/start")
        assert response.status_code == 400
        assert "leer" in response.text

        unknown = client.post("/api/playlists/nobody/start")
        assert unknown.status_code == 404


def test_playlist_start_fails_without_shared_key_value(
    settings, streamers_file, monkeypatch
) -> None:
    monkeypatch.delenv("UNSET_PLAYLIST_KEY", raising=False)
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        saved = client.post(
            "/api/playlists",
            json={
                "id": "abend",
                "channels": ["alice"],
                "youtube_key_env": "UNSET_PLAYLIST_KEY",
            },
        )
        assert saved.status_code == 201

        response = client.post("/api/playlists/abend/start")
        assert response.status_code == 400
        assert "gemeinsamer YouTube-Key" in response.text


def test_playlist_tune_requires_membership(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        created = client.post(
            "/api/playlists",
            json={"id": "abend", "channels": ["alice"]},
        )
        assert created.status_code == 201

        response = client.post("/api/playlists/abend/tune/bob")
        assert response.status_code == 404
        assert "nicht Teil der Senderliste" in response.text

        unknown = client.post("/api/playlists/nobody/tune/alice")
        assert unknown.status_code == 404

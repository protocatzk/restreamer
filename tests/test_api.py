from __future__ import annotations

import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.store import load_config, load_streamers


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
        assert payload["online_check"]["enabled"] is False
        assert payload["earnings"]["enabled"] is False
        assert payload["earnings"]["status"] == "unconfigured"
        assert all(item["online"] is None for item in payload["streamers"])


def test_earnings_endpoints(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.get("/api/earnings")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["earnings"]["enabled"] is False
        assert response.json()["earnings"]["view"] == "standard"

        check = client.post("/api/earnings/check")
        assert check.status_code == 200
        assert check.json()["ok"] is True
        assert check.json()["earnings"]["enabled"] is False


def test_earnings_source_is_exposed_without_active_stream(
    settings, streamers_file
) -> None:
    app = create_app(
        replace(
            settings,
            streamers_file=streamers_file,
            earnings_enabled=True,
            earnings_source_url="https://example.test/earnings",
            earnings_period="today",
        )
    )

    with TestClient(app) as client:
        payload = client.get("/api/status").json()["earnings"]

    assert payload["configured"] is True
    assert payload["enabled"] is True
    assert payload["period_label"] == "Heute"
    assert payload["status"] == "inactive"
    assert payload["data"] is None


def test_online_check_endpoint(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post("/api/online/check")
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["online_check"]["enabled"] is False


def test_online_status_is_exposed(
    settings, streamers_file, monkeypatch
) -> None:
    def fake_fetch_json(
        url: str, headers: dict[str, str], timeout: float
    ) -> object:
        return {
            "streamers": [
                {"username": "alice", "is_live": True},
                {"username": "bob", "is_live": False},
            ]
        }

    monkeypatch.setattr("app.online._fetch_json", fake_fetch_json)
    app = create_app(
        replace(
            settings,
            streamers_file=streamers_file,
            online_check_enabled=True,
            online_check_interval_seconds=10,
            online_source_url="https://example.test/status",
        )
    )

    with TestClient(app) as client:
        payload = {}
        for _ in range(100):
            payload = client.get("/api/status").json()
            if all(
                item["online"] is not None for item in payload["streamers"]
            ):
                break
            time.sleep(0.01)

        online = {item["id"]: item["online"] for item in payload["streamers"]}
        assert online == {"alice": True, "bob": False}
        assert payload["online_check"]["enabled"] is True
        assert payload["online_check"]["checked"] == 2


def test_index_page(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Restreamer" in response.text
        assert 'id="streamer-dialog"' in response.text
        assert 'id="settings-dialog"' in response.text
        assert 'id="playlist-dialog"' in response.text
        assert 'id="playlist-tabs"' in response.text
        assert 'id="playlist-new-btn"' in response.text
        assert 'id="playlist-end-mode"' in response.text
        assert 'name="end_after_minutes"' in response.text
        assert 'name="end_time"' in response.text
        assert 'id="channel-strip"' in response.text
        assert 'id="quick-add-form"' in response.text
        assert 'id="quick-playlist"' in response.text
        assert 'name="chat_enabled"' in response.text
        assert 'name="chat_source_url"' in response.text
        assert 'name="chat_source_api_key"' in response.text
        assert 'id="chat-key-hint"' in response.text
        assert 'name="clock_enabled"' in response.text
        assert 'name="clock_position"' in response.text
        assert 'name="clock_show_seconds"' in response.text
        assert 'name="version_overlay_enabled"' in response.text
        assert 'name="text_overlay_enabled"' in response.text
        assert 'name="text_overlay_text"' in response.text
        assert 'name="text_overlay_position"' in response.text
        assert 'name="text_overlay_speed"' in response.text
        assert 'name="online_check_enabled"' in response.text
        assert 'name="online_check_interval_seconds"' in response.text
        assert 'name="online_check_timeout_seconds"' in response.text
        assert 'id="online-check-btn"' in response.text
        assert 'id="online-count"' in response.text
        assert 'name="earnings_enabled"' in response.text
        assert 'name="earnings_source_url"' in response.text
        assert 'name="earnings_source_api_key"' in response.text
        assert 'name="earnings_period"' in response.text
        assert 'name="earnings_view"' in response.text
        assert 'name="earnings_refresh_seconds"' in response.text
        assert 'name="earnings_timeout_seconds"' in response.text
        assert 'name="earnings_overlay_enabled"' in response.text
        assert 'name="earnings_overlay_template"' in response.text
        assert 'name="earnings_overlay_position"' in response.text
        assert 'id="earnings-key-hint"' in response.text
        assert 'id="earnings-refresh-btn"' in response.text
        assert 'id="earnings-net"' in response.text
        assert 'id="earnings-extra"' in response.text
        assert 'id="earnings-session"' in response.text
        assert 'id="earnings-top-gift"' in response.text
        assert 'name="own_stream"' in response.text
        assert 'name="youtube_title_template"' in response.text
        assert 'id="now-title-row"' in response.text
        assert 'id="copy-title-btn"' in response.text


def test_chat_settings_are_exposed(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        payload = client.get("/api/settings").json()

    assert payload["chat_enabled"] is False
    assert payload["chat_source_url"] == ""
    assert payload["chat_source_api_key_set"] is False
    assert payload["chat_poll_seconds"] == 5
    assert payload["clock_enabled"] is False
    assert payload["clock_position"] == "top-left"
    assert payload["version_overlay_enabled"] is True
    assert payload["text_overlay_enabled"] is False
    assert payload["text_overlay_text"] == ""
    assert payload["text_overlay_position"] == "bottom"
    assert payload["text_overlay_speed"] == 100
    assert payload["earnings_enabled"] is True
    assert payload["earnings_source_url"] == ""
    assert payload["earnings_source_api_key_set"] is False
    assert payload["earnings_period"] == "today"
    assert payload["earnings_view"] == "standard"
    assert payload["earnings_refresh_seconds"] == 300
    assert payload["earnings_overlay_enabled"] is False


def test_earnings_api_key_can_be_cleared(
    settings, streamers_file, tmp_path
) -> None:
    app = create_app(
        replace(
            settings,
            streamers_file=streamers_file,
            runtime_file=tmp_path / "settings.yml",
            offline_poll_seconds=60,
            earnings_source_api_key="secret-earnings-key",
        )
    )

    with TestClient(app) as client:
        assert (
            client.get("/api/settings").json()["earnings_source_api_key_set"]
            is True
        )

        response = client.put(
            "/api/settings", json={"clear_earnings_source_api_key": True}
        )

        assert response.status_code == 200
        assert response.json()["earnings_source_api_key_set"] is False


def test_chat_api_key_can_be_cleared(settings, streamers_file, tmp_path) -> None:
    app = create_app(
        replace(
            settings,
            streamers_file=streamers_file,
            runtime_file=tmp_path / "settings.yml",
            offline_poll_seconds=60,
            chat_source_api_key="secret-chat-key",
        )
    )

    with TestClient(app) as client:
        assert client.get("/api/settings").json()["chat_source_api_key_set"] is True

        response = client.put(
            "/api/settings", json={"clear_chat_source_api_key": True}
        )

        assert response.status_code == 200
        assert response.json()["chat_source_api_key_set"] is False


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
            "preferred": True,
            "own_stream": True,
        }
        created = client.post("/api/streamers", json=payload)
        assert created.status_code == 201
        assert "top-secret-key" not in created.text

        status = client.get("/api/status").json()
        carol = next(item for item in status["streamers"] if item["id"] == "carol")
        assert carol["key_source"] == "config"
        assert carol["key_configured"] is True
        assert carol["preferred"] is True
        assert carol["own_stream"] is True
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
                "preferred": False,
                "own_stream": False,
            },
        )
        assert updated.status_code == 200

        streamers = {item.id: item for item in load_streamers(streamers_file)}
        assert streamers["carol"].name == "Carol neu"
        assert streamers["carol"].youtube_key == "top-secret-key"
        assert streamers["carol"].video_mode == "reencode"
        assert streamers["carol"].preferred is False
        assert streamers["carol"].own_stream is False

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
                "output_orientation": "landscape",
                "online_check_enabled": False,
                "online_check_interval_seconds": 90,
                "online_check_timeout_seconds": 25,
                "online_source_url": (
                    "https://toktracker.cc/api/public/overview"
                ),
                "online_source_api_key": "online-secret",
                "earnings_source_url": (
                    "https://toktracker.cc/api/public/overview"
                ),
                "earnings_source_api_key": "earnings-secret",
                "earnings_period": "7d",
                "earnings_view": "detailed",
                "earnings_refresh_seconds": 120,
                "earnings_overlay_enabled": True,
                "earnings_overlay_template": "{period}: {net}",
                "earnings_overlay_position": "bottom-right",
                "youtube_title_template": "@{tiktok} live: {title}",
            },
        )
        assert response.status_code == 200
        payload = response.json()

        assert payload["offline_poll_seconds"] == 30
        assert payload["auto_start"] is True
        assert payload["tiktok_session_id_set"] is True
        assert payload["output_orientation"] == "landscape"
        assert payload["online_check_enabled"] is False
        assert payload["online_check_interval_seconds"] == 90
        assert payload["online_check_timeout_seconds"] == 25
        assert (
            payload["online_source_url"]
            == "https://toktracker.cc/api/public/overview"
        )
        assert payload["online_source_api_key_set"] is True
        assert (
            payload["earnings_source_url"]
            == "https://toktracker.cc/api/public/overview"
        )
        assert payload["earnings_source_api_key_set"] is True
        assert payload["earnings_period"] == "7d"
        assert payload["earnings_view"] == "detailed"
        assert payload["earnings_refresh_seconds"] == 120
        assert payload["earnings_overlay_enabled"] is True
        assert payload["earnings_overlay_template"] == "{period}: {net}"
        assert payload["earnings_overlay_position"] == "bottom-right"
        assert payload["youtube_title_template"] == "@{tiktok} live: {title}"
        assert "session-abc" not in response.text
        assert "online-secret" not in response.text
        assert "earnings-secret" not in response.text
        assert settings.offline_poll_seconds == 30
        assert settings.output_orientation == "landscape"
        assert runtime_file.exists()
        assert (runtime_file.stat().st_mode & 0o777) == 0o600
        assert "session-abc" in cookies_file.read_text(encoding="utf-8")

        invalid = client.put(
            "/api/settings", json={"ffmpeg_loglevel": "loud"}
        )
        assert invalid.status_code == 400

        cleared_online = client.put(
            "/api/settings",
            json={"clear_online_source_api_key": True},
        )
        assert cleared_online.status_code == 200
        assert cleared_online.json()["online_source_api_key_set"] is False

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


def test_create_playlist_with_end_settings(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        created = client.post(
            "/api/playlists",
            json={
                "id": "abend",
                "name": "Abendprogramm",
                "channels": ["alice"],
                "end_after_minutes": 90,
            },
        )
        assert created.status_code == 201

        view = next(
            item
            for item in client.get("/api/playlists").json()["playlists"]
            if item["id"] == "abend"
        )
        assert view["end_after_minutes"] == 90
        assert view["end_time"] == ""
        assert view["ends_at"] is None
        assert view["end_in_seconds"] is None

        switched = client.put(
            "/api/playlists/abend", json={"end_time": "23:00"}
        )
        assert switched.status_code == 200
        updated = next(
            item
            for item in switched.json()["playlists"]
            if item["id"] == "abend"
        )
        assert updated["end_time"] == "23:00"
        assert updated["end_after_minutes"] == 0

        invalid = client.put(
            "/api/playlists/abend", json={"end_time": "25:00"}
        )
        assert invalid.status_code == 400

        both = client.put(
            "/api/playlists/abend",
            json={"end_time": "23:00", "end_after_minutes": 60},
        )
        assert both.status_code == 400


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


def test_quick_add_streamers_inherits_playlist_key(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        playlist = client.post(
            "/api/playlists",
            json={
                "id": "abend",
                "name": "Abend",
                "channels": ["alice"],
                "youtube_key": "shared-key",
            },
        )
        assert playlist.status_code == 201

        response = client.post(
            "/api/streamers/quick",
            json={
                "tiktoks": "https://www.tiktok.com/@carol/live",
                "playlist_id": "abend",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["created"]] == ["carol"]
        assert payload["skipped"] == []
        assert payload["playlist_id"] == "abend"
        assert "shared-key" not in response.text

        streamers = {item.id: item for item in load_streamers(streamers_file)}
        assert streamers["carol"].tiktok == "carol"
        assert streamers["carol"].youtube_key == "shared-key"

        _, playlists = load_config(streamers_file)
        assert playlists[0].channels == ("alice", "carol")

        status = client.get("/api/status").json()
        assert "shared-key" not in str(status)


def test_quick_add_streamers_bulk_skips_duplicates(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post(
            "/api/streamers/quick",
            json={
                "tiktoks": "alice\ndave\n@dave\n!bad",
                "youtube_key": "direct-key",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["id"] for item in payload["created"]] == ["dave"]
        assert len(payload["skipped"]) == 3

        streamers = {item.id: item for item in load_streamers(streamers_file)}
        assert streamers["dave"].youtube_key == "direct-key"


def test_quick_add_streamers_without_key_rejected(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        response = client.post(
            "/api/streamers/quick", json={"tiktoks": "carol"}
        )
        assert response.status_code == 400
        assert "YouTube-Key" in response.text

        created = client.post(
            "/api/playlists", json={"id": "leer", "channels": ["alice"]}
        )
        assert created.status_code == 201
        without_key = client.post(
            "/api/streamers/quick",
            json={"tiktoks": "carol", "playlist_id": "leer"},
        )
        assert without_key.status_code == 400
        assert "YouTube-Key" in without_key.text


def test_quick_add_streamers_validates_input(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))

    with TestClient(app) as client:
        empty = client.post("/api/streamers/quick", json={"tiktoks": " , "})
        assert empty.status_code == 400

        unknown = client.post(
            "/api/streamers/quick",
            json={"tiktoks": "carol", "playlist_id": "nobody"},
        )
        assert unknown.status_code == 404

        too_many = client.post(
            "/api/streamers/quick",
            json={
                "tiktoks": [f"user{index}" for index in range(101)],
                "youtube_key": "key",
            },
        )
        assert too_many.status_code == 400
        assert "Maximal" in too_many.text

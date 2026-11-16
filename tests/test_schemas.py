from __future__ import annotations

from app.schemas import (
    EarningsResponse,
    PlaylistMutationResponse,
    QuickAddResponse,
    SettingsResponse,
    StatusResponse,
)


def test_status_response_keeps_unknown_fields() -> None:
    model = StatusResponse.model_validate(
        {
            "active": "alice",
            "state": "live",
            "online_check": {"enabled": False},
            "output_bitrate_kbps": 4200.5,
        }
    )

    payload = model.model_dump()
    assert payload["state"] == "live"
    assert payload["online_check"] == {"enabled": False}
    assert payload["output_bitrate_kbps"] == 4200.5


def test_settings_response_keeps_editable_fields() -> None:
    model = SettingsResponse.model_validate(
        {
            "settings_file": "/app/config/settings.yml",
            "offline_poll_seconds": 45,
            "dashboard_password_set": True,
        }
    )

    payload = model.model_dump()
    assert payload["settings_file"] == "/app/config/settings.yml"
    assert payload["offline_poll_seconds"] == 45
    assert payload["dashboard_password_set"] is True


def test_nested_response_models_preserve_payloads() -> None:
    playlists = PlaylistMutationResponse.model_validate(
        {
            "playlist_id": "main",
            "playlists": [{"id": "main"}],
            "active_playlist": "main",
        }
    )
    quick = QuickAddResponse.model_validate(
        {
            "ok": True,
            "created": [{"id": "alice", "name": "Alice", "tiktok": "alice"}],
            "skipped": ["bob"],
            "streamers": ["alice"],
        }
    )
    earnings = EarningsResponse.model_validate(
        {"ok": True, "earnings": {"enabled": True, "checked": 1}}
    )

    assert playlists.playlists[0]["id"] == "main"
    assert quick.created[0].id == "alice"
    assert quick.skipped == ["bob"]
    assert earnings.earnings["checked"] == 1

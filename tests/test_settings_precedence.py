from __future__ import annotations

from dataclasses import replace

import pytest

from app.settings import Settings


def test_runtime_file_overrides_environment(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "settings.yml"
    monkeypatch.setenv("RUNTIME_SETTINGS_FILE", str(runtime))
    monkeypatch.setenv("OFFLINE_POLL_SECONDS", "30")

    from_env = Settings.from_env()
    assert from_env.offline_poll_seconds == 30

    runtime.write_text(
        "settings:\n  offline_poll_seconds: 45\n", encoding="utf-8"
    )
    loaded = Settings.from_env()

    assert loaded.offline_poll_seconds == 45


def test_dashboard_override_wins_and_does_not_mutate_base(settings) -> None:
    updated = settings.updated(
        {"offline_poll_seconds": 90, "output_orientation": "landscape"}
    )

    assert updated.offline_poll_seconds == 90
    assert updated.output_orientation == "landscape"
    assert settings.offline_poll_seconds == 1
    assert settings.output_orientation == "portrait"


def test_unknown_field_is_rejected(settings) -> None:
    with pytest.raises(ValueError, match="Unbekanntes Feld"):
        settings.updated({"does_not_exist": 1})


def test_invalid_value_is_rejected(settings) -> None:
    with pytest.raises(ValueError, match="Ganzzahl"):
        settings.updated({"offline_poll_seconds": "keine Zahl"})


def test_group_views_reflect_the_flat_fields(settings) -> None:
    grouped = replace(
        settings,
        output_orientation="landscape",
        relay_port=20000,
        asr_tap_enabled=True,
        telegram_notifications=False,
    )

    assert grouped.output.width == 1280
    assert grouped.output.height == 720
    assert grouped.relay.relay_port == 20000
    assert grouped.asr.tap_enabled is True
    assert grouped.asr.watchlist_file == grouped.asr_watchlist_file
    assert grouped.telegram.notifications is False
    assert (
        grouped.streamer.backoff_initial_seconds
        == grouped.backoff_initial_seconds
    )
    assert grouped.overlay.chat_enabled == grouped.chat_enabled

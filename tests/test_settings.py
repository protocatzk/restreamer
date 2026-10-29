from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cookies import write_tiktok_cookies
from app.settings import Settings


def test_public_dict_masks_secrets(tmp_path: Path) -> None:
    settings = Settings(
        runtime_file=tmp_path / "settings.yml",
        dashboard_password="super-secret-password",
        tiktok_session_id="super-secret-session",
    )

    data = settings.to_public_dict()
    serialized = json.dumps(data)

    assert data["dashboard_password_set"] is True
    assert data["tiktok_session_id_set"] is True
    assert "super-secret-password" not in serialized
    assert "super-secret-session" not in serialized
    assert data["offline_poll_seconds"] == 60


def test_updated_rejects_invalid_values(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="ffmpeg_loglevel"):
        settings.updated({"ffmpeg_loglevel": "loud"})
    with pytest.raises(ValueError, match="offline_poll"):
        settings.updated({"offline_poll_seconds": 1})
    with pytest.raises(ValueError, match="backoff_max"):
        settings.updated(
            {"backoff_initial_seconds": 60, "backoff_max_seconds": 10}
        )
    with pytest.raises(ValueError, match="Unbekanntes Feld"):
        settings.updated({"nope": 1})
    with pytest.raises(ValueError, match="auto_refresh_minutes"):
        settings.updated({"auto_refresh_minutes": -1})
    with pytest.raises(ValueError, match="stall_timeout_seconds"):
        settings.updated({"stall_timeout_seconds": 5})
    with pytest.raises(ValueError, match="watchdog_interval_seconds"):
        settings.updated(
            {"watchdog_interval_seconds": 30, "stall_timeout_seconds": 30}
        )


def test_auto_refresh_can_be_disabled(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    updated = settings.updated({"auto_refresh_minutes": 0})

    assert updated.auto_refresh_minutes == 0
    assert settings.updated({"auto_refresh_minutes": 1}).auto_refresh_minutes == 1


def test_new_defaults_are_present(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")
    data = settings.to_public_dict()

    assert data["auto_refresh_minutes"] == 20
    assert data["stall_timeout_seconds"] == 30
    assert data["watchdog_interval_seconds"] == 5


def test_updated_does_not_mutate_original(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    settings.updated({"offline_poll_seconds": 30})

    assert settings.offline_poll_seconds == 60


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.yml"
    settings = Settings(
        runtime_file=path,
        offline_poll_seconds=30,
        audio_bitrate="192k",
        dashboard_password="pw123",
        tiktok_session_id="sess123",
        ytdlp_extra_args=("--foo", "bar"),
    )

    settings.save_runtime()

    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600

    loaded = Settings(runtime_file=path)
    loaded._load_runtime()

    assert loaded.offline_poll_seconds == 30
    assert loaded.audio_bitrate == "192k"
    assert loaded.dashboard_password == "pw123"
    assert loaded.tiktok_session_id == "sess123"
    assert loaded.ytdlp_extra_args == ("--foo", "bar")


def test_write_tiktok_cookies(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"

    assert write_tiktok_cookies(path, "session-value", "useast5") is True

    content = path.read_text(encoding="utf-8")
    assert "\tsessionid\tsession-value" in content
    assert "\ttt-target-idc\tuseast5" in content
    assert (path.stat().st_mode & 0o777) == 0o600

    assert write_tiktok_cookies(path, "") is False

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
    assert data["pause_screen_enabled"] is True
    assert data["pause_screen_text"] == "Stream pausiert"
    assert data["pause_screen_subtext"] == "Gleich geht's weiter"
    assert data["pause_screen_background"] == "101418"
    assert data["pause_screen_font"] == ""
    assert data["pause_screen_max_minutes"] == 0
    assert data["reencode_video_bitrate"] == "5000k"
    assert data["audio_bitrate"] == "192k"
    assert data["output_fps"] == 25
    assert data["output_stretch_percent"] == 10
    assert data["relay_delay_seconds"] == 30
    assert data["source_min_rate_percent"] == 85
    assert data["online_check_enabled"] is True
    assert data["online_check_interval_seconds"] == 60
    assert data["online_check_timeout_seconds"] == 20
    assert data["online_source_url"] == ""
    assert data["online_source_api_key_set"] is False
    assert data["earnings_enabled"] is True
    assert data["earnings_source_url"] == ""
    assert data["earnings_source_api_key_set"] is False
    assert data["earnings_period"] == "today"
    assert data["earnings_refresh_seconds"] == 300
    assert data["earnings_timeout_seconds"] == 20
    assert data["earnings_overlay_enabled"] is False
    assert data["earnings_overlay_template"] == "{period}: {net} · Brutto {gross}"
    assert data["earnings_overlay_position"] == "top-left"
    assert data["earnings_overlay_font_size"] == 0
    assert data["earnings_overlay_font"] == ""
    assert data["youtube_title_template"] == "{name} live: {title}"


def test_online_check_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    assert settings.updated({"online_check_enabled": False}).online_check_enabled is False

    with pytest.raises(ValueError, match="online_check_interval_seconds"):
        settings.updated({"online_check_interval_seconds": 9})
    with pytest.raises(ValueError, match="online_check_timeout_seconds"):
        settings.updated({"online_check_timeout_seconds": 4})

    updated = settings.updated(
        {
            "online_check_interval_seconds": 120,
            "online_check_timeout_seconds": 30,
            "online_source_url": "https://toktracker.cc/api/public/overview",
            "online_source_api_key": "secret",
        }
    )
    assert updated.online_check_interval_seconds == 120
    assert updated.online_check_timeout_seconds == 30
    assert (
        updated.online_source_url
        == "https://toktracker.cc/api/public/overview"
    )
    assert updated.online_source_api_key == "secret"

    with pytest.raises(ValueError, match="online_source_url"):
        settings.updated({"online_source_url": "toktracker.cc/api"})


def test_earnings_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="earnings_source_url"):
        settings.updated({"earnings_source_url": "toktracker.cc/api"})
    with pytest.raises(ValueError, match="earnings_period"):
        settings.updated({"earnings_period": "yesterday"})
    with pytest.raises(ValueError, match="earnings_refresh_seconds"):
        settings.updated({"earnings_refresh_seconds": 29})
    with pytest.raises(ValueError, match="earnings_timeout_seconds"):
        settings.updated({"earnings_timeout_seconds": 4})
    with pytest.raises(ValueError, match="earnings_overlay_position"):
        settings.updated({"earnings_overlay_position": "middle"})
    with pytest.raises(ValueError, match="earnings_overlay_font_size"):
        settings.updated({"earnings_overlay_font_size": 201})
    with pytest.raises(ValueError, match="earnings_overlay_template"):
        settings.updated({"earnings_overlay_template": "x" * 201})

    updated = settings.updated(
        {
            "earnings_enabled": True,
            "earnings_source_url": "https://toktracker.cc/api/public/overview",
            "earnings_source_api_key": "secret-key",
            "earnings_period": "7d",
            "earnings_refresh_seconds": 120,
            "earnings_timeout_seconds": 30,
            "earnings_overlay_enabled": True,
            "earnings_overlay_template": "{period}: {net}",
            "earnings_overlay_position": "bottom-right",
            "earnings_overlay_font_size": 32,
            "earnings_overlay_font": "/tmp/font.ttf",
        }
    )
    assert updated.earnings_source_url == (
        "https://toktracker.cc/api/public/overview"
    )
    assert updated.earnings_source_api_key == "secret-key"
    assert updated.earnings_period == "7d"
    assert updated.earnings_refresh_seconds == 120
    assert updated.earnings_timeout_seconds == 30
    assert updated.earnings_overlay_enabled is True
    assert updated.earnings_overlay_template == "{period}: {net}"
    assert updated.earnings_overlay_position == "bottom-right"
    assert updated.earnings_overlay_font_size == 32
    assert updated.earnings_overlay_font == "/tmp/font.ttf"


def test_earnings_api_key_is_masked(tmp_path: Path) -> None:
    settings = Settings(
        runtime_file=tmp_path / "settings.yml",
        earnings_source_api_key="super-secret-earnings-key",
    )

    serialized = json.dumps(settings.to_public_dict())

    assert "super-secret-earnings-key" not in serialized
    assert settings.to_public_dict()["earnings_source_api_key_set"] is True


def test_youtube_title_template_is_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    updated = settings.updated(
        {"youtube_title_template": "@{tiktok} live: {title}"}
    )
    assert updated.youtube_title_template == "@{tiktok} live: {title}"

    with pytest.raises(ValueError, match="youtube_title_template"):
        settings.updated({"youtube_title_template": "x" * 201})


def test_output_orientation_can_be_switched(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    assert settings.output_orientation == "portrait"
    assert (settings.output_width, settings.output_height) == (720, 1280)

    landscape = settings.updated({"output_orientation": "landscape"})
    assert (landscape.output_width, landscape.output_height) == (1280, 720)

    with pytest.raises(ValueError, match="output_orientation"):
        settings.updated({"output_orientation": "square"})


def test_pause_screen_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="pause_screen_background"):
        settings.updated({"pause_screen_background": "rot"})
    with pytest.raises(ValueError, match="pause_screen_max_minutes"):
        settings.updated({"pause_screen_max_minutes": -1})
    with pytest.raises(ValueError, match="pause_screen_text"):
        settings.updated({"pause_screen_text": "x" * 121})
    with pytest.raises(ValueError, match="pause_screen_subtext"):
        settings.updated({"pause_screen_subtext": "x" * 161})

    updated = settings.updated(
        {
            "pause_screen_enabled": False,
            "pause_screen_background": "#00ff00",
            "pause_screen_max_minutes": 30,
        }
    )
    assert updated.pause_screen_enabled is False
    assert updated.pause_screen_background == "#00ff00"
    assert updated.pause_screen_max_minutes == 30


def test_relay_delay_is_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    assert settings.updated({"relay_delay_seconds": 0}).relay_delay_seconds == 0
    assert settings.updated({"relay_delay_seconds": 15}).relay_delay_seconds == 15

    with pytest.raises(ValueError, match="relay_delay_seconds"):
        settings.updated({"relay_delay_seconds": 61})
    with pytest.raises(ValueError, match="source_min_rate_percent"):
        settings.updated({"source_min_rate_percent": 101})
    with pytest.raises(ValueError, match="output_stretch_percent"):
        settings.updated({"output_stretch_percent": 51})


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


def test_chat_defaults_are_present(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")
    data = settings.to_public_dict()

    assert data["chat_enabled"] is False
    assert data["chat_source_url"] == ""
    assert data["chat_source_api_key_set"] is False
    assert data["chat_poll_seconds"] == 5
    assert data["chat_delay_seconds"] == -1
    assert data["chat_overlay_max_messages"] == 6
    assert data["chat_overlay_max_age_seconds"] == 25
    assert data["chat_overlay_width"] == 320
    assert data["chat_overlay_position"] == "bottom-left"


def test_chat_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="chat_source_url"):
        settings.updated({"chat_source_url": "ftp://example.invalid/chat"})
    with pytest.raises(ValueError, match="chat_poll_seconds"):
        settings.updated({"chat_poll_seconds": 1})
    with pytest.raises(ValueError, match="chat_delay_seconds"):
        settings.updated({"chat_delay_seconds": 121})
    with pytest.raises(ValueError, match="chat_overlay_max_messages"):
        settings.updated({"chat_overlay_max_messages": 13})
    with pytest.raises(ValueError, match="chat_overlay_max_age_seconds"):
        settings.updated({"chat_overlay_max_age_seconds": 4})
    with pytest.raises(ValueError, match="chat_overlay_font_size"):
        settings.updated({"chat_overlay_font_size": 201})
    with pytest.raises(ValueError, match="chat_overlay_width"):
        settings.updated({"chat_overlay_width": 1001})
    with pytest.raises(ValueError, match="chat_overlay_position"):
        settings.updated({"chat_overlay_position": "middle"})

    updated = settings.updated(
        {
            "chat_enabled": True,
            "chat_source_url": "https://toktracker.cc/api/chat-logs",
            "chat_source_api_key": "secret-key",
            "chat_poll_seconds": 10,
            "chat_overlay_position": "top-right",
        }
    )
    assert updated.chat_enabled is True
    assert updated.chat_source_url == "https://toktracker.cc/api/chat-logs"
    assert updated.chat_source_api_key == "secret-key"
    assert updated.chat_poll_seconds == 10
    assert updated.chat_overlay_width == 320
    assert updated.chat_overlay_position == "top-right"


def test_chat_api_key_is_masked(tmp_path: Path) -> None:
    settings = Settings(
        runtime_file=tmp_path / "settings.yml",
        chat_source_api_key="super-secret-chat-key",
    )

    serialized = json.dumps(settings.to_public_dict())

    assert "super-secret-chat-key" not in serialized
    assert settings.to_public_dict()["chat_source_api_key_set"] is True


def test_clock_defaults_are_present(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")
    data = settings.to_public_dict()

    assert data["clock_enabled"] is False
    assert data["clock_position"] == "top-left"
    assert data["clock_show_seconds"] is False
    assert data["clock_font_size"] == 0
    assert data["clock_font"] == ""
    assert data["version_overlay_enabled"] is True


def test_clock_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="clock_position"):
        settings.updated({"clock_position": "middle"})
    with pytest.raises(ValueError, match="clock_font_size"):
        settings.updated({"clock_font_size": 201})

    updated = settings.updated(
        {
            "clock_enabled": True,
            "clock_position": "top-right",
            "clock_show_seconds": True,
            "clock_font_size": 28,
            "clock_font": "/tmp/font.ttf",
        }
    )
    assert updated.clock_enabled is True
    assert updated.clock_position == "top-right"
    assert updated.clock_show_seconds is True
    assert updated.clock_font_size == 28
    assert updated.clock_font == "/tmp/font.ttf"


def test_text_overlay_defaults_are_present(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")
    data = settings.to_public_dict()

    assert data["text_overlay_enabled"] is False
    assert data["text_overlay_text"] == ""
    assert data["text_overlay_position"] == "bottom"
    assert data["text_overlay_font_size"] == 0
    assert data["text_overlay_font"] == ""
    assert data["text_overlay_speed"] == 100


def test_text_overlay_values_are_validated(tmp_path: Path) -> None:
    settings = Settings(runtime_file=tmp_path / "settings.yml")

    with pytest.raises(ValueError, match="text_overlay_position"):
        settings.updated({"text_overlay_position": "middle"})
    with pytest.raises(ValueError, match="text_overlay_font_size"):
        settings.updated({"text_overlay_font_size": 201})
    with pytest.raises(ValueError, match="text_overlay_speed"):
        settings.updated({"text_overlay_speed": 9})
    with pytest.raises(ValueError, match="text_overlay_text"):
        settings.updated({"text_overlay_text": "x" * 501})

    updated = settings.updated(
        {
            "text_overlay_enabled": True,
            "text_overlay_text": "Heute Abend: Community-Stream",
            "text_overlay_position": "top",
            "text_overlay_font_size": 32,
            "text_overlay_font": "/tmp/font.ttf",
            "text_overlay_speed": 240,
        }
    )
    assert updated.text_overlay_enabled is True
    assert updated.text_overlay_text == "Heute Abend: Community-Stream"
    assert updated.text_overlay_position == "top"
    assert updated.text_overlay_font_size == 32
    assert updated.text_overlay_font == "/tmp/font.ttf"
    assert updated.text_overlay_speed == 240

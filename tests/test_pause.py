from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app import pause
from app.ffmpeg_cmd import build_relay_source_url
from app.pause import build_pause_command, find_pause_font, write_pause_texts
from app.settings import Settings


def _build(settings: Settings) -> tuple[list[str], Path, Path]:
    text_path, subtext_path = write_pause_texts(
        settings, settings.pause_screen_text, settings.pause_screen_subtext
    )
    args = build_pause_command(settings, text_path, subtext_path)
    return args, text_path, subtext_path


def test_pause_command_streams_generated_screen(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    font = tmp_path / "DejaVuSans-Bold.ttf"
    font.write_bytes(b"")
    monkeypatch.setattr(pause, "find_pause_font", lambda explicit="": str(font))

    args, text_path, subtext_path = _build(settings)

    assert "color=c=0x101418:s=1280x720:r=30" in args
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in args
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-ar") + 1] == "44100"
    assert args[args.index("mpegts") - 1] == "-f"
    assert args[-1] == build_relay_source_url(settings)
    assert not any("rtmp://" in arg for arg in args)

    filters = args[args.index("-vf") + 1]
    assert f"textfile='{text_path}'" in filters
    assert f"textfile='{subtext_path}'" in filters
    assert text_path.read_text(encoding="utf-8") == "Stream pausiert"
    assert subtext_path.read_text(encoding="utf-8") == "Gleich geht's weiter"


def test_pause_command_uses_configured_canvas(
    settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(pause, "find_pause_font", lambda explicit="": None)
    portrait = replace(
        settings, output_width=720, output_height=1280, output_fps=25
    )

    args, _, _ = _build(portrait)

    assert "color=c=0x101418:s=720x1280:r=25" in args


def test_pause_command_applies_timestamp_offset(
    settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(pause, "find_pause_font", lambda explicit="": None)
    text_path, subtext_path = write_pause_texts(
        settings, settings.pause_screen_text, settings.pause_screen_subtext
    )

    args = build_pause_command(
        settings, text_path, subtext_path, timestamp_offset=7.25
    )

    assert args[args.index("-output_ts_offset") + 1] == "7.250"
    assert args[-1] == build_relay_source_url(settings)


def test_pause_command_without_font_has_no_drawtext(
    settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(pause, "find_pause_font", lambda explicit="": None)

    args, _, _ = _build(settings)

    assert "-vf" not in args
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in args


def test_empty_texts_skip_drawtext(settings: Settings, monkeypatch) -> None:
    monkeypatch.setattr(pause, "find_pause_font", lambda explicit="": "/font.ttf")
    empty = replace(settings, pause_screen_text="", pause_screen_subtext="")

    args, _, _ = _build(empty)

    assert "-vf" not in args


def test_find_pause_font_uses_explicit_file_first(
    tmp_path: Path, monkeypatch
) -> None:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"")
    monkeypatch.setattr(pause, "FONT_CANDIDATES", ())

    assert find_pause_font(str(font)) == str(font)

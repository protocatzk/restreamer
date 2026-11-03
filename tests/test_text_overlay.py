from __future__ import annotations

from pathlib import Path

from app.settings import Settings
from app.text_overlay import (
    build_text_overlay_filter,
    text_overlay_path,
    write_text_overlay_text,
)


def _settings(tmp_path: Path, **kwargs) -> Settings:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"fake-font")
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "landscape",
        "text_overlay_enabled": True,
        "text_overlay_text": "Hallo Welt",
        "text_overlay_font": str(font),
    }
    values.update(kwargs)
    return Settings(**values)


def test_text_overlay_is_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, text_overlay_enabled=False)

    assert build_text_overlay_filter(settings) is None


def test_text_overlay_requires_text(tmp_path: Path) -> None:
    settings = _settings(tmp_path, text_overlay_text="   ")

    assert build_text_overlay_filter(settings) is None


def test_text_overlay_writes_cleaned_text_file(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, text_overlay_text="Zeile 1\nZeile 2\tmit   Abstand"
    )

    filter_value = build_text_overlay_filter(settings)

    assert filter_value is not None
    assert text_overlay_path(settings).read_text(encoding="utf-8") == (
        "Zeile 1 Zeile 2 mit Abstand"
    )
    assert "textfile=" in filter_value
    assert "expansion=none" in filter_value


def test_text_overlay_scrolls_only_when_text_is_too_wide(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    filter_value = build_text_overlay_filter(settings)

    assert filter_value is not None
    assert "if(lt(text_w\\,w-2*42)\\,42\\," in filter_value
    assert "w-mod(t*100\\,w+text_w+128)" in filter_value


def test_text_overlay_position_and_size(tmp_path: Path) -> None:
    top = _settings(
        tmp_path,
        text_overlay_position="top",
        text_overlay_font_size=36,
        text_overlay_speed=250,
    )
    bottom = _settings(tmp_path, text_overlay_position="bottom")

    top_filter = build_text_overlay_filter(top)
    bottom_filter = build_text_overlay_filter(bottom)

    assert top_filter is not None
    assert "fontsize=36" in top_filter
    assert "t*250" in top_filter
    assert "y=42" in top_filter
    assert bottom_filter is not None
    assert "y=h-text_h-42" in bottom_filter


def test_write_text_overlay_text_returns_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path, text_overlay_text=" Ticker ")

    path = write_text_overlay_text(settings)

    assert path == text_overlay_path(settings)
    assert path.read_text(encoding="utf-8") == "Ticker"

from __future__ import annotations

from pathlib import Path

from app.clock import build_clock_filter
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "landscape",
    }
    values.update(kwargs)
    return Settings(**values)


def _font(tmp_path: Path) -> str:
    path = tmp_path / "font.ttf"
    path.write_bytes(b"fake-font")
    return str(path)


def test_clock_filter_renders_hours_and_minutes(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        clock_enabled=True,
        clock_font=_font(tmp_path),
        clock_position="top-left",
        clock_show_seconds=False,
    )

    filter_value = build_clock_filter(settings)

    assert filter_value is not None
    assert "drawtext=" in filter_value
    assert "%{localtime" in filter_value
    assert "%H" in filter_value
    assert "%M" in filter_value
    assert "%S" not in filter_value
    assert "fontsize=33" in filter_value
    assert "x=42" in filter_value
    assert "y=42" in filter_value


def test_clock_filter_can_show_seconds(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        clock_enabled=True,
        clock_font=_font(tmp_path),
        clock_show_seconds=True,
    )

    filter_value = build_clock_filter(settings)

    assert filter_value is not None
    assert "%S" in filter_value


def test_clock_filter_supports_top_right(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        clock_enabled=True,
        clock_font=_font(tmp_path),
        clock_position="top-right",
    )

    filter_value = build_clock_filter(settings)

    assert filter_value is not None
    assert "x=w-text_w-" in filter_value
    assert "y=42" in filter_value


def test_clock_filter_is_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, clock_font=_font(tmp_path))

    assert build_clock_filter(settings) is None

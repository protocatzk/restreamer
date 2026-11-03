from __future__ import annotations

from pathlib import Path

from app.settings import Settings
from app.version import build_version_filter


def _settings(tmp_path: Path, **kwargs) -> Settings:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"fake-font")
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "landscape",
        "clock_font": str(font),
    }
    values.update(kwargs)
    return Settings(**values)


def test_version_filter_renders_top_right(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    filter_value = build_version_filter(settings)

    assert filter_value is not None
    assert "drawtext=" in filter_value
    assert "text='v0.8.0'" in filter_value
    assert "expansion=none" in filter_value
    assert "fontsize=28" in filter_value
    assert "x=w-text_w-42" in filter_value
    assert "y=42" in filter_value


def test_version_filter_can_be_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, version_overlay_enabled=False)

    assert build_version_filter(settings) is None


def test_version_label_can_be_overridden(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    filter_value = build_version_filter(settings, version="9.9.9")

    assert filter_value is not None
    assert "text='v9.9.9'" in filter_value

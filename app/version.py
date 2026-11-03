from __future__ import annotations

from app import __version__
from app.pause import find_pause_font
from app.settings import Settings

VERSION_DEFAULT_FONT_RATIO = 0.022


def _quote_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def build_version_filter(
    settings: Settings, version: str | None = None
) -> str | None:
    """Render a small version label in the top-right corner."""
    if not settings.version_overlay_enabled:
        return None
    font = find_pause_font(settings.clock_font)
    if font is None:
        return None

    label = f"v{version or __version__}"
    width = settings.output_width
    font_size = max(18, round(width * VERSION_DEFAULT_FONT_RATIO))
    margin = max(16, round(width * 0.033))
    box_border = max(8, round(font_size * 0.4))

    return (
        "drawtext="
        f"fontfile={_quote_filter_value(font)}:"
        f"text={_quote_filter_value(label)}:"
        "expansion=none:"
        "fontcolor=white@0.90:"
        f"fontsize={font_size}:"
        "box=1:boxcolor=black@0.35:"
        f"boxborderw={box_border}:"
        f"x=w-text_w-{margin}:y={margin}"
    )

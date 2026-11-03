from __future__ import annotations

from app.pause import find_pause_font
from app.settings import Settings

CLOCK_DEFAULT_FONT_RATIO = 0.026


def _quote_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _clock_text(show_seconds: bool) -> str:
    if show_seconds:
        return r"%{localtime\:%H\\\:%M\\\:%S}"
    return r"%{localtime\:%H\\\:%M}"


def build_clock_filter(settings: Settings) -> str | None:
    """Build a drawtext filter that renders the current wall-clock time."""
    if not settings.clock_enabled:
        return None
    font = find_pause_font(settings.clock_font)
    if font is None:
        return None

    width = settings.output_width
    font_size = settings.clock_font_size or max(
        18, round(width * CLOCK_DEFAULT_FONT_RATIO)
    )
    margin = max(16, round(width * 0.033))
    box_border = max(10, round(font_size * 0.45))

    position = settings.clock_position
    x = str(margin) if position.endswith("left") else f"w-text_w-{margin}"
    y = str(margin) if position.startswith("top") else f"h-text_h-{margin}"

    return (
        "drawtext="
        f"fontfile={_quote_filter_value(font)}:"
        f"text='{_clock_text(settings.clock_show_seconds)}':"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        "box=1:boxcolor=black@0.45:"
        f"boxborderw={box_border}:"
        f"x={x}:y={y}"
    )

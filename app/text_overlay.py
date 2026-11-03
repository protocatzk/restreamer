from __future__ import annotations

import re
from pathlib import Path

from app.pause import find_pause_font
from app.settings import Settings

TEXT_OVERLAY_FILENAME = "text-overlay.txt"
TEXT_OVERLAY_DEFAULT_FONT_RATIO = 0.035
TEXT_OVERLAY_SCROLL_GAP_RATIO = 0.10

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    """Collapse user text to a single line suitable for a ticker."""
    text = _CONTROL_RE.sub(" ", value or "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _quote_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def text_overlay_path(settings: Settings) -> Path:
    return settings.logs_dir / TEXT_OVERLAY_FILENAME


def write_text_overlay_text(settings: Settings) -> Path:
    """Write the configured ticker text to the file used by drawtext."""
    path = text_overlay_path(settings)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(_clean_text(settings.text_overlay_text), encoding="utf-8")
    return path


def build_text_overlay_filter(settings: Settings) -> str | None:
    """Build a drawtext filter for a static or scrolling text line.

    Short text is drawn at the configured edge. If the rendered text is wider
    than the output, drawtext's own ``text_w`` variable is used to scroll it
    from right to left, so the decision needs no font metrics in Python.
    """
    if not settings.text_overlay_enabled:
        return None
    text = _clean_text(settings.text_overlay_text)
    if not text:
        return None
    font = find_pause_font(settings.text_overlay_font)
    if font is None:
        return None

    try:
        path = write_text_overlay_text(settings)
    except OSError:
        return None

    width = settings.output_width
    font_size = settings.text_overlay_font_size or max(
        20, round(width * TEXT_OVERLAY_DEFAULT_FONT_RATIO)
    )
    margin = max(16, round(width * 0.033))
    box_border = max(8, round(font_size * 0.35))
    gap = max(48, round(width * TEXT_OVERLAY_SCROLL_GAP_RATIO))
    speed = max(10, settings.text_overlay_speed)

    if settings.text_overlay_position == "top":
        y = str(margin)
    else:
        y = f"h-text_h-{margin}"

    x_static = str(margin)
    # Escaped commas keep the expression inside the filtergraph option.
    x_scroll = (
        f"w-mod(t*{speed}\\,w+text_w+{gap})"
    )
    x = (
        f"if(lt(text_w\\,w-2*{margin})\\,{x_static}\\,{x_scroll})"
    )

    return (
        "drawtext="
        f"fontfile={_quote_filter_value(font)}:"
        f"textfile={_quote_filter_value(str(path))}:"
        "expansion=none:"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        "box=1:boxcolor=black@0.55:"
        f"boxborderw={box_border}:"
        f"x='{x}':y={y}"
    )

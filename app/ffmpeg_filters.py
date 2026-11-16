"""Small shared helpers for building ffmpeg filter arguments."""

from __future__ import annotations


def quote_filter_value(value: str) -> str:
    """Quote a value for use inside an ffmpeg filtergraph option."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def scaled_font_size(
    configured: int, base: int, ratio: float, minimum: int
) -> int:
    """Return an explicit font size or a size proportional to ``base``."""
    if configured > 0:
        return configured
    return max(minimum, round(base * ratio))

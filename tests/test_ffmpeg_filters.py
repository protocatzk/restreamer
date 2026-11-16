from __future__ import annotations

from app.ffmpeg_filters import quote_filter_value, scaled_font_size


def test_quote_filter_value_escapes_backslash_and_quote() -> None:
    assert quote_filter_value("a'b\\c") == "'a\\'b\\\\c'"


def test_scaled_font_size_prefers_explicit_value() -> None:
    assert scaled_font_size(30, 720, 0.05, 18) == 30


def test_scaled_font_size_scales_with_base() -> None:
    assert scaled_font_size(0, 720, 0.05, 18) == 36
    assert scaled_font_size(0, 100, 0.05, 18) == 18

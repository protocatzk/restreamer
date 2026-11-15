from __future__ import annotations

import pytest

from app.overlay_text import (
    EmojiRenderer,
    RichText,
    find_bold_font,
    find_emoji_font,
    load_font_set,
)
from app.pause import find_pause_font


def _rich() -> RichText:
    path = find_pause_font()
    if path is None:
        pytest.skip("Keine DejaVu-Schrift im Testsystem")
    fonts = load_font_set(path, 28)
    assert fonts is not None
    return RichText(fonts, EmojiRenderer(find_emoji_font(), True))


def test_layout_wraps_to_width_and_limits_lines() -> None:
    rich = _rich()

    lines = rich.layout("Hallo Welt " * 20, 180, 3)

    assert 1 <= len(lines) <= 3
    for line in lines:
        assert line.width <= 180


def test_layout_adds_ellipsis_on_overflow() -> None:
    rich = _rich()

    lines = rich.layout("ein sehr langer Text " * 30, 120, 2)

    assert len(lines) == 2
    assert lines[-1].runs[-1].text.endswith("…")


def test_layout_keeps_emoji_cluster_atomic() -> None:
    rich = _rich()

    lines = rich.layout("Hallo 👨‍👩‍👧‍👦 Welt", 400, 1)

    assert len(lines) == 1
    assert any(run.kind == "emoji" for run in lines[0].runs)


def test_bold_variant_is_used_when_available() -> None:
    path = find_pause_font()
    if path is None:
        pytest.skip("Keine DejaVu-Schrift im Testsystem")

    bold = find_bold_font(path)

    assert bold.endswith((".ttf", ".otf"))


def test_color_emoji_renders_colored_pixels() -> None:
    path = find_emoji_font()
    if path is None:
        pytest.skip("Noto Color Emoji nicht installiert")

    renderer = EmojiRenderer(path, True)
    image = renderer.image("😀", 32)

    assert image is not None
    assert image.height == 32
    colors = image.getcolors(maxcolors=100_000) or []
    assert any(
        alpha > 0 and len({red, green, blue}) > 1
        for _, (red, green, blue, alpha) in colors
    )


def test_emoji_can_be_disabled() -> None:
    path = find_emoji_font()
    if path is None:
        pytest.skip("Noto Color Emoji nicht installiert")

    renderer = EmojiRenderer(path, False)

    assert renderer.available is False
    assert renderer.image("😀", 32) is None

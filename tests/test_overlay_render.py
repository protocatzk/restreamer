from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.chat import ChatMessage
from app.overlay_render import OverlayRenderer, OverlaySnapshot, _name_color
from app.pause import find_pause_font
from app.settings import Settings


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {
        "logs_dir": tmp_path,
        "output_orientation": "portrait",
        "chat_enabled": True,
        "chat_overlay_style": "bubbles",
        "chat_overlay_font_size": 26,
        "chat_overlay_width": 600,
        "chat_overlay_max_messages": 6,
        "chat_overlay_max_age_seconds": 25,
        "overlay_emoji_enabled": False,
        "version_overlay_enabled": False,
        "earnings_overlay_enabled": False,
    }
    values.update(kwargs)
    return Settings(**values)


def _message(text: str = "Hallo Welt!", username: str = "alice") -> ChatMessage:
    return ChatMessage(
        cursor="1",
        username=username,
        text=text,
        nickname="Alice",
        created_at=datetime(2026, 11, 9, 20, 15),
    )


def _requires_font() -> None:
    if find_pause_font() is None:
        pytest.skip("Keine DejaVu-Schrift im Testsystem")


def test_render_produces_transparent_canvas_with_chat(tmp_path: Path) -> None:
    _requires_font()
    renderer = OverlayRenderer(_settings(tmp_path))

    image = renderer.render_image(
        OverlaySnapshot(chat=(_message(),), now=datetime(2026, 11, 9, 20, 15))
    )

    assert image.size == (720, 1280)
    assert image.mode == "RGBA"
    bbox = image.getbbox()
    assert bbox is not None
    # bottom-left chat: the visible content must be in the lower half
    assert bbox[1] > 1280 // 2


def test_render_png_has_signature(tmp_path: Path) -> None:
    _requires_font()
    renderer = OverlayRenderer(_settings(tmp_path))

    png = renderer.render_png(OverlaySnapshot(chat=(_message(),)))

    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_style_changes_output(tmp_path: Path) -> None:
    _requires_font()
    snapshot = OverlaySnapshot(chat=(_message(),))
    bubbles = OverlayRenderer(
        _settings(tmp_path, chat_overlay_style="bubbles")
    ).render_png(snapshot)
    minimal = OverlayRenderer(
        _settings(tmp_path, chat_overlay_style="minimal")
    ).render_png(snapshot)

    assert bubbles != minimal


def test_name_colors_are_stable(tmp_path: Path) -> None:
    assert _name_color("Alice") == _name_color("alice")
    assert _name_color("alice") != _name_color("bob")


def test_clock_version_and_earnings_are_drawn(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(
        tmp_path,
        clock_enabled=True,
        version_overlay_enabled=True,
        earnings_overlay_enabled=True,
        earnings_overlay_template="{period}: {net}",
    )
    renderer = OverlayRenderer(settings)

    image = renderer.render_image(
        OverlaySnapshot(
            earnings="Heute: 12,34 €",
            now=datetime(2026, 11, 9, 20, 15, 42),
        )
    )

    assert image.getbbox() is not None
    # top-right version badge
    top_right = image.crop((500, 0, 720, 120))
    assert top_right.getbbox() is not None


def test_ticker_is_animated_and_moves(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(
        tmp_path,
        text_overlay_enabled=True,
        text_overlay_text="Ein sehr langer Laufschrift-Text " * 4,
        text_overlay_position="bottom",
        output_orientation="landscape",
        overlay_engine_enabled=True,
    )
    renderer = OverlayRenderer(settings)
    first = renderer.render_png(
        OverlaySnapshot(elapsed=0.0, animate=True)
    )
    second = renderer.render_png(
        OverlaySnapshot(elapsed=1.0, animate=True)
    )

    assert renderer.text_is_animated(OverlaySnapshot(elapsed=0.0)) is True
    assert first != second


def test_short_text_overlay_is_static(tmp_path: Path) -> None:
    _requires_font()
    settings = _settings(
        tmp_path,
        text_overlay_enabled=True,
        text_overlay_text="Kurz",
    )
    renderer = OverlayRenderer(settings)

    assert renderer.text_is_animated(OverlaySnapshot()) is False


def test_color_emoji_is_rendered(tmp_path: Path) -> None:
    _requires_font()
    from app.overlay_text import find_emoji_font

    if find_emoji_font() is None:
        pytest.skip("Noto Color Emoji nicht installiert")
    settings = _settings(
        tmp_path,
        overlay_emoji_enabled=True,
        chat_overlay_name_colors=False,
        chat_overlay_style="minimal",
        chat_overlay_width=400,
    )
    renderer = OverlayRenderer(settings)

    image = renderer.render_image(
        OverlaySnapshot(chat=(_message(text="😀"),))
    )
    colors = image.getcolors(maxcolors=2_000_000) or []
    assert any(
        alpha > 0 and max(red, green, blue) - min(red, green, blue) > 25
        for _, (red, green, blue, alpha) in colors
    )

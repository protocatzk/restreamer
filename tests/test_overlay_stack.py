from __future__ import annotations

from dataclasses import replace

from app import overlay_stack
from app.overlay_stack import OverlayStack


def test_missing_chat_asset_warns_only_once_until_reset(
    settings, monkeypatch
) -> None:
    settings = replace(settings, chat_enabled=True)
    messages: list[str] = []
    monkeypatch.setattr(
        overlay_stack, "build_overlay_filter", lambda _settings: None
    )
    stack = OverlayStack(settings, messages.append)

    stack.legacy_filters()
    stack.legacy_filters()

    assert sum("Chat-Overlay deaktiviert" in line for line in messages) == 1

    stack.reset_warnings()
    stack.legacy_filters()

    assert sum("Chat-Overlay deaktiviert" in line for line in messages) == 2


def test_chat_filter_is_skipped_on_request(settings) -> None:
    font = settings.logs_dir / "font.ttf"
    font.write_bytes(b"fake-font")
    settings = replace(
        settings, chat_enabled=True, chat_overlay_font=str(font)
    )
    stack = OverlayStack(settings, lambda _line: None)

    without_chat = stack.legacy_filters(include_chat=False)

    assert not any("textfile=" in item for item in without_chat)


def test_pipeline_is_disabled_by_engine_flag(settings) -> None:
    settings = replace(
        settings, chat_enabled=True, chat_overlay_engine_enabled=False
    )
    stack = OverlayStack(settings, lambda _line: None)

    assert (
        stack.start_pipeline(
            chat=object(), earnings_text=None, words=lambda: []
        )
        is None
    )

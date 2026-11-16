"""Overlay selection and the PNG chat pipeline.

A running output ffmpeg process needs a flat list of ``-vf`` fragments.  The
ordering, the one-time warnings for missing fonts, and the optional PNG chat
pipeline were previously entangled with the supervisor's process lifecycle.
Keeping them here makes the filter stack easy to test without spawning any
process.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from types import ModuleType

from app.chat import ChatMessage, ChatStats, ChatWorker, build_overlay_filter
from app.clock import build_clock_filter
from app.earnings import build_overlay_filter as build_earnings_overlay_filter
from app.settings import Settings
from app.text_overlay import build_text_overlay_filter
from app.version import build_version_filter
from app.words import WordCount

logger = logging.getLogger(__name__)

_chat_engine_module: ModuleType | None
try:  # Pillow is optional; the drawtext overlay remains the fallback.
    from app import chat_engine as _chat_engine_module
except Exception as _chat_engine_exc:  # pragma: no cover - dependency guard
    _chat_engine_module = None
    _chat_engine_import_error = str(_chat_engine_exc)
else:
    _chat_engine_import_error = ""

Record = Callable[[str], None]


class OverlayStack:
    """Collect overlay filters and warn once per missing asset."""

    def __init__(self, settings: Settings, record: Record) -> None:
        self.settings = settings
        self._record = record
        self._warned: set[str] = set()

    def reset_warnings(self) -> None:
        self._warned.clear()

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self._record(message)

    def legacy_filters(self, *, include_chat: bool = True) -> list[str]:
        """Build the drawtext filters used outside the PNG chat engine."""
        video_filters: list[str] = []
        if include_chat:
            chat_filter = build_overlay_filter(self.settings)
            if chat_filter is not None:
                video_filters.append(chat_filter)
            elif self.settings.chat_enabled:
                self._warn_once(
                    "chat",
                    "[Chat] Keine Schrift oder Overlay-Datei gefunden – "
                    "Chat-Overlay deaktiviert",
                )

        earnings_filter = build_earnings_overlay_filter(self.settings)
        if earnings_filter is not None:
            video_filters.append(earnings_filter)
        elif self.settings.earnings_overlay_enabled:
            self._warn_once(
                "earnings",
                "[Einnahmen] Keine Schrift oder Overlay-Datei gefunden – "
                "Einnahmen-Overlay deaktiviert",
            )

        clock_filter = build_clock_filter(self.settings)
        if clock_filter is not None:
            video_filters.append(clock_filter)
        elif self.settings.clock_enabled:
            self._warn_once(
                "clock", "[Clock] Keine Schrift gefunden – Uhrzeit deaktiviert"
            )

        text_filter = build_text_overlay_filter(self.settings)
        if text_filter is not None:
            video_filters.append(text_filter)
        elif self.settings.text_overlay_enabled:
            self._warn_once(
                "text",
                "[Text] Keine Schrift oder Textdatei gefunden – "
                "Text-Overlay deaktiviert",
            )

        version_filter = build_version_filter(self.settings)
        if version_filter is not None:
            video_filters.append(version_filter)
        elif self.settings.version_overlay_enabled:
            self._warn_once(
                "version",
                "[Version] Keine Schrift gefunden – "
                "Versionsanzeige deaktiviert",
            )
        return video_filters

    def start_pipeline(
        self,
        *,
        chat: ChatWorker | None,
        earnings_text: Callable[[], str] | None,
        words: Callable[[], Iterable[WordCount]],
    ):
        """Create the PNG chat FIFO for the next output process."""
        if not self.settings.chat_overlay_engine_enabled:
            return None
        if not self.settings.chat_enabled or chat is None:
            return None
        if _chat_engine_module is None:
            detail = (
                f": {_chat_engine_import_error}"
                if _chat_engine_import_error
                else ""
            )
            self._warn_once(
                "engine",
                "[Chat] PNG-Engine nicht verfügbar"
                + detail
                + " – drawtext-Fallback",
            )
            return None
        try:
            pipe = _chat_engine_module.ChatPipeline(
                _chat_engine_module.ChatOverlaySources(
                    self.settings,
                    messages=chat.overlay.visible_messages,
                    stats=chat.overlay.stats,
                    earnings=earnings_text,
                    words=words,
                ),
                log=self._record,
            )
            pipe.prepare()
        except Exception as exc:  # noqa: BLE001 - fallback to drawtext
            self._warn_once(
                "engine",
                f"[Chat] PNG-Engine nicht startbar: {exc} – "
                "drawtext-Fallback",
            )
            return None
        self._record(
            "[Chat] PNG-Engine aktiv "
            f"({pipe.geometry.width}x{pipe.geometry.height} "
            f"@ {pipe.fps} fps)"
        )
        return pipe


def preview_png(
    settings: Settings,
    messages: tuple[ChatMessage, ...],
    stats: ChatStats | None,
    earnings: str,
    words: tuple[WordCount, ...],
    *,
    sample: bool = False,
) -> bytes:
    """Render the current (or sample) chat overlay for the dashboard."""
    if _chat_engine_module is None:
        detail = (
            f": {_chat_engine_import_error}"
            if _chat_engine_import_error
            else ""
        )
        raise RuntimeError(f"PNG-Chat-Engine nicht verfügbar{detail}")
    return _chat_engine_module.preview_png(
        settings, messages, stats, earnings, words, sample=sample
    )

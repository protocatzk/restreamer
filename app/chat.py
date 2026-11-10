from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.handles import normalize_handle
from app.pause import find_pause_font
from app.settings import Settings

logger = logging.getLogger(__name__)

CHAT_OVERLAY_FILENAME = "chat-overlay.txt"
CHAT_REQUEST_TIMEOUT_SECONDS = 8.0
CHAT_BATCH_LIMIT = 200
CHAT_BOOTSTRAP_LIMIT = 100
CHAT_BUFFER_LIMIT = 250
CHAT_RENDER_INTERVAL_SECONDS = 0.5
CHAT_ERROR_LOG_INTERVAL_SECONDS = 60.0
CHAT_DEFAULT_FONT_RATIO = 0.042
CHAT_WRAP_CHAR_RATIO = 0.60
CHAT_MAX_NAME_LENGTH = 28
CHAT_MAX_MESSAGE_LINES = 3

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

JsonFetcher = Callable[[str, dict[str, str], float], object]


def _clean_text(value: str) -> str:
    text = _CONTROL_RE.sub(" ", value or "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _first_value(item: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_str(item: dict[str, object], *keys: str) -> str:
    value = _first_value(item, *keys)
    return "" if value is None else str(value).strip()


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class ChatMessage:
    cursor: str
    username: str
    text: str
    nickname: str = ""
    streamer: str = ""
    created_at: datetime | None = None
    is_whale: bool = False

    @property
    def display_name(self) -> str:
        # Prefer the unique handle over the (non-unique) display name so the
        # overlay always shows who actually wrote the message.
        username = _clean_text(self.username)
        if username:
            return f"@{username}"
        return _clean_text(self.nickname) or "?"


class ChatSource(Protocol):
    def fetch(
        self,
        *,
        cursor: str | None,
        limit: int = CHAT_BATCH_LIMIT,
        descending: bool = False,
    ) -> list[ChatMessage]:
        """Return messages newer than ``cursor`` (or the newest page)."""


def parse_messages(payload: object) -> list[ChatMessage]:
    """Parse a chat source response into messages.

    The contract is intentionally small: a list (or an object containing a
    ``messages``/``data``/``items``/``results`` list) of objects with at least
    a text/comment field. TokTracker's ``/api/chat-logs`` response fits this
    shape without any adapter code.
    """
    items: object = payload
    if isinstance(payload, dict):
        for key in ("messages", "data", "items", "results"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        else:
            items = []
    if not isinstance(items, list):
        return []

    messages: list[ChatMessage] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = _clean_text(
            _first_str(item, "text", "comment", "content", "message")
        )
        if not text:
            continue
        cursor_value = _first_value(item, "id", "message_id", "cursor")
        cursor = (
            str(cursor_value) if cursor_value is not None else f"idx-{index}"
        )
        messages.append(
            ChatMessage(
                cursor=cursor,
                username=_first_str(item, "username", "user", "unique_id"),
                text=text,
                nickname=_first_str(
                    item, "nickname", "display_name", "displayName", "name"
                ),
                streamer=_first_str(
                    item, "streamer_username", "streamer", "channel"
                ),
                created_at=_parse_datetime(
                    _first_value(item, "created_at", "timestamp", "ts", "time")
                ),
                is_whale=bool(_first_value(item, "is_whale", "isWhale") or False),
            )
        )
    return messages


def _cursor_key(cursor: str) -> tuple[int, int | str]:
    try:
        return (0, int(cursor))
    except (TypeError, ValueError):
        return (1, cursor)


def max_cursor(
    messages: Iterable[ChatMessage], fallback: str | None = None
) -> str | None:
    best = fallback
    for message in messages:
        if best is None or _cursor_key(message.cursor) > _cursor_key(best):
            best = message.cursor
    return best


def _append_query(url: str, params: dict[str, str]) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.extend(params.items())
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> object:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


class HttpChatSource:
    """Generic polling source for a small JSON chat feed."""

    def __init__(
        self, settings: Settings, fetcher: JsonFetcher | None = None
    ) -> None:
        self.settings = settings
        self._fetcher = fetcher or _fetch_json

    def fetch(
        self,
        *,
        cursor: str | None,
        limit: int = CHAT_BATCH_LIMIT,
        descending: bool = False,
    ) -> list[ChatMessage]:
        url = self.settings.chat_source_url.strip()
        if not url:
            return []
        params = {
            "limit": str(limit),
            "order_dir": "DESC" if descending else "ASC",
        }
        if cursor is not None:
            params["after_id"] = cursor
        headers = {
            "Accept": "application/json",
            "User-Agent": "restreamer-chat-source/1",
        }
        api_key = self.settings.chat_source_api_key.strip()
        if api_key:
            headers["X-API-Key"] = api_key
        payload = self._fetcher(
            _append_query(url, params), headers, CHAT_REQUEST_TIMEOUT_SECONDS
        )
        return parse_messages(payload)


def chat_overlay_path(settings: Settings) -> Path:
    return settings.logs_dir / CHAT_OVERLAY_FILENAME


def ensure_chat_overlay_file(settings: Settings) -> Path:
    path = chat_overlay_path(settings)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def _quote_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _chat_margin(settings: Settings) -> int:
    return max(16, round(settings.output_width * 0.033))


def _chat_font_size(settings: Settings) -> int:
    if settings.chat_overlay_font_size > 0:
        return settings.chat_overlay_font_size
    auto = max(22, round(settings.output_width * CHAT_DEFAULT_FONT_RATIO))
    if settings.chat_overlay_width > 0:
        auto = min(auto, max(16, round(settings.chat_overlay_width / 12)))
    return auto


def _chat_max_width(settings: Settings) -> int:
    available = max(120, settings.output_width - 2 * _chat_margin(settings))
    configured = settings.chat_overlay_width
    return min(configured, available) if configured > 0 else available


def chat_margin(settings: Settings) -> int:
    """Public helper used by the image overlay engine."""
    return _chat_margin(settings)


def chat_font_size(settings: Settings) -> int:
    """Public helper used by the image overlay engine."""
    return _chat_font_size(settings)


def chat_max_width(settings: Settings) -> int:
    """Public helper used by the image overlay engine."""
    return _chat_max_width(settings)


def build_overlay_filter(settings: Settings) -> str | None:
    """Build the ffmpeg drawtext filter for the active chat overlay."""
    if not settings.chat_enabled:
        return None
    path = chat_overlay_path(settings)
    if not path.is_file():
        return None
    font = find_pause_font(settings.chat_overlay_font)
    if font is None:
        return None

    font_size = _chat_font_size(settings)
    line_spacing = max(4, font_size // 4)
    margin = _chat_margin(settings)
    box_border = max(10, round(font_size * 0.55))

    position = settings.chat_overlay_position
    x = str(margin) if position.endswith("left") else f"w-text_w-{margin}"
    y = str(margin) if position.startswith("top") else f"h-text_h-{margin}"

    return (
        "drawtext="
        f"fontfile={_quote_filter_value(font)}:"
        f"textfile={_quote_filter_value(str(path))}:"
        "expansion=none:reload=1:"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        f"line_spacing={line_spacing}:"
        "box=1:boxcolor=black@0.45:"
        f"boxborderw={box_border}:"
        f"x={x}:y={y}"
    )


@dataclass
class _PendingMessage:
    message: ChatMessage
    show_at: float
    expire_at: float


class ChatOverlay:
    """Buffer chat messages and write the currently visible block to disk."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = chat_overlay_path(settings)
        try:
            ensure_chat_overlay_file(settings)
        except OSError as exc:
            logger.warning("Chat-Overlay-Datei nicht anlegbar: %s", exc)
        self._pending: list[_PendingMessage] = []
        self._clear_at: float | None = None
        self._clock_offset: float | None = None
        self._last_text = ""
        self._last_write_error = 0.0

    def reset_clock(self) -> None:
        """Forget the source clock offset, e.g. after changing the source URL."""
        self._clock_offset = None

    def _delay(self) -> float:
        value = self.settings.chat_delay_seconds
        if value < 0:
            value = self.settings.relay_delay_seconds
        return max(0.0, float(value))

    def enqueue(
        self,
        messages: Iterable[ChatMessage],
        *,
        reference: Iterable[ChatMessage] | None = None,
        now_mono: float | None = None,
        now_wall: datetime | None = None,
    ) -> None:
        messages = list(messages)
        if not messages:
            return
        reference_messages = list(reference) if reference is not None else messages
        now_mono = time.monotonic() if now_mono is None else now_mono
        now_wall = datetime.now() if now_wall is None else now_wall
        max_age = max(5, self.settings.chat_overlay_max_age_seconds)
        delay = self._delay()
        max_lead = delay + max(5, self.settings.chat_poll_seconds * 3)

        timestamps = [
            message.created_at
            for message in reference_messages
            if message.created_at is not None
        ]
        if timestamps:
            newest = max(timestamps)
            observed_offset = (now_wall - newest).total_seconds()
            if self._clock_offset is None:
                self._clock_offset = observed_offset
            elif observed_offset <= self._clock_offset + max(
                5, self.settings.chat_poll_seconds * 2
            ):
                # A fresh batch: keep the smallest observed lag.
                self._clock_offset = min(self._clock_offset, observed_offset)

        existing = {item.message.cursor for item in self._pending}
        for message in messages:
            if message.cursor in existing:
                continue
            if message.created_at is not None and self._clock_offset is not None:
                target = message.created_at + timedelta(
                    seconds=self._clock_offset + delay
                )
                show_at = now_mono + (target - now_wall).total_seconds()
            else:
                show_at = now_mono + delay
            if show_at < now_mono - max_age:
                continue
            show_at = max(show_at, now_mono)
            show_at = min(show_at, now_mono + max_lead)
            self._pending.append(
                _PendingMessage(
                    message=message,
                    show_at=show_at,
                    expire_at=show_at + max_age,
                )
            )
            existing.add(message.cursor)
        if len(self._pending) > CHAT_BUFFER_LIMIT:
            self._pending.sort(key=lambda item: item.show_at)
            self._pending = self._pending[-CHAT_BUFFER_LIMIT:]

    def clear_in(self, seconds: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        target = now + max(0.0, seconds)
        if self._clear_at is None or target > self._clear_at:
            self._clear_at = target

    def clear(self) -> None:
        self._pending.clear()
        self._clear_at = None
        self._write("")

    def visible_messages(self, now: float | None = None) -> list[ChatMessage]:
        """Return the currently visible messages, oldest first.

        The image overlay engine uses this directly; the legacy drawtext
        overlay formats the same list into its text file.
        """
        now = time.monotonic() if now is None else now
        if self._clear_at is not None and now >= self._clear_at:
            clear_at = self._clear_at
            self._pending = [
                item for item in self._pending if item.show_at > clear_at
            ]
            self._clear_at = None
        visible = [
            item for item in self._pending if item.show_at <= now < item.expire_at
        ]
        self._pending = [
            item for item in self._pending if item.expire_at > now
        ]
        visible.sort(key=lambda item: item.show_at)
        limit = max(1, self.settings.chat_overlay_max_messages)
        return [item.message for item in visible[-limit:]]

    def render(self, now: float | None = None) -> None:
        text = self._format(self.visible_messages(now))
        if text != self._last_text:
            self._write(text)

    def _format(self, messages: list[ChatMessage]) -> str:
        if not messages:
            return ""
        font_size = _chat_font_size(self.settings)
        max_chars = max(
            10,
            int(
                _chat_max_width(self.settings)
                / max(1.0, font_size * CHAT_WRAP_CHAR_RATIO)
            ),
        )
        lines: list[str] = []
        for message in messages:
            body = _clean_text(message.text)
            if not body:
                continue
            name = message.display_name[:CHAT_MAX_NAME_LENGTH].strip()
            prefix = f"{name}: " if name else ""
            wrapped = textwrap.wrap(
                prefix + body,
                width=max_chars,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [prefix + body]
            if len(wrapped) > CHAT_MAX_MESSAGE_LINES:
                wrapped = wrapped[:CHAT_MAX_MESSAGE_LINES]
                wrapped[-1] = wrapped[-1].rstrip() + "…"
            lines.extend(wrapped)
        return "\n".join(lines).strip()

    def _write(self, text: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
            self._last_text = text
        except OSError as exc:
            now = time.monotonic()
            if now - self._last_write_error >= CHAT_ERROR_LOG_INTERVAL_SECONDS:
                self._last_write_error = now
                logger.warning(
                    "Chat-Overlay konnte nicht geschrieben werden: %s", exc
                )


def _redact(value: str, secret: str = "") -> str:
    if secret:
        value = value.replace(secret, "***")
    return re.sub(r"(api_key=)[^&\s]+", r"\1***", value)


def _safe_source(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class ChatWorker:
    """Poll a chat source and keep the overlay text file up to date."""

    def __init__(
        self,
        settings: Settings,
        *,
        overlay: ChatOverlay | None = None,
        source: ChatSource | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.overlay = overlay or ChatOverlay(settings)
        self.source = source or HttpChatSource(settings)
        self._log = log or logger.info
        self._streamer = ""
        self._cursor: str | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._next_poll_at = 0.0
        self._backoff = 0.0
        self._connected = False
        self._last_error = ""
        self._last_message_at: str | None = None
        self._message_count = 0
        self._last_error_log = 0.0
        self._disabled_cleared = False
        self._signature: tuple[str, str] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="chat-source")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            self._stop_event.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.overlay.clear()
        self._connected = False

    def set_streamer(self, handle: str) -> None:
        normalized = normalize_handle(handle)
        if normalized == self._streamer:
            return
        if self._streamer:
            self.overlay.clear_in(self._delay())
        else:
            self.overlay.clear()
        self._streamer = normalized
        self._next_poll_at = 0.0

    def pause(self) -> None:
        self.overlay.clear_in(self._delay())

    def status(self) -> dict:
        return {
            "enabled": bool(self.settings.chat_enabled),
            "streamer": self._streamer or None,
            "connected": self._connected,
            "messages": self._message_count,
            "last_message_at": self._last_message_at,
            "error": self._last_error,
            "source": _safe_source(self.settings.chat_source_url),
        }

    def _delay(self) -> float:
        value = self.settings.chat_delay_seconds
        if value < 0:
            value = self.settings.relay_delay_seconds
        return max(0.0, float(value))

    def _enabled(self) -> bool:
        return bool(
            self.settings.chat_enabled
            and self.settings.chat_source_url.strip()
            and self._streamer
        )

    def _signature_value(self) -> tuple[str, str]:
        return (
            self.settings.chat_source_url.strip(),
            self.settings.chat_source_api_key.strip(),
        )

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must stay alive
                logger.exception("Chat-Worker Fehler")
                await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        self.overlay.render()
        if self._enabled():
            self._disabled_cleared = False
            signature = self._signature_value()
            if signature != self._signature:
                self._signature = signature
                self._cursor = None
                self._next_poll_at = 0.0
                self.overlay.reset_clock()
                self.overlay.clear()
            if time.monotonic() >= self._next_poll_at:
                await self._poll()
        elif not self._disabled_cleared:
            self.overlay.clear()
            self._disabled_cleared = True
            self._connected = False
        await asyncio.sleep(CHAT_RENDER_INTERVAL_SECONDS)

    async def _poll(self) -> None:
        try:
            if self._cursor is None:
                messages = await asyncio.to_thread(
                    self.source.fetch,
                    cursor=None,
                    limit=CHAT_BOOTSTRAP_LIMIT,
                    descending=True,
                )
                self._accept(messages)
                self._cursor = max_cursor(messages)
            else:
                for _ in range(5):
                    messages = await asyncio.to_thread(
                        self.source.fetch,
                        cursor=self._cursor,
                        limit=CHAT_BATCH_LIMIT,
                    )
                    if not messages:
                        break
                    self._cursor = max_cursor(messages, self._cursor)
                    self._accept(messages)
                    if len(messages) < CHAT_BATCH_LIMIT:
                        break
            was_connected = self._connected
            self._connected = True
            self._last_error = ""
            self._backoff = 0.0
            if not was_connected:
                self._log("[Chat] Chatquelle verbunden")
        except Exception as exc:  # noqa: BLE001 - chat must never break the stream
            self._connected = False
            self._last_error = _redact(
                str(exc) or type(exc).__name__,
                self.settings.chat_source_api_key,
            )
            self._backoff = min(
                max(5.0, self._backoff * 2.0 if self._backoff else 5.0),
                60.0,
            )
            self._log_throttled(
                f"[Chat] Chatquelle nicht erreichbar: {self._last_error}"
            )
        finally:
            interval = max(1, self.settings.chat_poll_seconds)
            self._next_poll_at = time.monotonic() + max(
                float(interval), self._backoff
            )

    def _accept(self, messages: list[ChatMessage]) -> None:
        selected = self._filter(messages)
        if not selected:
            return
        self.overlay.enqueue(selected, reference=messages)
        self._message_count += len(selected)
        self._last_message_at = datetime.now().isoformat(timespec="seconds")

    def _filter(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        if not self._streamer:
            return messages
        selected: list[ChatMessage] = []
        for message in messages:
            if not message.streamer:
                selected.append(message)
            elif normalize_handle(message.streamer) == self._streamer:
                selected.append(message)
        return selected

    def _log_throttled(self, line: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log < CHAT_ERROR_LOG_INTERVAL_SECONDS:
            return
        self._last_error_log = now
        self._log(line)

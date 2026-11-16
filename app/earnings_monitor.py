"""HTTP polling and state for the earnings monitor."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Protocol

from app.earnings_overlay import (
    earnings_overlay_path,
    ensure_earnings_overlay_file,
    render_overlay_text,
)
from app.earnings_parse import EARNINGS_PERIOD_LABELS, EarningsEntry, parse_earnings
from app.handles import normalize_handle
from app.models import StreamerConfig
from app.polling import PollingWorker
from app.redaction import redact_value as _redact
from app.settings import Settings
from app.store import StreamerStore
from app.timing import utc_now_iso as _utc_now_iso

logger = logging.getLogger(__name__)

EARNINGS_RENDER_INTERVAL_SECONDS = 1.0
EARNINGS_ERROR_LOG_INTERVAL_SECONDS = 120.0
EARNINGS_MIN_WAIT_SECONDS = 30

JsonFetcher = Callable[[str, dict[str, str], float], object]

def _safe_source(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> object:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _prepare_url(url: str, username: str, period: str) -> str:
    handle = normalize_handle(username)
    placeholder = "{username}" in url or "{streamer}" in url
    if placeholder:
        if not handle:
            return ""
        encoded = urllib.parse.quote(handle, safe="")
        url = url.replace("{username}", encoded).replace("{streamer}", encoded)

    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "period" for key, _ in query):
        query.append(("period", period))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


class EarningsSource(Protocol):
    def fetch(self, username: str = "") -> dict[str, EarningsEntry]:
        """Return current earnings keyed by normalized TikTok handle."""


class HttpEarningsSource:
    """Generic HTTP/JSON source for TokTracker-compatible earnings."""

    def __init__(
        self, settings: Settings, fetcher: JsonFetcher | None = None
    ) -> None:
        self.settings = settings
        self._fetcher = fetcher or _fetch_json

    def fetch(self, username: str = "") -> dict[str, EarningsEntry]:
        url = self.settings.earnings_source_url.strip()
        if not url:
            return {}
        url = _prepare_url(url, username, self.settings.earnings_period)
        if not url:
            return {}

        headers = {
            "Accept": "application/json",
            "User-Agent": "restreamer-earnings-source/1",
        }
        api_key = self.settings.earnings_source_api_key.strip()
        if api_key:
            headers["X-API-Key"] = api_key
        timeout = max(1.0, float(min(self.settings.earnings_timeout_seconds, 120)))
        payload = self._fetcher(url, headers, timeout)
        return parse_earnings(payload)
def _entry_view(entry: EarningsEntry) -> dict:
    currency = entry.currency
    session_currency = entry.session_currency
    return {
        "username": entry.username,
        "display_name": entry.display_name,
        "avatar_url": entry.avatar_url,
        "room_id": entry.room_id,
        "is_live": entry.is_live,
        "follower_count": entry.follower_count,
        "current_viewers": entry.current_viewers,
        "last_seen_at": entry.last_seen_at,
        "coins": entry.coins,
        "currency": currency,
        "net": entry.net,
        "gross": entry.gross,
        "usd_net": entry.usd_net,
        "eur_net": entry.eur_net,
        "usd_gross": entry.usd_gross,
        "eur_gross": entry.eur_gross,
        "gift_events": entry.gift_events,
        "gifts_sent": entry.gifts_sent,
        "unique_gifters": entry.unique_gifters,
        "session_coins": entry.session_coins,
        "session_currency": session_currency,
        "session_net": entry.session_net,
        "session_gross": entry.session_gross,
        "session_gifts": entry.session_gifts,
        "session_info": entry.session_info,
        "top_gift": entry.top_gift,
        "top_gifter": entry.top_gifter,
        "diagnostics": entry.diagnostics,
        "rates": entry.rates,
        "period": entry.period,
    }


def default_summary(settings: Settings) -> dict:
    configured = bool(settings.earnings_source_url.strip())
    enabled = bool(settings.earnings_enabled and configured)
    if not configured:
        status = "unconfigured"
    elif not settings.earnings_enabled:
        status = "disabled"
    else:
        status = "inactive"
    return {
        "enabled": enabled,
        "configured": configured,
        "source": "",
        "period": settings.earnings_period,
        "period_label": EARNINGS_PERIOD_LABELS.get(
            settings.earnings_period, settings.earnings_period
        ),
        "interval_seconds": int(settings.earnings_refresh_seconds),
        "timeout_seconds": int(settings.earnings_timeout_seconds),
        "overlay_enabled": bool(settings.earnings_overlay_enabled),
        "overlay_position": settings.earnings_overlay_position,
        "overlay_font_size": int(settings.earnings_overlay_font_size),
        "view": settings.earnings_view,
        "rates": None,
        "last_run_at": None,
        "checking": False,
        "status": status,
        "active_streamer": None,
        "data": None,
        "error": "",
        "missing": False,
    }
class EarningsMonitor:
    """Poll one earnings source for the currently active streamer."""

    def __init__(
        self,
        settings: Settings,
        store: StreamerStore,
        source: EarningsSource | None = None,
        *,
        is_active: Callable[[], str | None] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.source = source or HttpEarningsSource(settings)
        self._is_active = is_active or (lambda: None)
        self._poller = PollingWorker(
            name="earnings-source",
            tick=self._tick,
            interval=lambda: EARNINGS_RENDER_INTERVAL_SECONDS,
            min_interval=0.0,
            error_delay=1.0,
            error_message="Einnahmen-Worker Fehler",
        )
        self._streamer_id: str | None = None
        self._streamer_name = ""
        self._streamer_handle = ""
        self._entry: EarningsEntry | None = None
        self._last_run_at: str | None = None
        self._error = ""
        self._missing = False
        self._checking = False
        self._next_poll_at = 0.0
        self._force_check = False
        self._signature: tuple[str, str, str, int] | None = None
        self._last_error_log = 0.0
        self._last_write_error = 0.0
        self._last_overlay_text = ""
        try:
            ensure_earnings_overlay_file(settings)
        except OSError as exc:
            logger.warning("Einnahmen-Overlay-Datei nicht anlegbar: %s", exc)

    def start(self) -> None:
        self._poller.start()

    async def stop(self) -> None:
        await self._poller.stop()
        self._checking = False
        self._clear_overlay()

    def request_check(self) -> None:
        """Wake the worker so a check starts as soon as possible."""
        self._force_check = True
        self._poller.request_check()

    def summary(self) -> dict:
        configured = self._configured()
        enabled = self._enabled()
        if not configured:
            status = "unconfigured"
        elif not self.settings.earnings_enabled:
            status = "disabled"
        elif self._streamer_id is None:
            status = "inactive"
        elif self._error:
            status = "error"
        elif self._missing:
            status = "missing"
        elif self._entry is None:
            status = "checking"
        else:
            status = "ok"

        streamer = None
        if self._streamer_id is not None:
            streamer = {
                "id": self._streamer_id,
                "name": self._streamer_name,
                "tiktok": self._streamer_handle,
            }
        period = (
            self._entry.period
            if self._entry is not None and self._entry.period
            else self.settings.earnings_period
        )
        return {
            "enabled": enabled,
            "configured": configured,
            "source": _safe_source(self.settings.earnings_source_url),
            "period": period,
            "period_label": EARNINGS_PERIOD_LABELS.get(period, period),
            "interval_seconds": int(self.settings.earnings_refresh_seconds),
            "timeout_seconds": int(self.settings.earnings_timeout_seconds),
            "overlay_enabled": bool(self.settings.earnings_overlay_enabled),
            "overlay_position": self.settings.earnings_overlay_position,
            "overlay_font_size": int(self.settings.earnings_overlay_font_size),
            "view": self.settings.earnings_view,
            "rates": self._entry.rates if self._entry is not None else None,
            "last_run_at": self._last_run_at,
            "checking": self._checking,
            "status": status,
            "active_streamer": streamer,
            "data": _entry_view(self._entry) if self._entry else None,
            "error": self._error,
            "missing": self._missing,
        }

    def _configured(self) -> bool:
        return bool(self.settings.earnings_source_url.strip())

    def _enabled(self) -> bool:
        return bool(self.settings.earnings_enabled and self._configured())

    def _signature_value(self) -> tuple[str, str, str, int]:
        return (
            self.settings.earnings_source_url.strip(),
            self.settings.earnings_source_api_key.strip(),
            self.settings.earnings_period,
            int(self.settings.earnings_timeout_seconds),
        )

    async def _tick(self) -> None:
        force_check = self._force_check
        self._force_check = False
        active_id = self._is_active()
        streamer = self.store.get(active_id) if active_id else None
        if streamer is None:
            self._set_inactive()
        else:
            self._sync_streamer(streamer)
            if not self._enabled():
                self._clear_data()
            else:
                signature = self._signature_value()
                if signature != self._signature:
                    self._signature = signature
                    self._clear_data()
                if force_check:
                    self._next_poll_at = 0.0
                if time.monotonic() >= self._next_poll_at:
                    await self._poll(streamer)

        self._write_overlay()

    def _sync_streamer(self, streamer: StreamerConfig) -> None:
        if streamer.id == self._streamer_id:
            return
        self._streamer_id = streamer.id
        self._streamer_name = streamer.name
        self._streamer_handle = normalize_handle(streamer.tiktok)
        self._clear_data()
        self._signature = None
        self._next_poll_at = 0.0
        self._write_overlay()

    def _set_inactive(self) -> None:
        self._streamer_id = None
        self._streamer_name = ""
        self._streamer_handle = ""
        self._clear_data()
        self._signature = None
        self._next_poll_at = 0.0

    def _clear_data(self) -> None:
        self._entry = None
        self._last_run_at = None
        self._error = ""
        self._missing = False

    async def _poll(self, streamer: StreamerConfig) -> None:
        self._checking = True
        try:
            entries = await asyncio.to_thread(self.source.fetch, streamer.tiktok)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never break the monitor
            self._error = _redact(
                str(exc) or type(exc).__name__,
                self.settings.earnings_source_api_key,
            )
            self._log_error(self._error)
        else:
            handle = normalize_handle(streamer.tiktok)
            entry = entries.get(handle)
            self._last_run_at = _utc_now_iso()
            if entry is None:
                self._entry = None
                self._missing = True
                self._error = ""
            else:
                self._entry = entry
                self._missing = False
                self._error = ""
        finally:
            self._checking = False
            self._next_poll_at = time.monotonic() + max(
                EARNINGS_MIN_WAIT_SECONDS,
                int(self.settings.earnings_refresh_seconds),
            )
            self._write_overlay()

    def overlay_text(self) -> str:
        """Return the rendered earnings text for the chat overlay.

        This works independently of the standalone drawtext overlay, so the
        chat card can show earnings even when the separate overlay is off.
        """
        if self._entry is None or self._streamer_id is None:
            return ""
        return render_overlay_text(
            self.settings.earnings_overlay_template,
            self._entry,
            name=self._streamer_name,
            username=self._streamer_handle,
            period=self._entry.period or self.settings.earnings_period,
        )

    def _write_overlay(self) -> None:
        text = (
            self.overlay_text()
            if self.settings.earnings_overlay_enabled
            else ""
        )
        if text == self._last_overlay_text:
            return
        self._last_overlay_text = text
        self._write_text(text)

    def _clear_overlay(self) -> None:
        self._last_overlay_text = ""
        self._write_text("")

    def _write_text(self, text: str) -> None:
        path = earnings_overlay_path(self.settings)
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            now = time.monotonic()
            if now - self._last_write_error >= EARNINGS_ERROR_LOG_INTERVAL_SECONDS:
                self._last_write_error = now
                logger.warning(
                    "Einnahmen-Overlay konnte nicht geschrieben werden: %s",
                    exc,
                )

    def _log_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log < EARNINGS_ERROR_LOG_INTERVAL_SECONDS:
            return
        self._last_error_log = now
        logger.warning("Einnahmen-Quelle nicht erreichbar: %s", message)

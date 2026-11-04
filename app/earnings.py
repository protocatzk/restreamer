from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.handles import normalize_handle
from app.models import StreamerConfig
from app.pause import find_pause_font
from app.settings import (
    EARNINGS_OVERLAY_POSITIONS,
    EARNINGS_PERIODS,
    EARNINGS_TEMPLATE_MAX_LENGTH,
    Settings,
)
from app.store import StreamerStore

logger = logging.getLogger(__name__)

EARNINGS_OVERLAY_FILENAME = "earnings-overlay.txt"
EARNINGS_DEFAULT_TEMPLATE = "{period}: {net} · Brutto {gross}"
EARNINGS_RENDER_INTERVAL_SECONDS = 1.0
EARNINGS_ERROR_LOG_INTERVAL_SECONDS = 120.0
EARNINGS_MIN_WAIT_SECONDS = 30

EARNINGS_PERIOD_LABELS = {
    "today": "Heute",
    "24h": "Letzte 24 h",
    "7d": "Letzte 7 Tage",
    "30d": "Letzte 30 Tage",
    "all": "Gesamt",
}

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")
_WHITESPACE_RE = re.compile(r"[ \t]+")

JsonFetcher = Callable[[str, dict[str, str], float], object]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def _first_value(item: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    for marker in ("EUR", "USD", "€", "$"):
        text = text.replace(marker, "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(round(number))


def _first_float(item: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        if key not in item:
            continue
        number = _as_float(item.get(key))
        if number is not None:
            return number
    return None


def _first_int(item: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        if key not in item:
            continue
        number = _as_int(item.get(key))
        if number is not None:
            return number
    return None


def _item_username(item: dict[str, object]) -> str:
    value = _first_value(
        item,
        "username",
        "unique_id",
        "uniqueId",
        "handle",
        "tiktok",
        "streamer_username",
        "streamerUsername",
        "user",
        "streamer",
        "channel",
    )
    if isinstance(value, dict):
        value = _first_value(
            value, "username", "unique_id", "uniqueId", "handle", "tiktok"
        )
    if value in (None, ""):
        return ""
    return str(value).strip()


def _merge_missing(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        if target.get(key) in (None, ""):
            target[key] = value


def _extract_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    streamer = payload.get("streamer")
    if isinstance(streamer, dict) and _item_username(streamer):
        merged = dict(streamer)
        for key in ("summary", "totals"):
            extra = payload.get(key)
            if isinstance(extra, dict):
                _merge_missing(merged, extra)
        sessions = payload.get("sessions")
        if isinstance(sessions, list):
            merged["__sessions"] = sessions
        rates = payload.get("rates")
        if isinstance(rates, dict):
            merged["__rates"] = rates
        return [merged]

    for key in ("streamers", "data", "items", "results", "channels"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for nested in ("streamers", "items", "results", "channels"):
                found = candidate.get(nested)
                if isinstance(found, list):
                    return [item for item in found if isinstance(item, dict)]
            if _item_username(candidate):
                return [candidate]

    for key in ("streamer", "channel", "user"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and _item_username(candidate):
            return [candidate]

    if _item_username(payload):
        return [payload]
    return []


def _extract_rates(payload: object) -> dict[str, float | None]:
    candidates: list[dict[str, object]] = []
    if isinstance(payload, dict):
        for key in ("rates", "settings"):
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                candidates.append(candidate)
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("rates", "settings"):
                candidate = data.get(key)
                if isinstance(candidate, dict):
                    candidates.append(candidate)

    def find(*keys: str) -> float | None:
        for candidate in candidates:
            for key in keys:
                number = _as_float(candidate.get(key))
                if number is not None:
                    return number
        return None

    return {
        "usd_per_diamond": find("usd_per_diamond", "usdPerDiamond"),
        "gross_usd_per_coin": find("gross_usd_per_coin", "grossUsdPerCoin"),
        "usd_to_eur_rate": find("usd_to_eur_rate", "usdToEurRate"),
    }


def _session_values(
    item: dict[str, object],
    rates: dict[str, float | None],
) -> tuple[float | None, float | None, float | None, float | None, int | None]:
    session_coins: float | None = None
    session_usd_net: float | None = None
    session_eur_net: float | None = None
    session_usd_gross: float | None = None
    session_gifts: int | None = None

    worker = item.get("worker_diagnostics")
    if isinstance(worker, dict):
        session_coins = _first_float(
            worker, "coins_received_session", "session_coins"
        )
        session_gifts = _first_int(
            worker, "gifts_received_session", "session_gifts"
        )

    sessions = item.get("__sessions")
    if session_coins is None and isinstance(sessions, list):
        session_items = [entry for entry in sessions if isinstance(entry, dict)]
        active = next(
            (
                entry
                for entry in session_items
                if str(entry.get("status", "")).strip().lower() == "active"
            ),
            session_items[0] if session_items else None,
        )
        if active is not None:
            session_coins = _first_float(
                active, "session_coins", "total_coins", "coins"
            )
            session_usd_net = _first_float(active, "usd_net", "usdNet")
            session_eur_net = _first_float(active, "eur_net", "eurNet")
            session_gifts = _first_int(
                active, "gift_events", "gifts_received_session"
            )

    item_rates = _extract_rates(item)
    for key, value in rates.items():
        if item_rates.get(key) is None:
            item_rates[key] = value

    usd_per_diamond = item_rates["usd_per_diamond"]
    gross_usd_per_coin = item_rates["gross_usd_per_coin"]
    usd_to_eur_rate = item_rates["usd_to_eur_rate"]

    if session_coins is not None:
        if session_usd_net is None and usd_per_diamond is not None:
            session_usd_net = session_coins * usd_per_diamond
        if session_usd_gross is None and gross_usd_per_coin is not None:
            session_usd_gross = session_coins * gross_usd_per_coin
        if session_eur_net is None and session_usd_net is not None:
            if usd_to_eur_rate is not None:
                session_eur_net = session_usd_net * usd_to_eur_rate

    return (
        session_coins,
        _round_money(session_usd_net),
        _round_money(session_eur_net),
        _round_money(session_usd_gross),
        session_gifts,
    )


def _round_money(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


@dataclass(frozen=True)
class EarningsEntry:
    username: str
    coins: float | None = None
    usd_net: float | None = None
    eur_net: float | None = None
    usd_gross: float | None = None
    eur_gross: float | None = None
    gift_events: int | None = None
    gifts_sent: int | None = None
    unique_gifters: int | None = None
    session_coins: float | None = None
    session_usd_net: float | None = None
    session_eur_net: float | None = None
    session_usd_gross: float | None = None
    session_gifts: int | None = None
    period: str = ""

    @property
    def currency(self) -> str:
        if self.eur_net is not None or self.eur_gross is not None:
            return "EUR"
        if self.usd_net is not None or self.usd_gross is not None:
            return "USD"
        return ""

    @property
    def net(self) -> float | None:
        return self.eur_net if self.currency == "EUR" else self.usd_net

    @property
    def gross(self) -> float | None:
        return self.eur_gross if self.currency == "EUR" else self.usd_gross

    @property
    def session_currency(self) -> str:
        if self.session_eur_net is not None:
            return "EUR"
        if self.session_usd_net is not None:
            return "USD"
        return ""

    @property
    def session_net(self) -> float | None:
        if self.session_currency == "EUR":
            return self.session_eur_net
        return self.session_usd_net

    @property
    def session_gross(self) -> float | None:
        if self.session_currency == "EUR":
            if self.session_eur_net is None:
                return None
            if self.session_usd_net and self.session_eur_net:
                ratio = self.session_eur_net / self.session_usd_net
                if self.session_usd_gross is not None:
                    return round(self.session_usd_gross * ratio, 2)
            return None
        return self.session_usd_gross


def parse_earnings(payload: object) -> dict[str, EarningsEntry]:
    """Parse a TokTracker-compatible earnings response.

    The contract accepts the public overview (``streamers`` list), the
    single-streamer detail response (``streamer`` + ``summary`` + ``sessions``)
    and a bare list. Missing values stay unknown; the monitor then shows the
    last known values or an explicit "no data" state.
    """
    items = _extract_items(payload)
    if not items:
        return {}

    rates = _extract_rates(payload)
    period = ""
    if isinstance(payload, dict):
        period = str(payload.get("period") or "").strip()

    earnings: dict[str, EarningsEntry] = {}
    for item in items:
        username = normalize_handle(_item_username(item))
        if not username:
            continue

        coins = _first_float(
            item, "total_coins", "coins", "diamonds", "diamond_count"
        )
        usd_net = _first_float(item, "usd_net", "usdNet", "net_usd")
        eur_net = _first_float(item, "eur_net", "eurNet", "net_eur")
        usd_gross = _first_float(item, "usd_gross", "usdGross", "gross_usd")
        eur_gross = _first_float(item, "eur_gross", "eurGross", "gross_eur")

        item_rates = _extract_rates(item)
        for key, value in rates.items():
            if item_rates.get(key) is None:
                item_rates[key] = value
        usd_per_diamond = item_rates["usd_per_diamond"]
        gross_usd_per_coin = item_rates["gross_usd_per_coin"]
        usd_to_eur_rate = item_rates["usd_to_eur_rate"]

        if coins is not None:
            if usd_net is None and usd_per_diamond is not None:
                usd_net = coins * usd_per_diamond
            if usd_gross is None and gross_usd_per_coin is not None:
                usd_gross = coins * gross_usd_per_coin
        if usd_net is not None and eur_net is None:
            if usd_to_eur_rate is not None:
                eur_net = usd_net * usd_to_eur_rate
        if usd_gross is not None and eur_gross is None:
            if usd_to_eur_rate is not None:
                eur_gross = usd_gross * usd_to_eur_rate

        (
            session_coins,
            session_usd_net,
            session_eur_net,
            session_usd_gross,
            session_gifts,
        ) = _session_values(item, rates)

        earnings[username] = EarningsEntry(
            username=username,
            coins=coins,
            usd_net=_round_money(usd_net),
            eur_net=_round_money(eur_net),
            usd_gross=_round_money(usd_gross),
            eur_gross=_round_money(eur_gross),
            gift_events=_first_int(
                item, "gift_events_count", "giftEventsCount", "gift_events"
            ),
            gifts_sent=_first_int(
                item, "total_gifts_sent", "totalGiftsSent", "gifts_sent"
            ),
            unique_gifters=_first_int(
                item, "unique_gifters", "uniqueGifters", "gifters"
            ),
            session_coins=session_coins,
            session_usd_net=session_usd_net,
            session_eur_net=session_eur_net,
            session_usd_gross=session_usd_gross,
            session_gifts=session_gifts,
            period=period,
        )
    return earnings


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


def earnings_overlay_path(settings: Settings) -> Path:
    return settings.logs_dir / EARNINGS_OVERLAY_FILENAME


def ensure_earnings_overlay_file(settings: Settings) -> Path:
    path = earnings_overlay_path(settings)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def _quote_filter_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _overlay_font_size(settings: Settings) -> int:
    if settings.earnings_overlay_font_size > 0:
        return settings.earnings_overlay_font_size
    return max(
        20, round(min(settings.output_width, settings.output_height) * 0.035)
    )


def build_overlay_filter(settings: Settings) -> str | None:
    """Build the ffmpeg drawtext filter for the active earnings overlay."""
    if not settings.earnings_overlay_enabled:
        return None
    path = earnings_overlay_path(settings)
    if not path.is_file():
        return None
    font = find_pause_font(settings.earnings_overlay_font)
    if font is None:
        return None

    font_size = _overlay_font_size(settings)
    line_spacing = max(4, font_size // 4)
    margin = max(16, round(settings.output_width * 0.033))
    box_border = max(10, round(font_size * 0.55))

    position = settings.earnings_overlay_position
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


def _format_amount(value: float | None, currency: str) -> str:
    if value is None:
        return "–"
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = "€" if currency == "EUR" else "$"
    return f"{formatted} {symbol}"


def _format_int(value: int | None) -> str:
    if value is None:
        return "–"
    return f"{int(value):,}".replace(",", ".")


def render_overlay_text(
    template: str,
    entry: EarningsEntry | None,
    *,
    name: str = "",
    username: str = "",
    period: str = "",
) -> str:
    if entry is None:
        return ""
    period_label = EARNINGS_PERIOD_LABELS.get(period, period or "")
    currency = entry.currency
    values = {
        "name": (name or "").strip(),
        "username": (username or "").strip().lstrip("@"),
        "period": period_label,
        "net": _format_amount(entry.net, currency),
        "gross": _format_amount(entry.gross, currency),
        "coins": _format_int(entry.coins),
        "gifts": _format_int(entry.gift_events),
        "gifters": _format_int(entry.unique_gifters),
    }
    raw = (template or "").strip() or EARNINGS_DEFAULT_TEMPLATE
    if len(raw) > EARNINGS_TEMPLATE_MAX_LENGTH:
        raw = raw[:EARNINGS_TEMPLATE_MAX_LENGTH]
    rendered = _PLACEHOLDER_PATTERN.sub(
        lambda match: values.get(match.group(1), match.group(0)), raw
    )
    lines = [
        _WHITESPACE_RE.sub(" ", line).strip()
        for line in rendered.splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def _entry_view(entry: EarningsEntry) -> dict:
    currency = entry.currency
    session_currency = entry.session_currency
    return {
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
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
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
        if self._task is not None:
            return
        self._wake.clear()
        self._task = asyncio.create_task(self._run(), name="earnings-source")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._checking = False
        self._clear_overlay()

    def request_check(self) -> None:
        """Wake the worker so a check starts as soon as possible."""
        self._force_check = True
        self._wake.set()

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

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - earnings must never break the stream
                logger.exception("Einnahmen-Worker Fehler")
                await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        self._wake.clear()
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
        try:
            await asyncio.wait_for(
                self._wake.wait(), timeout=EARNINGS_RENDER_INTERVAL_SECONDS
            )
        except TimeoutError:
            pass

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

    def _write_overlay(self) -> None:
        if (
            not self.settings.earnings_overlay_enabled
            or self._entry is None
            or self._streamer_id is None
        ):
            text = ""
        else:
            text = render_overlay_text(
                self.settings.earnings_overlay_template,
                self._entry,
                name=self._streamer_name,
                username=self._streamer_handle,
                period=self._entry.period or self.settings.earnings_period,
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


__all__ = [
    "EARNINGS_OVERLAY_FILENAME",
    "EARNINGS_PERIOD_LABELS",
    "EARNINGS_PERIODS",
    "EARNINGS_OVERLAY_POSITIONS",
    "EarningsEntry",
    "EarningsMonitor",
    "EarningsSource",
    "HttpEarningsSource",
    "build_overlay_filter",
    "default_summary",
    "ensure_earnings_overlay_file",
    "earnings_overlay_path",
    "parse_earnings",
    "render_overlay_text",
]

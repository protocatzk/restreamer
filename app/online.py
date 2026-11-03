from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.handles import normalize_handle
from app.models import StreamerConfig
from app.settings import Settings
from app.store import StreamerStore

logger = logging.getLogger(__name__)

ONLINE_ERROR_LOG_INTERVAL_SECONDS = 120.0
ONLINE_MIN_WAIT_SECONDS = 5

_TRUE_STATUS_VALUES = frozenset(
    {"1", "true", "yes", "on", "live", "online", "is_live", "streaming"}
)
_FALSE_STATUS_VALUES = frozenset(
    {
        "0",
        "false",
        "no",
        "off",
        "offline",
        "not_live",
        "notlive",
        "was_live",
        "ended",
    }
)

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


def _parse_live(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_STATUS_VALUES:
        return True
    if text in _FALSE_STATUS_VALUES:
        return False
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


def _item_live(item: dict[str, object]) -> bool | None:
    for key in (
        "is_live",
        "isLive",
        "live",
        "online",
        "is_online",
        "isOnline",
        "live_status",
        "liveStatus",
        "status",
    ):
        if key not in item:
            continue
        parsed = _parse_live(item[key])
        if parsed is not None:
            return parsed
    return None


def _extract_items(payload: object) -> list[object] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None

    for key in ("streamers", "data", "items", "results", "channels"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            for nested in ("streamers", "items", "results", "channels"):
                found = candidate.get(nested)
                if isinstance(found, list):
                    return found
            if _item_username(candidate):
                return [candidate]

    for key in ("streamer", "channel", "user"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and _item_username(candidate):
            return [candidate]

    if _item_username(payload):
        return [payload]
    return None


def parse_online_statuses(payload: object) -> dict[str, bool]:
    """Parse a JSON live-status response into ``handle -> is_live``.

    The contract intentionally accepts common list wrappers (TokTracker's
    ``streamers`` array, for example) as well as single-streamer detail
    responses. Entries without a recognisable username or live flag are
    skipped instead of raising: a missing status simply stays unknown.
    """
    items = _extract_items(payload)
    if not items:
        return {}

    statuses: dict[str, bool] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        username = normalize_handle(_item_username(item))
        if not username:
            continue
        live = _item_live(item)
        if live is None:
            continue
        statuses[username] = live
    return statuses


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> object:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


class OnlineSource(Protocol):
    def fetch(self) -> dict[str, bool]:
        """Return the current live state keyed by normalized TikTok handle."""


class HttpOnlineSource:
    """Generic HTTP/JSON source for live statuses."""

    def __init__(
        self, settings: Settings, fetcher: JsonFetcher | None = None
    ) -> None:
        self.settings = settings
        self._fetcher = fetcher or _fetch_json

    def fetch(self) -> dict[str, bool]:
        url = self.settings.online_source_url.strip()
        if not url:
            return {}

        headers = {
            "Accept": "application/json",
            "User-Agent": "restreamer-online-source/1",
        }
        api_key = self.settings.online_source_api_key.strip()
        if api_key:
            headers["X-API-Key"] = api_key
        timeout = max(
            1.0, float(min(self.settings.online_check_timeout_seconds, 120))
        )
        payload = self._fetcher(url, headers, timeout)
        return parse_online_statuses(payload)


@dataclass
class StreamerOnlineState:
    online: bool | None = None
    checked_at: str | None = None
    error: str = ""
    checking: bool = False

    def view(self) -> dict:
        return {
            "online": self.online,
            "online_checked_at": self.checked_at,
            "online_error": self.error,
            "online_checking": self.checking,
        }


class OnlineStatusMonitor:
    """Poll one HTTP/JSON source for every streamer's live status.

    A single request fetches the status of all configured streamers. Missing
    entries or missing live flags leave that streamer at ``unknown``; they are
    never treated as offline and never abort the whole check.
    """

    def __init__(
        self,
        settings: Settings,
        store: StreamerStore,
        source: OnlineSource | None = None,
        *,
        is_active: Callable[[], str | None] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.source = source or HttpOnlineSource(settings)
        self._is_active = is_active or (lambda: None)
        self._states: dict[str, StreamerOnlineState] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_run_at: str | None = None
        self._last_error = ""
        self._last_error_log = 0.0
        self._signature: tuple[str, str, int] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._wake.clear()
        self._task = asyncio.create_task(self._run(), name="online-status")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def request_check(self) -> None:
        """Wake the worker so a check starts as soon as possible."""
        self._wake.set()

    def view(self, streamer_id: str) -> dict:
        state = self._states.get(streamer_id)
        if state is None:
            return StreamerOnlineState().view()
        return state.view()

    def summary(self) -> dict:
        states = list(self._states.values())
        return {
            "enabled": self._enabled(),
            "configured": self._configured(),
            "source": _safe_source(self.settings.online_source_url),
            "interval_seconds": int(self.settings.online_check_interval_seconds),
            "timeout_seconds": int(self.settings.online_check_timeout_seconds),
            "last_run_at": self._last_run_at,
            "checking": any(state.checking for state in states),
            "online": sum(1 for state in states if state.online is True),
            "checked": sum(1 for state in states if state.checked_at is not None),
            "errors": sum(1 for state in states if state.error),
            "error": self._last_error,
        }

    def _configured(self) -> bool:
        return bool(self.settings.online_source_url.strip())

    def _enabled(self) -> bool:
        return bool(self.settings.online_check_enabled and self._configured())

    def _signature_value(self) -> tuple[str, str, int]:
        return (
            self.settings.online_source_url.strip(),
            self.settings.online_source_api_key.strip(),
            int(self.settings.online_check_timeout_seconds),
        )

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            if self._enabled():
                signature = self._signature_value()
                if signature != self._signature:
                    self._signature = signature
                    self._states.clear()
                    self._last_run_at = None
                    self._last_error = ""
                try:
                    await self._check_all()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - status checks are best effort
                    logger.exception("Online-Status-Prüfung fehlgeschlagen")
            else:
                self._states.clear()
                self._last_run_at = None
                self._last_error = ""
                self._signature = None

            wait = max(
                ONLINE_MIN_WAIT_SECONDS,
                int(self.settings.online_check_interval_seconds),
            )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except TimeoutError:
                pass

    async def _check_all(self) -> None:
        self.store.refresh()
        active_id = self._is_active()
        candidates = [
            streamer
            for streamer in self.store.streamers
            if streamer.enabled and streamer.id != active_id
        ]

        self._prune_states(candidates)
        if not candidates:
            self._last_run_at = _utc_now_iso()
            self._last_error = ""
            return

        for streamer in candidates:
            state = self._states.setdefault(
                streamer.id, StreamerOnlineState()
            )
            state.checking = True

        try:
            statuses = await asyncio.to_thread(self.source.fetch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never break the monitor
            self._last_error = _redact(
                str(exc) or type(exc).__name__,
                self.settings.online_source_api_key,
            )
            self._log_error(self._last_error)
            return
        else:
            self._last_error = ""
            self._apply_statuses(candidates, statuses)
            self._last_run_at = _utc_now_iso()
        finally:
            for streamer in candidates:
                state = self._states.get(streamer.id)
                if state is not None:
                    state.checking = False

    def _prune_states(self, candidates: Iterable[StreamerConfig]) -> None:
        known_ids = {streamer.id for streamer in candidates}
        for streamer_id in list(self._states):
            if streamer_id not in known_ids:
                del self._states[streamer_id]

    def _apply_statuses(
        self,
        candidates: list[StreamerConfig],
        statuses: dict[str, bool],
    ) -> None:
        checked_at = _utc_now_iso()
        for streamer in candidates:
            state = self._states.setdefault(
                streamer.id, StreamerOnlineState()
            )
            state.online = statuses.get(normalize_handle(streamer.tiktok))
            state.checked_at = checked_at
            state.error = ""

    def _log_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log < ONLINE_ERROR_LOG_INTERVAL_SECONDS:
            return
        self._last_error_log = now
        logger.warning("Online-Status-Quelle nicht erreichbar: %s", message)

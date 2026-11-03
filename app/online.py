from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models import StreamerConfig
from app.resolver import ResolveError, Resolver
from app.settings import Settings
from app.store import StreamerStore

logger = logging.getLogger(__name__)

ONLINE_CHECK_CONCURRENCY = 3
ONLINE_ERROR_LOG_INTERVAL_SECONDS = 120.0
ONLINE_MIN_WAIT_SECONDS = 5


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
    """Poll every configured streamer's live status in the background.

    The monitor never touches the running restream. It uses yt-dlp's
    ``--simulate --print is_live`` mode, which is much cheaper than a full
    resolution and is skipped for the streamer that is currently on air (the
    supervisor already knows that state).
    """

    def __init__(
        self,
        settings: Settings,
        store: StreamerStore,
        resolver: Resolver,
        *,
        is_active: Callable[[], str | None] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.resolver = resolver
        self._is_active = is_active or (lambda: None)
        self._states: dict[str, StreamerOnlineState] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._last_run_at: str | None = None
        self._last_error_log = 0.0

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
            "enabled": bool(self.settings.online_check_enabled),
            "interval_seconds": int(self.settings.online_check_interval_seconds),
            "timeout_seconds": int(self.settings.online_check_timeout_seconds),
            "last_run_at": self._last_run_at,
            "checking": any(state.checking for state in states),
            "online": sum(1 for state in states if state.online is True),
            "checked": sum(1 for state in states if state.checked_at is not None),
            "errors": sum(1 for state in states if state.error),
        }

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            if self.settings.online_check_enabled:
                try:
                    await self._check_all()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - status checks are best effort
                    logger.exception("Online-Status-Prüfung fehlgeschlagen")
            else:
                self._states.clear()
                self._last_run_at = None

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

        known_ids = {streamer.id for streamer in candidates}
        for streamer_id in list(self._states):
            if streamer_id not in known_ids:
                del self._states[streamer_id]

        if not candidates:
            self._last_run_at = _utc_now_iso()
            return

        semaphore = asyncio.Semaphore(ONLINE_CHECK_CONCURRENCY)
        await asyncio.gather(
            *(self._check_one(streamer, semaphore) for streamer in candidates)
        )
        self._last_run_at = _utc_now_iso()

    async def _check_one(
        self, streamer: StreamerConfig, semaphore: asyncio.Semaphore
    ) -> None:
        async with semaphore:
            state = self._states.setdefault(streamer.id, StreamerOnlineState())
            state.checking = True
            try:
                online = await self.resolver.probe(streamer)
            except asyncio.CancelledError:
                state.checking = False
                raise
            except ResolveError as exc:
                self._set_error(streamer.id, state, str(exc))
            except Exception as exc:  # noqa: BLE001 - never break the monitor
                self._set_error(
                    streamer.id, state, str(exc) or type(exc).__name__
                )
            else:
                state.online = online
                state.checked_at = _utc_now_iso()
                state.error = ""
            finally:
                state.checking = False

    def _set_error(
        self, streamer_id: str, state: StreamerOnlineState, message: str
    ) -> None:
        state.online = None
        state.error = message
        now = time.monotonic()
        if now - self._last_error_log >= ONLINE_ERROR_LOG_INTERVAL_SECONDS:
            self._last_error_log = now
            logger.warning(
                "Online-Status für %s nicht ermittelbar: %s",
                streamer_id,
                message,
            )

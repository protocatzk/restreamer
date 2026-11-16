"""Small reusable polling loop for background monitors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

logger = logging.getLogger(__name__)


class PollingWorker:
    """Run an async tick function periodically and wake on request."""

    def __init__(
        self,
        *,
        name: str,
        tick: Callable[[], Awaitable[None]],
        interval: Callable[[], float],
        min_interval: float = 1.0,
        error_delay: float | None = None,
        error_message: str = "Hintergrund-Worker Fehler",
    ) -> None:
        self._name = name
        self._tick = tick
        self._interval = interval
        self._min_interval = min_interval
        self._error_delay = error_delay
        self._error_message = error_message
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def start(self) -> None:
        if self._task is not None:
            return
        self._wake.clear()
        self._task = asyncio.create_task(self._run(), name=self._name)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def request_check(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            delay: float | None = None
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - monitors must stay alive
                logger.exception(self._error_message)
                delay = self._error_delay

            if delay is None:
                delay = max(self._min_interval, float(self._interval()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

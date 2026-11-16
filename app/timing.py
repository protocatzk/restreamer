"""Injectable time source for the supervisor.

The supervisor needs wall-clock timestamps for its status views but measures
every timeout and watchdog interval on the monotonic clock.  Tests used to
wait for those intervals in real time, which made the playlist and watchdog
suites timing sensitive.  A single ``Clock`` abstraction keeps production
behaviour unchanged while allowing tests to advance time deterministically.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """Minimal time source used by the stream supervisor."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary, non-decreasing epoch."""

    def time(self) -> float:
        """Seconds since the Unix epoch."""

    async def sleep(self, seconds: float) -> None:
        """Wait for ``seconds`` on this clock."""


class WallClock:
    """Real-time clock used in production."""

    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class FakeClock:
    """Deterministic clock for tests.

    ``advance()`` moves both clocks forward and wakes every sleeper whose
    deadline has been reached.  Sleepers are woken in deadline order so that
    tests can interleave several waiting tasks predictably.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._monotonic = float(start)
        self._wall = float(start)
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self._monotonic

    def time(self) -> float:
        return self._wall

    async def sleep(self, seconds: float) -> None:
        delay = max(0.0, float(seconds))
        if delay == 0.0:
            await asyncio.sleep(0)
            return

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        waiter = (self._monotonic + delay, future)
        self._waiters.append(waiter)
        try:
            await future
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def advance(self, seconds: float) -> None:
        """Move time forward and wake all sleepers that are now due."""
        self._monotonic += seconds
        self._wall += seconds
        now = self._monotonic
        due = [waiter for waiter in self._waiters if waiter[0] <= now]
        for waiter in sorted(due, key=lambda item: item[0]):
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            if not waiter[1].done():
                waiter[1].set_result(None)

    @property
    def sleeping(self) -> int:
        """Number of tasks currently waiting on this clock."""
        return len(self._waiters)

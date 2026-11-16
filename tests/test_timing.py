from __future__ import annotations

import asyncio
import time

from app.timing import FakeClock, WallClock


async def test_fake_clock_wakes_sleeper_when_time_advances() -> None:
    clock = FakeClock(start=10.0)
    done = False

    async def waiter() -> None:
        nonlocal done
        await clock.sleep(5)
        done = True

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert clock.sleeping == 1
    assert done is False

    clock.advance(4.9)
    await asyncio.sleep(0)
    assert done is False

    clock.advance(0.1)
    await asyncio.sleep(0)
    assert done is True
    assert clock.monotonic() == 15.0
    await task


async def test_fake_clock_orders_sleepers_by_deadline() -> None:
    clock = FakeClock()
    order: list[str] = []

    async def waiter(name: str, delay: float) -> None:
        await clock.sleep(delay)
        order.append(name)

    tasks = [
        asyncio.create_task(waiter("later", 10)),
        asyncio.create_task(waiter("soon", 2)),
    ]
    await asyncio.sleep(0)

    clock.advance(10)
    await asyncio.sleep(0)
    await asyncio.gather(*tasks)

    assert order == ["soon", "later"]


def test_wall_clock_matches_stdlib() -> None:
    clock = WallClock()

    assert abs(clock.monotonic() - time.monotonic()) < 1.0
    assert abs(clock.time() - time.time()) < 1.0

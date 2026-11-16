from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest

from app.models import ResolvedStream
from app.store import StreamerStore
from app.supervisor import Supervisor
from app.timing import FakeClock

CONFIG = """
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
"""

LIVE = ResolvedStream(
    url="https://example.invalid/live.m3u8",
    title="Alice live",
    is_live=True,
    source_vcodec="avc1.64001f",
    height=720,
)


class FakeResolver:
    async def resolve(self, streamer) -> ResolvedStream:
        return LIVE


async def spawn_sleep(args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "sleep",
        "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def wait_until(
    predicate: Callable[[], bool],
    timeout_s: float = 5.0,
    *,
    label: str = "",
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    detail = f" ({label})" if label else ""
    raise AssertionError("Bedingung wurde nicht rechtzeitig erfüllt" + detail)


@pytest.fixture
def store(settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


async def test_sleep_is_driven_by_the_injected_clock(settings, store) -> None:
    clock = FakeClock()
    supervisor = Supervisor(settings, store, FakeResolver(), clock=clock)

    task = asyncio.create_task(supervisor._sleep(30))
    for _ in range(10):
        if clock.sleeping:
            break
        await asyncio.sleep(0)
    assert not task.done()
    assert clock.sleeping == 1

    clock.advance(30)
    assert await asyncio.wait_for(task, timeout=1) is True


async def test_sleep_wakes_early_when_stop_is_requested(settings, store) -> None:
    clock = FakeClock()
    supervisor = Supervisor(settings, store, FakeResolver(), clock=clock)

    task = asyncio.create_task(supervisor._sleep(30))
    await asyncio.sleep(0)

    supervisor._stop_event.set()
    assert await asyncio.wait_for(task, timeout=1) is False


async def test_stall_watchdog_fires_from_injected_clock(
    settings, store, monkeypatch
) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    quick = replace(
        settings,
        watchdog_interval_seconds=2,
        stall_timeout_seconds=10,
        auto_refresh_minutes=0,
    )
    clock = FakeClock()
    supervisor = Supervisor(
        quick, store, FakeResolver(), spawn=spawn_sleep, clock=clock
    )

    try:
        await supervisor.start("alice")
        await wait_until(lambda: supervisor.status()["state"] == "live")
        supervisor._last_restart_reason = ""

        for _ in range(20):
            if clock.sleeping:
                break
            await asyncio.sleep(0)

        clock.advance(15)
        await wait_until(
            lambda: supervisor.status()["stall_count"] >= 1,
            label="stall watchdog",
        )

        assert supervisor.status()["last_restart_reason"] == "stall"
    finally:
        await supervisor.stop()

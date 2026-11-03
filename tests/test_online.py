from __future__ import annotations

import asyncio
from dataclasses import replace

from app.online import OnlineStatusMonitor
from app.resolver import ResolveError
from app.store import StreamerStore

CONFIG = """
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key_env: BOB_YT_KEY
"""


class FakeProbeResolver:
    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.probed: list[str] = []

    async def probe(self, streamer) -> bool:
        self.probed.append(streamer.id)
        result = self.results.get(streamer.id, False)
        if isinstance(result, Exception):
            raise result
        return bool(result)


def make_store(settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


async def test_monitor_collects_live_states(settings) -> None:
    store = make_store(settings)
    resolver = FakeProbeResolver({"alice": True, "bob": False})
    monitor = OnlineStatusMonitor(settings, store, resolver)

    await monitor._check_all()

    assert monitor.view("alice")["online"] is True
    assert monitor.view("bob")["online"] is False
    assert monitor.view("alice")["online_checked_at"] is not None
    assert sorted(resolver.probed) == ["alice", "bob"]

    summary = monitor.summary()
    assert summary["online"] == 1
    assert summary["checked"] == 2
    assert summary["errors"] == 0


async def test_monitor_skips_active_streamer(settings) -> None:
    store = make_store(settings)
    resolver = FakeProbeResolver({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(
        settings, store, resolver, is_active=lambda: "alice"
    )

    await monitor._check_all()

    assert resolver.probed == ["bob"]
    assert monitor.view("alice")["online"] is None
    assert monitor.view("bob")["online"] is True


async def test_monitor_marks_probe_errors_unknown(settings) -> None:
    store = make_store(settings)
    resolver = FakeProbeResolver(
        {"alice": ResolveError("HTTP 403"), "bob": True}
    )
    monitor = OnlineStatusMonitor(settings, store, resolver)

    await monitor._check_all()

    alice = monitor.view("alice")
    assert alice["online"] is None
    assert "403" in alice["online_error"]
    assert monitor.view("bob")["online"] is True
    assert monitor.summary()["errors"] == 1


async def test_monitor_removes_deleted_streamers(settings) -> None:
    store = make_store(settings)
    resolver = FakeProbeResolver({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(settings, store, resolver)

    await monitor._check_all()
    assert monitor.view("alice")["online"] is True

    store.delete("alice")
    await monitor._check_all()

    assert monitor.view("alice")["online"] is None
    assert resolver.probed[-1] == "bob"


async def test_monitor_start_and_stop(settings) -> None:
    settings = replace(
        settings,
        online_check_enabled=True,
        online_check_interval_seconds=10,
    )
    store = make_store(settings)
    resolver = FakeProbeResolver({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(settings, store, resolver)

    monitor.start()
    try:
        for _ in range(100):
            if monitor.summary()["checked"] == 2:
                break
            await asyncio.sleep(0.01)
        assert monitor.summary()["checked"] == 2
    finally:
        await monitor.stop()

    assert monitor._task is None

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.online import (
    HttpOnlineSource,
    OnlineStatusMonitor,
    parse_online_statuses,
)
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


class FakeOnlineSource:
    def __init__(self, statuses: dict[str, bool] | None = None) -> None:
        self.statuses = statuses or {}
        self.calls = 0
        self.error: Exception | None = None

    def fetch(self) -> dict[str, bool]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.statuses


def make_store(settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


def test_parse_toktracker_overview_statuses() -> None:
    payload = {
        "period": "24h",
        "streamers": [
            {"username": "supergirl0584", "is_live": True},
            {"username": "@KarlchenTV", "is_live": False},
            {"display_name": "ohne Handle", "is_live": True},
        ],
    }

    assert parse_online_statuses(payload) == {
        "supergirl0584": True,
        "karlchentv": False,
    }


def test_parse_accepts_aliases_and_skips_missing_status() -> None:
    payload = [
        {"username": "alice", "live_status": "is_live"},
        {"unique_id": "bob", "isLive": "offline"},
        {"username": "carol", "is_live": None},
        {"username": "dave", "status": "stopped"},
        {"username": "erin", "online": 1},
        {"username": "frank", "online": 0},
    ]

    assert parse_online_statuses(payload) == {
        "alice": True,
        "bob": False,
        "erin": True,
        "frank": False,
    }


def test_parse_single_streamer_detail() -> None:
    payload = {
        "streamer": {"username": "alice", "is_live": True},
        "summary": {},
    }

    assert parse_online_statuses(payload) == {"alice": True}


def test_http_source_sends_api_key(settings) -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, headers, timeout))
        return {"streamers": [{"username": "alice", "is_live": True}]}

    configured = replace(
        settings,
        online_source_url="https://toktracker.cc/api/public/overview",
        online_source_api_key="secret-key",
        online_check_timeout_seconds=15,
    )
    source = HttpOnlineSource(configured, fetcher=fake_fetcher)

    assert source.fetch() == {"alice": True}
    assert calls == [
        (
            "https://toktracker.cc/api/public/overview",
            {
                "Accept": "application/json",
                "User-Agent": "restreamer-online-source/1",
                "X-API-Key": "secret-key",
            },
            15.0,
        )
    ]


def test_http_source_without_url_does_not_fetch(settings) -> None:
    calls: list[str] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append(url)
        return {}

    source = HttpOnlineSource(settings, fetcher=fake_fetcher)

    assert source.fetch() == {}
    assert calls == []


async def test_monitor_collects_live_states(settings) -> None:
    store = make_store(settings)
    source = FakeOnlineSource({"alice": True, "bob": False})
    monitor = OnlineStatusMonitor(settings, store, source)

    await monitor._check_all()

    assert monitor.view("alice")["online"] is True
    assert monitor.view("bob")["online"] is False
    assert monitor.view("alice")["online_checked_at"] is not None
    assert source.calls == 1

    summary = monitor.summary()
    assert summary["online"] == 1
    assert summary["checked"] == 2
    assert summary["errors"] == 0
    assert summary["error"] == ""


async def test_monitor_leaves_missing_statuses_unknown(settings) -> None:
    store = make_store(settings)
    monitor = OnlineStatusMonitor(settings, store, FakeOnlineSource({"alice": True}))

    await monitor._check_all()

    alice = monitor.view("alice")
    bob = monitor.view("bob")
    assert alice["online"] is True
    assert bob["online"] is None
    assert bob["online_checked_at"] is not None
    assert bob["online_error"] == ""
    assert monitor.summary()["checked"] == 2
    assert monitor.summary()["errors"] == 0


async def test_monitor_notifies_status_handler(settings) -> None:
    store = make_store(settings)
    monitor = OnlineStatusMonitor(
        settings, store, FakeOnlineSource({"alice": True, "bob": False})
    )
    received: list[dict[str, bool]] = []

    async def handler(statuses: dict[str, bool]) -> None:
        received.append(statuses)

    monitor.set_status_handler(handler)
    await monitor._check_all()

    assert received == [{"alice": True, "bob": False}]


async def test_monitor_skips_active_streamer(settings) -> None:
    store = make_store(settings)
    source = FakeOnlineSource({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(
        settings, store, source, is_active=lambda: "alice"
    )

    await monitor._check_all()

    assert source.calls == 1
    assert monitor.view("alice")["online"] is None
    assert monitor.view("bob")["online"] is True


async def test_monitor_keeps_last_status_on_source_error(settings) -> None:
    store = make_store(settings)
    source = FakeOnlineSource({"alice": True, "bob": False})
    monitor = OnlineStatusMonitor(settings, store, source)

    await monitor._check_all()
    last_run_at = monitor.summary()["last_run_at"]

    source.error = RuntimeError("HTTP 503")
    await monitor._check_all()

    assert monitor.view("alice")["online"] is True
    assert monitor.view("bob")["online"] is False
    assert "503" in monitor.summary()["error"]
    assert monitor.summary()["last_run_at"] == last_run_at


async def test_monitor_removes_deleted_streamers(settings) -> None:
    store = make_store(settings)
    source = FakeOnlineSource({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(settings, store, source)

    await monitor._check_all()
    assert monitor.view("alice")["online"] is True

    store.delete("alice")
    await monitor._check_all()

    assert monitor.view("alice")["online"] is None
    assert monitor.view("bob")["online"] is True


async def test_monitor_start_and_stop(settings) -> None:
    settings = replace(
        settings,
        online_check_enabled=True,
        online_check_interval_seconds=10,
        online_source_url="http://example.invalid/status",
    )
    store = make_store(settings)
    source = FakeOnlineSource({"alice": True, "bob": True})
    monitor = OnlineStatusMonitor(settings, store, source)

    monitor.start()
    try:
        for _ in range(100):
            if monitor.summary()["checked"] == 2:
                break
            await asyncio.sleep(0.01)
        assert monitor.summary()["checked"] == 2
        assert monitor.summary()["enabled"] is True
    finally:
        await monitor.stop()

    assert monitor._task is None

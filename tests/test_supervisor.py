from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from app.models import ResolvedStream
from app.store import StreamerStore
from app.supervisor import KeyNotConfigured, StreamerNotFound, Supervisor

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


class FakeResolver:
    def __init__(self, stream: ResolvedStream) -> None:
        self.stream = stream
        self.calls = 0

    async def resolve(self, streamer) -> ResolvedStream:
        self.calls += 1
        return self.stream


async def spawn_sleep(args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "sleep",
        "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Bedingung wurde nicht rechtzeitig erfüllt")


LIVE = ResolvedStream(
    url="https://example.invalid/live.m3u8",
    title="Alice live",
    is_live=True,
    source_vcodec="avc1.64001f",
    height=720,
)
OFFLINE = ResolvedStream(url="", is_live=False)


@pytest.fixture
def store(settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


async def test_start_and_stop(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    supervisor = Supervisor(
        settings, store, FakeResolver(LIVE), spawn=spawn_sleep
    )

    try:
        await supervisor.start("alice")
        await wait_until(lambda: supervisor.status()["state"] == "live")
        status = supervisor.status()

        assert status["active"] == "alice"
        assert status["pid"] is not None
        assert status["sessions"] == 1
        assert status["streamers"][0]["key_configured"] is True
    finally:
        await supervisor.stop()

    status = supervisor.status()
    assert status["state"] == "idle"
    assert status["active"] is None
    assert status["pid"] is None


async def test_offline_without_watch_finishes(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    resolver = FakeResolver(OFFLINE)
    supervisor = Supervisor(settings, store, resolver, spawn=spawn_sleep)

    await supervisor.start("alice", watch=False)
    await wait_until(lambda: supervisor.status()["state"] == "idle")

    assert resolver.calls == 1
    assert supervisor.status()["active"] is None


async def test_switch_replaces_active_stream(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    monkeypatch.setenv("BOB_YT_KEY", "bob-key")
    supervisor = Supervisor(
        settings, store, FakeResolver(LIVE), spawn=spawn_sleep
    )

    try:
        await supervisor.start("alice")
        await wait_until(lambda: supervisor.status()["state"] == "live")

        await supervisor.switch("bob")
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live"
        )
        assert supervisor.status()["sessions"] == 1
    finally:
        await supervisor.stop()


async def test_missing_key_is_rejected(settings, store, monkeypatch) -> None:
    monkeypatch.delenv("ALICE_YT_KEY", raising=False)
    supervisor = Supervisor(
        settings, store, FakeResolver(OFFLINE), spawn=spawn_sleep
    )

    with pytest.raises(KeyNotConfigured):
        await supervisor.start("alice")


async def test_unknown_streamer_is_rejected(settings, store) -> None:
    supervisor = Supervisor(
        settings, store, FakeResolver(OFFLINE), spawn=spawn_sleep
    )

    with pytest.raises(StreamerNotFound):
        await supervisor.start("nobody")

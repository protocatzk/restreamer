from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.models import PlaylistConfig, ResolvedStream
from app.store import StreamerStore
from app.supervisor import (
    KeyNotConfigured,
    StreamerNotFound,
    Supervisor,
    playlist_end_deadline,
)

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


class HangingResolver:
    async def resolve(self, streamer) -> ResolvedStream:
        await asyncio.sleep(3600)
        return OFFLINE


async def spawn_sleep(args: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "sleep",
        "60",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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


async def test_stop_cancels_hanging_resolver(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    supervisor = Supervisor(
        settings, store, HangingResolver(), spawn=spawn_sleep
    )

    await supervisor.start("alice")
    await asyncio.sleep(0.05)
    await supervisor.stop()

    assert supervisor.status()["state"] == "idle"
    assert supervisor.status()["active"] is None


async def test_unknown_streamer_is_rejected(settings, store) -> None:
    supervisor = Supervisor(
        settings, store, FakeResolver(OFFLINE), spawn=spawn_sleep
    )

    with pytest.raises(StreamerNotFound):
        await supervisor.start("nobody")


class FakeStdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)

    async def read(self, size: int) -> bytes:
        return next(self._chunks, b"")


class FakeOutputProcess:
    def __init__(self, chunks: list[bytes]) -> None:
        self.stderr = FakeStdout(chunks)
        self.stdout = FakeStdout([])


async def test_pump_logs_splits_progress_on_carriage_return(settings, store) -> None:
    supervisor = Supervisor(
        settings, store, FakeResolver(OFFLINE), spawn=spawn_sleep
    )

    await supervisor._pump_logs(FakeOutputProcess([b"frame= 1\rframe= 2\nrest"]))

    assert supervisor._log.tail(10) == ["frame= 1", "frame= 2", "rest"]


async def test_pump_progress_updates_progress_state(settings, store) -> None:
    supervisor = Supervisor(
        settings, store, FakeResolver(OFFLINE), spawn=spawn_sleep
    )
    process = FakeOutputProcess([])
    process.stdout = FakeStdout(
        [b"out_time_us=1000000\ntotal_size=5000\nspeed=1.01x\n"]
    )

    await supervisor._pump_progress(process)

    assert supervisor._last_out_time_us == 1_000_000
    assert supervisor._last_total_size == 5000
    assert supervisor._progress_out_time_us == 1_000_000
    assert supervisor._progress_speed == "1.01x"
    assert supervisor._last_progress_monotonic is not None


async def test_stall_triggers_restart(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    quick = replace(
        settings,
        stall_timeout_seconds=2,
        watchdog_interval_seconds=2,
        auto_refresh_minutes=0,
        backoff_initial_seconds=1,
    )
    supervisor = Supervisor(quick, store, FakeResolver(LIVE), spawn=spawn_sleep)

    try:
        await supervisor.start("alice")
        await wait_until(
            lambda: supervisor.status()["stall_count"] >= 1,
            timeout_s=15,
        )
        assert supervisor.status()["last_restart_reason"] == "stall"
    finally:
        await supervisor.stop()


async def test_auto_refresh_reconnects(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    quick = replace(
        settings,
        auto_refresh_minutes=0.05,
        stall_timeout_seconds=60,
        watchdog_interval_seconds=2,
        backoff_initial_seconds=1,
    )
    supervisor = Supervisor(quick, store, FakeResolver(LIVE), spawn=spawn_sleep)

    try:
        await supervisor.start("alice")
        await wait_until(
            lambda: supervisor.status()["refresh_count"] >= 1
            and supervisor.status()["sessions"] >= 2,
            timeout_s=30,
        )
        assert supervisor.status()["last_restart_reason"] == "auto-refresh"
    finally:
        await supervisor.stop()


class SequenceResolver:
    """Returns the given streams in order, then reports offline forever."""

    def __init__(self, streams: list[ResolvedStream]) -> None:
        self._streams = list(streams)
        self.calls = 0

    async def resolve(self, streamer) -> ResolvedStream:
        self.calls += 1
        if self._streams:
            return self._streams.pop(0)
        return OFFLINE


class RecordingSpawn:
    """Records ffmpeg calls and returns short- or long-lived sleep processes."""

    def __init__(self, live_seconds: str = "0.2") -> None:
        self.live_seconds = live_seconds
        self.calls: list[list[str]] = []
        self.live_calls = 0
        self.pause_calls = 0
        self.pause_processes: list[asyncio.subprocess.Process] = []

    async def __call__(self, args: list[str]) -> asyncio.subprocess.Process:
        self.calls.append(list(args))
        pause_call = "lavfi" in args
        if pause_call:
            self.pause_calls += 1
            seconds = "60"
        else:
            self.live_calls += 1
            seconds = self.live_seconds
        process = await asyncio.create_subprocess_exec(
            "sleep",
            seconds,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        if pause_call:
            self.pause_processes.append(process)
        return process


async def test_pause_screen_starts_after_stream_ends(
    settings, store, monkeypatch
) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    resolver = SequenceResolver([LIVE, OFFLINE])
    spawn = RecordingSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.start("alice")
        await wait_until(
            lambda: supervisor.status()["state"] == "offline"
            and supervisor.status()["pause_active"],
            timeout_s=8,
        )
        status = supervisor.status()
        assert status["pause_since"] is not None
        assert spawn.pause_calls == 1
        assert "color=c=0x101418:s=1920x1080:r=30" in spawn.calls[-1]
    finally:
        await supervisor.stop()

    assert supervisor.status()["pause_active"] is False
    assert spawn.pause_processes[0].returncode is not None


async def test_stop_does_not_start_pause_screen(settings, store, monkeypatch) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    spawn = RecordingSpawn(live_seconds="60")
    supervisor = Supervisor(settings, store, FakeResolver(LIVE), spawn=spawn)

    try:
        await supervisor.start("alice")
        await wait_until(lambda: supervisor.status()["state"] == "live")
        await supervisor.stop()
    finally:
        await supervisor.stop()

    assert spawn.pause_calls == 0


async def test_disabled_pause_screen_does_not_start(
    settings, store, monkeypatch
) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    quick = replace(settings, pause_screen_enabled=False)
    spawn = RecordingSpawn()
    supervisor = Supervisor(
        quick, store, SequenceResolver([LIVE, OFFLINE]), spawn=spawn
    )

    try:
        await supervisor.start("alice")
        await wait_until(
            lambda: supervisor.status()["state"] == "offline", timeout_s=8
        )
        assert supervisor.status()["pause_active"] is False
        assert spawn.pause_calls == 0
    finally:
        await supervisor.stop()


async def test_pause_screen_stops_before_live_reconnect(
    settings, store, monkeypatch
) -> None:
    monkeypatch.setenv("ALICE_YT_KEY", "alice-key")
    resolver = SequenceResolver([LIVE, OFFLINE, LIVE])
    spawn = RecordingSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.start("alice")
        await wait_until(lambda: spawn.pause_calls >= 1, timeout_s=8)
        await wait_until(lambda: spawn.live_calls >= 2, timeout_s=12)
        assert spawn.pause_processes[0].returncode is not None
    finally:
        await supervisor.stop()



PLAYLIST_SHARED = """
playlist:
  name: Abendprogramm
  auto_advance: true
  wrap: true
  advance_delay_seconds: 0
  youtube_key: shared-key
  channels: [alice, bob]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key: bob-key
"""

PLAYLIST_PER_KEY = """
playlist:
  name: Abendprogramm
  auto_advance: true
  wrap: true
  advance_delay_seconds: 0
  channels: [alice, bob]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key: bob-key
"""

PLAYLIST_WITH_DISABLED = """
playlist:
  name: Abendprogramm
  auto_advance: true
  wrap: true
  advance_delay_seconds: 0
  channels: [alice, bob, carol]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key: bob-key
    enabled: false
  - id: carol
    name: Carol
    tiktok: carol
    youtube_key: carol-key
"""


class MapResolver:
    """Returns queued streams per streamer, then reports offline."""

    def __init__(self, streams: dict[str, list[ResolvedStream]]) -> None:
        self._streams = {key: list(value) for key, value in streams.items()}
        self.calls: list[str] = []

    async def resolve(self, streamer) -> ResolvedStream:
        self.calls.append(streamer.id)
        queue = self._streams.get(streamer.id, [])
        if queue:
            return queue.pop(0)
        return OFFLINE


class SequenceSpawn:
    """Live ffmpeg calls use queued durations; pause calls stay alive."""

    def __init__(self, live_seconds: list[str] | None = None) -> None:
        self._live_seconds = list(live_seconds or [])
        self.calls: list[list[str]] = []
        self.live_calls = 0
        self.pause_calls = 0

    async def __call__(self, args: list[str]) -> asyncio.subprocess.Process:
        self.calls.append(list(args))
        if "lavfi" in args:
            self.pause_calls += 1
            seconds = "60"
        else:
            self.live_calls += 1
            seconds = self._live_seconds.pop(0) if self._live_seconds else "60"

        return await asyncio.create_subprocess_exec(
            "sleep",
            seconds,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    def live_arguments(self) -> list[list[str]]:
        return [args for args in self.calls if "lavfi" not in args]


def make_store(settings, body: str) -> StreamerStore:
    settings.streamers_file.write_text(body, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


def playlist_view(status: dict, playlist_id: str = "main") -> dict:
    return next(
        item for item in status["playlists"] if item["id"] == playlist_id
    )


async def test_playlist_advances_on_offline_with_shared_key(
    settings,
) -> None:
    store = make_store(settings, PLAYLIST_SHARED)
    resolver = MapResolver({"alice": [OFFLINE], "bob": [LIVE]})
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("main")
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        status = supervisor.status()
        assert playlist_view(status)["mode_active"] is True
        assert playlist_view(status)["switch_count"] == 1
        assert playlist_view(status)["last_switch_reason"] == "offline"
        assert spawn.pause_calls >= 1

        bob_args = spawn.live_arguments()[-1]
        assert bob_args[-1].endswith("/shared-key")
        assert "alice-key" not in " ".join(bob_args)
        assert "bob-key" not in " ".join(bob_args)
    finally:
        await supervisor.stop()


async def test_playlist_advances_after_clean_stream_end(settings) -> None:
    store = make_store(settings, PLAYLIST_PER_KEY)
    resolver = MapResolver({"alice": [LIVE], "bob": [LIVE]})
    spawn = SequenceSpawn(["0.2"])
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("main")
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        status = supervisor.status()
        assert playlist_view(status)["switch_count"] == 1
        assert playlist_view(status)["last_switch_reason"] == "Stream beendet"
        assert spawn.live_arguments()[-1][-1].endswith("/bob-key")
    finally:
        await supervisor.stop()


async def test_manual_start_does_not_advance_playlist(settings) -> None:
    store = make_store(settings, PLAYLIST_PER_KEY)
    resolver = MapResolver({"alice": [LIVE, OFFLINE], "bob": [LIVE]})
    spawn = SequenceSpawn(["0.2"])
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.start("alice")
        await wait_until(
            lambda: supervisor.status()["state"] == "offline"
            and supervisor.status()["active"] == "alice",
            timeout_s=10,
        )

        status = supervisor.status()
        assert playlist_view(status)["mode_active"] is False
        assert playlist_view(status)["switch_count"] == 0
        assert "bob" not in resolver.calls
        assert spawn.pause_calls >= 1
    finally:
        await supervisor.stop()


async def test_playlist_skips_disabled_channel_and_stops_cycle(settings) -> None:
    store = make_store(settings, PLAYLIST_WITH_DISABLED)
    resolver = MapResolver({"alice": [OFFLINE], "bob": [LIVE], "carol": [OFFLINE]})
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("main")
        await wait_until(
            lambda: supervisor.status()["active"] == "carol"
            and supervisor.status()["pause_active"] is True,
            timeout_s=10,
        )

        status = supervisor.status()
        assert playlist_view(status)["switch_count"] == 1
        assert "bob" not in resolver.calls
        assert spawn.pause_calls >= 1
        assert status["state"] == "offline"
    finally:
        await supervisor.stop()


async def test_playlist_auto_advance_disabled_stays_on_channel(settings) -> None:
    body = PLAYLIST_PER_KEY.replace("auto_advance: true", "auto_advance: false")
    store = make_store(settings, body)
    resolver = MapResolver({"alice": [OFFLINE], "bob": [LIVE]})
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("main")
        await wait_until(
            lambda: supervisor.status()["state"] == "offline"
            and supervisor.status()["pause_active"] is True,
            timeout_s=10,
        )

        status = supervisor.status()
        assert status["active"] == "alice"
        assert playlist_view(status)["mode_active"] is True
        assert playlist_view(status)["switch_count"] == 0
        assert "bob" not in resolver.calls
    finally:
        await supervisor.stop()


async def test_playlist_next_and_previous_wrap(settings) -> None:
    store = make_store(settings, PLAYLIST_PER_KEY)
    resolver = MapResolver(
        {"alice": [LIVE, LIVE, LIVE], "bob": [LIVE, LIVE, LIVE]}
    )
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_step("main", 1)
        await wait_until(
            lambda: supervisor.status()["active"] == "alice"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        await supervisor.playlist_step("main", 1)
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        await supervisor.playlist_step("main", 1)
        await wait_until(
            lambda: supervisor.status()["active"] == "alice"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        await supervisor.playlist_step("main", -1)
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )
        assert playlist_view(supervisor.status())["number"] == 2
    finally:
        await supervisor.stop()


async def test_auto_refresh_does_not_switch_channel_in_playlist_mode(
    settings,
) -> None:
    store = make_store(settings, PLAYLIST_PER_KEY)
    resolver = MapResolver({"alice": [LIVE], "bob": [LIVE]})
    spawn = SequenceSpawn()
    quick = replace(
        settings,
        auto_refresh_minutes=0.02,
        stall_timeout_seconds=60,
        watchdog_interval_seconds=2,
    )
    supervisor = Supervisor(quick, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("main")
        await wait_until(
            lambda: supervisor.status()["refresh_count"] >= 1,
            timeout_s=20,
        )

        status = supervisor.status()
        assert status["active"] == "alice"
        assert playlist_view(status)["switch_count"] == 0
        assert "bob" not in resolver.calls
    finally:
        await supervisor.stop()


MULTI_PLAYLISTS = """
playlists:
  - id: abend
    name: Abendprogramm
    auto_advance: true
    wrap: true
    advance_delay_seconds: 0
    channels: [alice, bob]
  - id: nacht
    name: Nachtprogramm
    auto_advance: true
    wrap: true
    advance_delay_seconds: 0
    channels: [carol]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key: bob-key
  - id: carol
    name: Carol
    tiktok: carol
    youtube_key: carol-key
"""


async def test_multiple_playlists_switch_independently(settings) -> None:
    store = make_store(settings, MULTI_PLAYLISTS)
    resolver = MapResolver({"alice": [LIVE], "carol": [LIVE]})
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("abend")
        await wait_until(
            lambda: supervisor.status()["active"] == "alice"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        status = supervisor.status()
        assert status["active_playlist"] == "abend"
        assert playlist_view(status, "abend")["mode_active"] is True
        assert playlist_view(status, "nacht")["mode_active"] is False

        await supervisor.playlist_tune("nacht", "carol")
        await wait_until(
            lambda: supervisor.status()["active"] == "carol"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        status = supervisor.status()
        assert status["active_playlist"] == "nacht"
        assert playlist_view(status, "abend")["mode_active"] is False
        assert playlist_view(status, "nacht")["mode_active"] is True
    finally:
        await supervisor.stop()


async def test_auto_advance_stays_inside_active_playlist(settings) -> None:
    store = make_store(settings, MULTI_PLAYLISTS)
    resolver = MapResolver({"alice": [OFFLINE], "bob": [LIVE], "carol": [LIVE]})
    spawn = SequenceSpawn()
    supervisor = Supervisor(settings, store, resolver, spawn=spawn)

    try:
        await supervisor.playlist_play("abend")
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )

        status = supervisor.status()
        assert status["active_playlist"] == "abend"
        assert resolver.calls == ["alice", "bob"]
        assert playlist_view(status, "abend")["switch_count"] == 1
    finally:
        await supervisor.stop()


def test_playlist_end_deadline_duration() -> None:
    now = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    playlist = PlaylistConfig(end_after_minutes=90)

    assert playlist_end_deadline(playlist, now) == now + timedelta(minutes=90)


def test_playlist_end_deadline_daily_time() -> None:
    playlist = PlaylistConfig(end_time="23:00")
    evening = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    late = datetime(2026, 10, 30, 23, 30, tzinfo=UTC)

    assert playlist_end_deadline(playlist, evening) == datetime(
        2026, 10, 30, 23, 0, tzinfo=UTC
    )
    assert playlist_end_deadline(playlist, late) == datetime(
        2026, 10, 31, 23, 0, tzinfo=UTC
    )
    assert playlist_end_deadline(PlaylistConfig(), evening) is None


async def test_playlist_end_stops_the_stream(settings) -> None:
    body = PLAYLIST_PER_KEY.replace(
        "advance_delay_seconds: 0",
        "advance_delay_seconds: 0\n  end_after_minutes: 60",
    )
    store = make_store(settings, body)
    supervisor = Supervisor(
        settings, store, FakeResolver(LIVE), spawn=SequenceSpawn()
    )

    try:
        await supervisor.playlist_play("main")
        await wait_until(lambda: supervisor.status()["state"] == "live")

        playlist = store.playlist("main")
        await supervisor._cancel_playlist_end()
        ends_at = datetime.now(UTC).isoformat(timespec="seconds")
        deadline = time.monotonic() + 0.05
        supervisor._playlist_end_at = ends_at
        supervisor._playlist_end_at_monotonic = deadline
        supervisor._start_playlist_end(
            playlist,
            supervisor._session_token,
            keep_at=ends_at,
            keep_monotonic=deadline,
        )

        await wait_until(
            lambda: supervisor.status()["state"] == "idle", timeout_s=5
        )
        status = supervisor.status()
        assert status["active"] is None
        assert status["active_playlist"] is None
    finally:
        await supervisor.stop()


async def test_manual_step_keeps_playlist_end_deadline(settings) -> None:
    body = PLAYLIST_PER_KEY.replace(
        "advance_delay_seconds: 0",
        "advance_delay_seconds: 0\n  end_after_minutes: 60",
    )
    store = make_store(settings, body)
    supervisor = Supervisor(
        settings, store, FakeResolver(LIVE), spawn=SequenceSpawn()
    )

    try:
        await supervisor.playlist_play("main")
        await wait_until(lambda: supervisor.status()["state"] == "live")

        playlist = store.playlist("main")
        await supervisor._cancel_playlist_end()
        ends_at = "2026-10-30T23:00:00+00:00"
        deadline = time.monotonic() + 300
        supervisor._playlist_end_at = ends_at
        supervisor._playlist_end_at_monotonic = deadline
        supervisor._start_playlist_end(
            playlist,
            supervisor._session_token,
            keep_at=ends_at,
            keep_monotonic=deadline,
        )

        await supervisor.playlist_step("main", 1)

        assert supervisor.status()["active"] == "bob"
        assert supervisor._playlist_end_at == ends_at
        assert supervisor._playlist_end_at_monotonic == pytest.approx(deadline)
    finally:
        await supervisor.stop()


async def test_manual_switch_uses_pause_bridge_for_shared_output(settings) -> None:
    store = make_store(settings, PLAYLIST_SHARED)
    spawn = SequenceSpawn()
    supervisor = Supervisor(
        settings, store, FakeResolver(LIVE), spawn=spawn
    )

    try:
        await supervisor.playlist_play("main")
        await wait_until(lambda: supervisor.status()["state"] == "live")

        await supervisor.playlist_step("main", 1)

        assert spawn.pause_calls >= 1
        await wait_until(
            lambda: supervisor.status()["active"] == "bob"
            and supervisor.status()["state"] == "live",
            timeout_s=10,
        )
    finally:
        await supervisor.stop()

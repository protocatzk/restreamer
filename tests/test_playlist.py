from __future__ import annotations

from datetime import UTC, datetime

from app.models import PlaylistConfig, StreamerConfig
from app.playlist import (
    PlaylistController,
    playlist_end_deadline,
)
from app.store import StreamerStore
from app.timing import FakeClock

CONFIG = """
playlists:
  - id: main
    name: Hauptprogramm
    auto_advance: true
    wrap: true
    advance_delay_seconds: 0
    end_after_minutes: 90
    channels: [alice, bob, carol, dave]
  - id: linear
    name: Linear
    auto_advance: true
    wrap: false
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
  - id: carol
    name: Carol
    tiktok: carol
    youtube_key: carol-key
  - id: dave
    name: Dave
    tiktok: dave
    youtube_key: dave-key
    enabled: false
"""


def make_store(settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


def make_controller(
    store: StreamerStore,
    clock: FakeClock,
    *,
    can_output=None,
    record=None,
) -> PlaylistController:
    kwargs = {}
    if can_output is not None:
        kwargs["can_output"] = can_output
    if record is not None:
        kwargs["record"] = record
    return PlaylistController(store, clock, **kwargs)


def playlist(store: StreamerStore, playlist_id: str = "main") -> PlaylistConfig:
    result = store.playlist(playlist_id)
    assert result is not None
    return result


def test_next_target_steps_and_wraps_in_both_directions(settings) -> None:
    store = make_store(settings)
    controller = make_controller(store, FakeClock())
    main = playlist(store)

    assert controller.next_target(main, 1, from_id="alice").id == "bob"
    assert controller.next_target(main, 1, from_id="carol").id == "alice"
    assert controller.next_target(main, -1, from_id="alice").id == "carol"
    # dave is disabled and therefore never selected.
    assert controller.next_target(main, 1, from_id="alice", skip_offline=True).id == "bob"


def test_next_target_respects_wrap_false(settings) -> None:
    store = make_store(settings)
    controller = make_controller(store, FakeClock())
    linear = playlist(store, "linear")

    assert controller.next_target(linear, 1, from_id="alice").id == "bob"
    assert controller.next_target(linear, 1, from_id="carol") is None
    assert controller.next_target(linear, -1, from_id="alice") is None


def test_advance_target_skips_channels_offline_in_this_cycle(settings) -> None:
    store = make_store(settings)
    controller = make_controller(store, FakeClock())
    main = playlist(store)

    controller.offline_cycle.update({"bob"})

    assert controller.advance_target("alice", main).target.id == "carol"


def test_advance_target_clears_cycle_when_everything_failed(settings) -> None:
    store = make_store(settings)
    controller = make_controller(store, FakeClock())
    main = playlist(store)
    controller.offline_cycle.update({"alice", "bob", "carol"})

    decision = controller.advance_target("alice", main)

    assert decision.target is None
    assert decision.cycle_cleared is True
    assert controller.offline_cycle == set()


def test_advance_target_skips_channels_without_output_key(settings) -> None:
    store = make_store(settings)
    messages: list[str] = []
    main = playlist(store)

    def can_output(cfg: StreamerConfig, _playlist: PlaylistConfig) -> bool:
        return cfg.id != "bob"

    controller = make_controller(
        store, FakeClock(), can_output=can_output, record=messages.append
    )

    decision = controller.advance_target("alice", main)

    assert decision.target.id == "carol"
    assert any("Bob" in message for message in messages)


def test_preferred_cooldown_is_driven_by_the_injected_clock(settings) -> None:
    store = make_store(settings)
    clock = FakeClock(start=100.0)
    controller = make_controller(store, clock)
    bob = store.get("bob")
    assert bob is not None

    assert controller.next_preferred_target([bob], now=1.0) is bob

    controller.note_attempt("bob")
    assert controller.next_preferred_target([bob]) is None

    clock.advance(300.0)
    assert controller.next_preferred_target([bob]) is bob


def test_preferred_return_captures_and_restores_playlist_state(settings) -> None:
    store = make_store(settings)
    clock = FakeClock()
    controller = make_controller(store, clock)
    main = playlist(store)

    controller.begin(main.id)
    controller.end_at = "2026-01-01T22:00:00+01:00"
    controller.end_at_monotonic = 1234.5
    controller.record_switch("offline")

    record = controller.begin_preferred_return("alice", "bob")

    assert record.playlist_mode is True
    assert record.playlist_id == main.id
    assert record.playlist_end_at == controller.end_at
    assert record.playlist_end_at_monotonic == 1234.5
    assert controller.take_preferred_return("alice") is None
    assert controller.take_preferred_return("bob") is record
    assert controller.preferred_return is None


def test_begin_and_reset_clear_session_but_keep_cooldowns(settings) -> None:
    store = make_store(settings)
    controller = make_controller(store, FakeClock())
    main = playlist(store)

    controller.note_attempt("bob", now=10.0)
    controller.begin(main.id)
    controller.record_switch("offline")
    controller.end_at = "soon"

    assert controller.mode is True
    assert controller.switch_count == 1

    controller.reset()

    assert controller.mode is False
    assert controller.playlist_id is None
    assert controller.switch_count == 0
    assert controller.end_at is None
    assert controller._auto_switch_attempts == {"bob": 10.0}


def test_playlist_end_deadline_duration_and_daily_time() -> None:
    now = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)

    assert playlist_end_deadline(
        PlaylistConfig(end_after_minutes=90), now
    ) == datetime(2026, 10, 30, 21, 30, tzinfo=UTC)

    assert playlist_end_deadline(
        PlaylistConfig(end_time="22:00"), now
    ) == datetime(2026, 10, 30, 22, 0, tzinfo=UTC)

    assert playlist_end_deadline(
        PlaylistConfig(end_time="19:00"), now
    ) == datetime(2026, 10, 31, 19, 0, tzinfo=UTC)

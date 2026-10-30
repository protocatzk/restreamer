from __future__ import annotations

from pathlib import Path

import pytest

from app.models import PlaylistConfig, StreamerConfig
from app.store import (
    ConfigError,
    StreamerStore,
    build_playlist,
    build_streamer,
    load_config,
    load_streamers,
    normalize_tiktok,
)

GOOD = """
streamers:
  - id: alice
    name: Alice
    tiktok: "@alice"
    youtube_key_env: ALICE_YT_KEY
    video_mode: copy
    auto_start: true
"""


def test_normalize_tiktok() -> None:
    assert normalize_tiktok("@alice") == "alice"
    assert normalize_tiktok("alice") == "alice"
    assert normalize_tiktok("https://www.tiktok.com/@alice/live") == "alice"


def test_load_streamers(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(GOOD, encoding="utf-8")

    streamers = load_streamers(path)

    assert len(streamers) == 1
    assert streamers[0].id == "alice"
    assert streamers[0].tiktok == "alice"
    assert streamers[0].auto_start is True
    assert streamers[0].live_url == "https://www.tiktok.com/@alice/live"


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
streamers:
  - id: alice
    name: A
    tiktok: alice1
    youtube_key_env: A_KEY
  - id: alice
    name: B
    tiktok: alice2
    youtube_key_env: B_KEY
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Doppelte"):
        load_streamers(path)


def test_invalid_video_mode_rejected(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
    video_mode: turbo
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="video_mode"):
        load_streamers(path)


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nicht gefunden"):
        load_streamers(tmp_path / "missing.yml")


def test_store_keeps_last_good_config_on_error(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(GOOD, encoding="utf-8")
    store = StreamerStore(path)
    assert [s.id for s in store.streamers] == ["alice"]

    path.write_text("streamers: not-a-list", encoding="utf-8")
    store.reload()

    assert [s.id for s in store.streamers] == ["alice"]
    assert "Liste" in store.error


def test_store_save_roundtrip_with_key(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    streamer = StreamerConfig(
        id="alice",
        name="Alice",
        tiktok="alice",
        youtube_key="secret-key",
        video_mode="reencode",
        auto_start=True,
    )

    store.save([streamer])

    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = load_streamers(path)[0]
    assert loaded.youtube_key == "secret-key"
    assert loaded.video_mode == "reencode"
    assert loaded.auto_start is True


def test_store_upsert_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    store.save(
        [
            StreamerConfig(
                id="alice",
                name="Alice",
                tiktok="alice",
                youtube_key="key-1",
            )
        ]
    )

    store.upsert(
        StreamerConfig(
            id="alice",
            name="Alice neu",
            tiktok="alice",
            youtube_key="key-2",
        ),
        original_id="alice",
    )

    assert store.get("alice").name == "Alice neu"
    assert store.get("alice").youtube_key == "key-2"

    store.delete("alice")
    assert store.streamers == []


def test_build_streamer_requires_key_or_env() -> None:
    with pytest.raises(ConfigError, match="youtube_key"):
        build_streamer({"id": "alice", "name": "Alice", "tiktok": "alice"})


def test_build_streamer_keeps_existing_key_on_blank_update() -> None:
    existing = StreamerConfig(
        id="alice",
        name="Alice",
        tiktok="alice",
        youtube_key="old-key",
    )

    updated = build_streamer(
        {"name": "Alice 2", "youtube_key": ""}, existing=existing
    )

    assert updated.youtube_key == "old-key"
    assert updated.name == "Alice 2"


def test_build_streamer_can_clear_key() -> None:
    existing = StreamerConfig(
        id="alice",
        name="Alice",
        tiktok="alice",
        youtube_key="old-key",
        youtube_key_env="ALICE_YT_KEY",
    )

    updated = build_streamer({"clear_youtube_key": True}, existing=existing)

    assert updated.youtube_key == ""
    assert updated.youtube_key_env == "ALICE_YT_KEY"


PLAYLIST = """
playlist:
  id: abend
  name: Abendprogramm
  auto_advance: true
  wrap: true
  advance_delay_seconds: 3
  youtube_key: shared-key
  channels:
    - bob
    - alice
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key_env: BOB_YT_KEY
"""


def test_load_playlist(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(PLAYLIST, encoding="utf-8")

    streamers, playlists = load_config(path)
    playlist = playlists[0]

    assert [streamer.id for streamer in streamers] == ["alice", "bob"]
    assert len(playlists) == 1
    assert playlist.id == "abend"
    assert playlist.name == "Abendprogramm"
    assert playlist.channels == ("bob", "alice")
    assert playlist.youtube_key == "shared-key"
    assert playlist.auto_advance is True
    assert playlist.wrap is True
    assert playlist.advance_delay_seconds == 3


def test_playlist_defaults_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(GOOD, encoding="utf-8")

    _, playlists = load_config(path)

    assert playlists == []


def test_playlist_unknown_channel_rejected(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
playlist:
  channels: [alice, nobody]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unbekannten Streamer"):
        load_config(path)


def test_playlist_duplicate_channel_rejected(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
playlist:
  channels: [alice, alice]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="doppelter Sender"):
        load_config(path)


def test_store_save_playlist_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    streamers = [
        StreamerConfig(
            id="alice",
            name="Alice",
            tiktok="alice",
            youtube_key="alice-key",
        ),
        StreamerConfig(
            id="bob",
            name="Bob",
            tiktok="bob",
            youtube_key="bob-key",
        ),
    ]
    playlist = PlaylistConfig(
        id="nacht",
        name="Nachtprogramm",
        channels=("bob", "alice"),
        youtube_key="shared-key",
        auto_advance=False,
        wrap=False,
        advance_delay_seconds=0,
    )

    store.save(streamers, [playlist])

    assert (path.stat().st_mode & 0o777) == 0o600
    loaded, loaded_playlists = load_config(path)
    assert [item.id for item in loaded] == ["alice", "bob"]
    assert loaded_playlists == [playlist]


def test_delete_streamer_removes_playlist_channel(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    store.save(
        [
            StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a"),
            StreamerConfig(id="bob", name="Bob", tiktok="bob", youtube_key="b"),
        ],
        [PlaylistConfig(channels=("alice", "bob"))],
    )

    store.delete("alice")

    assert store.playlists[0].channels == ("bob",)
    _, loaded_playlists = load_config(path)
    assert loaded_playlists[0].channels == ("bob",)


def test_rename_streamer_updates_playlist_channel(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    store.save(
        [
            StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a"),
            StreamerConfig(id="bob", name="Bob", tiktok="bob", youtube_key="b"),
        ],
        [PlaylistConfig(channels=("alice", "bob"))],
    )

    store.upsert(
        StreamerConfig(id="alice2", name="Alice", tiktok="alice", youtube_key="a"),
        original_id="alice",
    )

    assert store.playlists[0].channels == ("alice2", "bob")


def test_build_playlist_validates_channels() -> None:
    streamers = [
        StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a")
    ]

    with pytest.raises(ConfigError, match="unbekannten Streamer"):
        build_playlist({"channels": ["nobody"]}, streamers=streamers)

    playlist = build_playlist(
        {
            "name": "Programm",
            "channels": ["alice"],
            "auto_advance": False,
            "wrap": False,
            "advance_delay_seconds": 5,
        },
        streamers=streamers,
    )
    assert playlist.channels == ("alice",)
    assert playlist.auto_advance is False
    assert playlist.advance_delay_seconds == 5


def test_build_playlist_keeps_or_clears_shared_key() -> None:
    existing = PlaylistConfig(channels=("alice",), youtube_key="old-key")
    streamers = [
        StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a")
    ]

    kept = build_playlist({"name": "Neu"}, existing=existing, streamers=streamers)
    assert kept.youtube_key == "old-key"

    cleared = build_playlist(
        {"clear_youtube_key": True}, existing=existing, streamers=streamers
    )
    assert cleared.youtube_key == ""


def test_load_multiple_playlists(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
playlists:
  - id: abend
    name: Abendprogramm
    channels: [alice]
  - name: Nachtprogramm
    channels: [alice, bob]
    auto_advance: false
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key: bob-key
""",
        encoding="utf-8",
    )

    _, playlists = load_config(path)

    assert [playlist.id for playlist in playlists] == [
        "abend",
        "nachtprogramm",
    ]
    assert playlists[1].channels == ("alice", "bob")
    assert playlists[1].auto_advance is False


def test_duplicate_playlist_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
playlists:
  - id: abend
    name: Eins
  - id: abend
    name: Zwei
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Doppelte Senderlisten-ID"):
        load_config(path)


def test_legacy_and_playlists_block_are_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(
        """
playlist:
  channels: [alice]
playlists:
  - id: abend
    channels: [alice]
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key: alice-key
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="nicht beides"):
        load_config(path)


def test_save_multiple_playlists_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    store = StreamerStore(path)
    streamers = [
        StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a"),
        StreamerConfig(id="bob", name="Bob", tiktok="bob", youtube_key="b"),
    ]
    playlists = [
        PlaylistConfig(id="abend", name="Abend", channels=("alice",)),
        PlaylistConfig(id="nacht", name="Nacht", channels=("bob", "alice")),
    ]

    store.save(streamers, playlists)

    assert [playlist.id for playlist in store.playlists] == ["abend", "nacht"]
    _, loaded = load_config(path)
    assert loaded == playlists


def test_build_playlist_generates_id_from_name() -> None:
    streamers = [
        StreamerConfig(id="alice", name="Alice", tiktok="alice", youtube_key="a")
    ]

    playlist = build_playlist(
        {"name": "Abend-Programm", "channels": ["alice"]},
        streamers=streamers,
    )

    assert playlist.id == "abend-programm"


def test_legacy_playlist_is_migrated_on_save(tmp_path: Path) -> None:
    path = tmp_path / "streamers.yml"
    path.write_text(PLAYLIST, encoding="utf-8")
    store = StreamerStore(path)

    store.save(store.streamers, store.playlists)

    document = path.read_text(encoding="utf-8")
    assert "playlists:" in document
    assert "playlist:" not in document.replace("playlists:", "")
    loaded, playlists = load_config(path)
    assert [streamer.id for streamer in loaded] == ["alice", "bob"]
    assert [playlist.id for playlist in playlists] == ["abend"]

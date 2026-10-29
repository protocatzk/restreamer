from __future__ import annotations

from pathlib import Path

import pytest

from app.models import StreamerConfig
from app.store import (
    ConfigError,
    StreamerStore,
    build_streamer,
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

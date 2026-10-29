from __future__ import annotations

from pathlib import Path

import pytest

from app.store import ConfigError, StreamerStore, load_streamers, normalize_tiktok

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

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    logs = tmp_path / "logs"
    logs.mkdir()
    return Settings(
        streamers_file=tmp_path / "streamers.yml",
        logs_dir=logs,
        cookies_file=tmp_path / "cookies.txt",
        offline_poll_seconds=1,
        backoff_initial_seconds=1,
        backoff_max_seconds=2,
        terminate_timeout_seconds=2,
    )


def write_streamers(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def streamers_file(tmp_path: Path) -> Path:
    path = tmp_path / "streamers.yml"
    write_streamers(
        path,
        """
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key_env: BOB_YT_KEY
    video_mode: reencode
""",
    )
    return path

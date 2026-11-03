from __future__ import annotations

import socket
from pathlib import Path

import pytest

from app.settings import Settings


def free_tcp_port_pair() -> int:
    """Return a free port whose successor is free as well (relay + output)."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port >= 65535:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as next_sock:
            try:
                next_sock.bind(("127.0.0.1", port + 1))
            except OSError:
                continue
        return port
    raise RuntimeError("kein freies TCP-Portpaar gefunden")


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
        relay_port=free_tcp_port_pair(),
        relay_delay_seconds=1,
        online_check_enabled=False,
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

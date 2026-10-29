from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import CommandResult, StreamerConfig
from app.resolver import ResolveError, Resolver, is_h264, select_format
from app.settings import Settings

STREAMER = StreamerConfig(
    id="alice",
    name="Alice",
    tiktok="alice",
    youtube_key_env="ALICE_YT_KEY",
)


class StubRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.args: list[str] | None = None

    async def __call__(self, args: list[str], timeout_s: int) -> CommandResult:
        self.args = args
        return self.result


def payload(data: dict, code: int = 0, stderr: str = "") -> CommandResult:
    return CommandResult(
        returncode=code, stdout=json.dumps(data), stderr=stderr
    )


def test_is_h264() -> None:
    assert is_h264("avc1.64001f")
    assert is_h264("h264")
    assert not is_h264("hvc1.1.6.L120")
    assert not is_h264("")


def test_select_format_prefers_h264() -> None:
    info = {
        "formats": [
            {"url": "hevc-url", "vcodec": "hvc1.1.6", "height": 1080},
            {"url": "h264-url", "vcodec": "avc1.64001f", "height": 720},
        ]
    }
    url, vcodec, height, needs_reencode = select_format(info)

    assert url == "h264-url"
    assert vcodec == "avc1.64001f"
    assert height == 720
    assert needs_reencode is False


def test_select_format_hevc_only_marks_reencode() -> None:
    info = {
        "formats": [
            {"url": "hevc-url", "vcodec": "hvc1.1.6", "height": 1080},
        ]
    }
    url, _vcodec, _height, needs_reencode = select_format(info)

    assert url == "hevc-url"
    assert needs_reencode is True


async def test_resolve_live(settings: Settings) -> None:
    runner = StubRunner(
        payload(
            {
                "is_live": True,
                "live_status": "is_live",
                "title": "Alice live",
                "formats": [
                    {"url": "https://cdn/h264.m3u8", "vcodec": "avc1", "height": 720}
                ],
            }
        )
    )
    resolver = Resolver(settings, runner=runner)

    stream = await resolver.resolve(STREAMER)

    assert stream.is_live is True
    assert stream.url == "https://cdn/h264.m3u8"
    assert stream.title == "Alice live"
    assert runner.args is not None
    assert runner.args[-1] == "https://www.tiktok.com/@alice/live"


async def test_resolve_offline_by_json(settings: Settings) -> None:
    runner = StubRunner(
        payload({"is_live": False, "live_status": "not_live", "formats": []})
    )
    resolver = Resolver(settings, runner=runner)

    stream = await resolver.resolve(STREAMER)

    assert stream.is_live is False
    assert stream.url == ""


async def test_resolve_offline_by_stderr(settings: Settings) -> None:
    runner = StubRunner(
        CommandResult(
            returncode=1,
            stdout="",
            stderr="ERROR: [TikTokLive] alice is not currently live",
        )
    )
    resolver = Resolver(settings, runner=runner)

    stream = await resolver.resolve(STREAMER)

    assert stream.is_live is False


async def test_resolve_error(settings: Settings) -> None:
    runner = StubRunner(
        CommandResult(returncode=1, stdout="", stderr="ERROR: HTTP Error 403")
    )
    resolver = Resolver(settings, runner=runner)

    with pytest.raises(ResolveError, match="403"):
        await resolver.resolve(STREAMER)


async def test_cookies_are_passed(settings: Settings, tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cookie_settings = settings.__class__(
        **{**settings.__dict__, "cookies_file": cookies}
    )
    runner = StubRunner(payload({"is_live": False, "formats": []}))
    resolver = Resolver(cookie_settings, runner=runner)

    await resolver.resolve(STREAMER)

    assert runner.args is not None
    assert "--cookies" in runner.args
    assert str(cookies) in runner.args

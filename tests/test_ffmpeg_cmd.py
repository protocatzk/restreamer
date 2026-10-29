from __future__ import annotations

from dataclasses import replace

import pytest

from app.ffmpeg_cmd import build_ffmpeg_command, build_youtube_urls
from app.models import ResolvedStream, StreamerConfig
from app.settings import Settings

STREAMER = StreamerConfig(
    id="alice",
    name="Alice",
    tiktok="alice",
    youtube_key_env="ALICE_YT_KEY",
)
STREAM = ResolvedStream(
    url="https://example.invalid/live.m3u8",
    is_live=True,
    source_vcodec="avc1.64001f",
    height=720,
)


def test_copy_mode_uses_stream_copy(settings: Settings) -> None:
    args = build_ffmpeg_command(STREAMER, STREAM, "key-123", settings)

    codec_index = args.index("-c:v")
    assert args[codec_index + 1] == "copy"
    assert "libx264" not in args
    assert args[-1] == "rtmp://a.rtmp.youtube.com/live2/key-123"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-af") + 1] == "aresample=async=1:first_pts=0"
    assert "+genpts" in args


def test_reencode_mode(settings: Settings) -> None:
    reencode_streamer = replace(STREAMER, video_mode="reencode")
    args = build_ffmpeg_command(reencode_streamer, STREAM, "key-123", settings)

    codec_index = args.index("-c:v")
    assert args[codec_index + 1] == "libx264"
    assert "veryfast" in args


def test_hevc_source_forces_reencode(settings: Settings) -> None:
    hevc_stream = replace(STREAM, needs_reencode=True, source_vcodec="hvc1")
    args = build_ffmpeg_command(STREAMER, hevc_stream, "key-123", settings)

    assert args[args.index("-c:v") + 1] == "libx264"


def test_backup_url(settings: Settings) -> None:
    backup_settings = replace(settings, youtube_backup=True)
    urls = build_youtube_urls(backup_settings, "key-123")

    assert urls == [
        "rtmp://a.rtmp.youtube.com/live2/key-123",
        "rtmp://b.rtmp.youtube.com/live2/key-123?backup=1",
    ]


def test_full_rtmp_key_is_used_as_is(settings: Settings) -> None:
    full = "rtmp://custom.example/live2/abc"
    assert build_youtube_urls(settings, full) == [full]


def test_empty_key_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="leer"):
        build_youtube_urls(settings, "  ")


def test_empty_stream_url_rejected(settings: Settings) -> None:
    empty = replace(STREAM, url="")
    with pytest.raises(ValueError, match="Stream-URL"):
        build_ffmpeg_command(STREAMER, empty, "key", settings)

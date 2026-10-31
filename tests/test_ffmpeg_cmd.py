from __future__ import annotations

from dataclasses import replace

import pytest

from app.ffmpeg_cmd import (
    build_output_command,
    build_relay_input_url,
    build_relay_source_url,
    build_source_command,
    build_youtube_urls,
)
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


def test_source_copy_publishes_mpegts_to_relay(settings: Settings) -> None:
    args = build_source_command(STREAMER, STREAM, settings)

    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert args[args.index("-f") + 1] == "mpegts"
    assert args[-1] == build_relay_source_url(settings)
    assert not any("rtmp://" in arg for arg in args)


def test_source_force_reencode_uses_libx264(settings: Settings) -> None:
    args = build_source_command(
        STREAMER, STREAM, settings, force_reencode=True
    )

    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[-1] == build_relay_source_url(settings)


def test_source_timestamp_offset_is_applied(settings: Settings) -> None:
    args = build_source_command(
        STREAMER, STREAM, settings, timestamp_offset=12.5
    )

    assert args[args.index("-output_ts_offset") + 1] == "12.500"
    assert args[-1] == build_relay_source_url(settings)


def test_output_audio_resampler_uses_fixed_sample_rate(
    settings: Settings,
) -> None:
    args = build_output_command(settings, "key-123")

    assert (
        args[args.index("-af") + 1]
        == "aresample=async=1:osr=44100:osf=fltp:ochl=stereo"
    )


def test_output_normalises_video_and_pushes_youtube(
    settings: Settings,
) -> None:
    args = build_output_command(settings, "key-123")

    assert args[args.index("-i") + 1] == build_relay_input_url(settings)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("flv") - 1] == "-f"
    assert args[-1] == "rtmp://a.rtmp.youtube.com/live2/key-123"

    video_filter = args[args.index("-vf") + 1]
    assert "scale=1280:720" in video_filter
    assert "fps=30" in video_filter
    assert "pad=1280:720" in video_filter
    assert args[args.index("-fps_mode") + 1] == "cfr"


def test_output_custom_canvas(settings: Settings) -> None:
    portrait = replace(
        settings, output_width=720, output_height=1280, output_fps=25
    )
    args = build_output_command(portrait, "key-123")

    video_filter = args[args.index("-vf") + 1]
    assert "scale=720:1280" in video_filter
    assert "fps=25" in video_filter


def test_backup_url(settings: Settings) -> None:
    backup_settings = replace(settings, youtube_backup=True)
    urls = build_youtube_urls(backup_settings, "key-123")

    assert urls == [
        "rtmp://a.rtmp.youtube.com/live2/key-123",
        "rtmp://b.rtmp.youtube.com/live2/key-123?backup=1",
    ]


def test_backup_output_gets_its_own_format_options(
    settings: Settings,
) -> None:
    backup_settings = replace(settings, youtube_backup=True)
    args = build_output_command(backup_settings, "key-123")

    primary = "rtmp://a.rtmp.youtube.com/live2/key-123"
    backup = "rtmp://b.rtmp.youtube.com/live2/key-123?backup=1"
    assert primary in args
    assert backup in args

    for url in (primary, backup):
        index = args.index(url)
        # The output options must directly precede each URL.
        assert args[index - 1] == "1024"
        assert "-f" in args[index - 40 : index]
        assert args[index - 40 : index].count("-c:v") == 1


def test_full_rtmp_key_is_used_as_is(settings: Settings) -> None:
    full = "rtmp://custom.example/live2/abc"
    assert build_youtube_urls(settings, full) == [full]


def test_empty_key_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="leer"):
        build_youtube_urls(settings, "  ")


def test_empty_stream_url_rejected(settings: Settings) -> None:
    empty = replace(STREAM, url="")
    with pytest.raises(ValueError, match="Stream-URL"):
        build_source_command(STREAMER, empty, settings)

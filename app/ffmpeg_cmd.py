from __future__ import annotations

from app.models import ResolvedStream, StreamerConfig
from app.settings import Settings


def build_youtube_urls(settings: Settings, key: str) -> list[str]:
    """Return primary (and optionally backup) YouTube RTMP URLs."""
    value = (key or "").strip()
    if not value:
        raise ValueError("YouTube-Stream-Key ist leer")

    if value.startswith(("rtmp://", "rtmps://")):
        return [value]

    base = settings.youtube_rtmp_url.rstrip("/")
    urls = [f"{base}/{value}"]
    if settings.youtube_backup:
        backup_base = "rtmp://b.rtmp.youtube.com/live2"
        urls.append(f"{backup_base}/{value}?backup=1")
    return urls


def build_ffmpeg_command(
    streamer: StreamerConfig,
    stream: ResolvedStream,
    key: str,
    settings: Settings,
    force_reencode: bool = False,
) -> list[str]:
    if not stream.url:
        raise ValueError("Keine Stream-URL vorhanden")

    reencode = (
        force_reencode
        or streamer.video_mode == "reencode"
        or stream.needs_reencode
    )

    args = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        settings.ffmpeg_loglevel,
        "-progress",
        "pipe:1",
        "-nostats",
        "-stats_period",
        "5",
        "-fflags",
        "+genpts",
        "-rw_timeout",
        "15000000",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-i",
        stream.url,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
    ]

    if reencode:
        bitrate = settings.reencode_video_bitrate
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            "5000k",
            "-g",
            "60",
            "-keyint_min",
            "60",
            "-sc_threshold",
            "0",
        ]
    else:
        args += ["-c:v", "copy"]

    args += [
        "-af",
        "aresample=async=1:first_pts=0",
        "-c:a",
        "aac",
        "-b:a",
        settings.audio_bitrate,
        "-ar",
        "44100",
        "-ac",
        "2",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        "-max_muxing_queue_size",
        "1024",
    ]

    args += list(streamer.ffmpeg_args)
    args += build_youtube_urls(settings, key)
    return args


def command_uses_reencode(
    streamer: StreamerConfig,
    stream: ResolvedStream,
    force_reencode: bool = False,
) -> bool:
    return (
        force_reencode
        or streamer.video_mode == "reencode"
        or stream.needs_reencode
    )

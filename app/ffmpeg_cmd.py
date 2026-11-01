from __future__ import annotations

import re

from app.models import ResolvedStream, StreamerConfig
from app.settings import Settings

RELAY_HOST = "127.0.0.1"
BITRATE_VALUE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)([kKmM]?)$")
SOURCE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
SOURCE_HEADERS = "Referer: https://www.tiktok.com/\r\nAccept: */*\r\n"


def multiply_bitrate(bitrate: str, factor: float) -> str:
    """Return a bitrate string scaled by ``factor`` (e.g. 4000k -> 8000k)."""
    match = BITRATE_VALUE_PATTERN.match(str(bitrate).strip())
    if not match:
        return bitrate
    value = float(match.group(1)) * factor
    suffix = match.group(2)
    if suffix:
        return f"{value:g}{suffix}"
    return str(int(value)) if value.is_integer() else f"{value:g}"


def bitrate_to_kbps(bitrate: str) -> float | None:
    """Convert a settings bitrate string to kbit/s (e.g. 4M -> 4000)."""
    match = BITRATE_VALUE_PATTERN.match(str(bitrate).strip())
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "m":
        return value * 1000
    if suffix == "":
        return value / 1000
    return value


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


def build_relay_source_url(settings: Settings) -> str:
    """TCP endpoint that source/pause ffmpeg write MPEG-TS bytes to."""
    return f"tcp://{RELAY_HOST}:{settings.relay_port}?tcp_nodelay=1"


def build_relay_input_url(settings: Settings) -> str:
    """TCP endpoint the long-lived output ffmpeg reads MPEG-TS bytes from.

    The delay buffer listens on ``relay_port`` and forwards data to
    ``relay_port + 1`` after the configured delay.
    """
    return f"tcp://{RELAY_HOST}:{settings.relay_port + 1}?tcp_nodelay=1"


def build_source_command(
    streamer: StreamerConfig,
    stream: ResolvedStream,
    settings: Settings,
    force_reencode: bool = False,
    timestamp_offset: float = 0.0,
) -> list[str]:
    """Read the TikTok stream and publish it as MPEG-TS on the local relay.

    The long-lived output ffmpeg is responsible for normalising video and
    audio.  The source therefore simply copies the codec data whenever
    possible.  ``force_reencode`` is the fallback for codecs that cannot be
    carried in MPEG-TS (detected from ffmpeg's log by the supervisor).
    """
    if not stream.url:
        raise ValueError("Keine Stream-URL vorhanden")

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
        "-user_agent",
        SOURCE_USER_AGENT,
        "-headers",
        SOURCE_HEADERS,
        "-i",
        stream.url,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
    ]

    if force_reencode:
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
            multiply_bitrate(bitrate, 2),
            "-g",
            "60",
            "-keyint_min",
            "60",
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            settings.audio_bitrate,
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
    else:
        args += ["-c:v", "copy", "-c:a", "copy"]

    args += [
        "-f",
        "mpegts",
        "-mpegts_flags",
        "+resend_headers",
        "-flush_packets",
        "1",
        "-muxdelay",
        "0",
    ]
    if timestamp_offset > 0:
        args += ["-output_ts_offset", f"{timestamp_offset:.3f}"]
    args.append(build_relay_source_url(settings))
    return args


def build_output_command(
    settings: Settings,
    key: str,
    ffmpeg_args: tuple[str, ...] = (),
) -> list[str]:
    """Build the long-lived ffmpeg that pushes the relay to YouTube.

    Video and audio are decoded and re-encoded at a fixed canvas/framerate.
    This gives YouTube a continuous, monotonic timeline even when the
    underlying TikTok source reconnects, changes resolution, or delivers
    non-monotonic DTS values.
    """
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
        "+genpts+discardcorrupt",
        "-thread_queue_size",
        "512",
        "-probesize",
        "1000000",
        "-analyzeduration",
        "1000000",
        "-f",
        "mpegts",
        "-i",
        build_relay_input_url(settings),
    ]

    width = settings.output_width
    height = settings.output_height
    stretch = 1 + settings.output_stretch_percent / 100
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"scale=w='trunc(min({width}\\,iw*{stretch:.3f})/2)*2':h=ih,"
        "setsar=1,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={settings.output_fps},format=yuv420p"
    )

    bitrate = settings.reencode_video_bitrate
    gop = max(1, settings.output_fps * 2)
    output_options = [
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-vf",
        video_filter,
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        multiply_bitrate(bitrate, 2),
        "-x264-params",
        "nal-hrd=cbr:filler=1",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-af",
        "aresample=async=1:osr=48000:osf=fltp:ochl=stereo",
        "-c:a",
        "aac",
        "-b:a",
        settings.audio_bitrate,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-f",
        "flv",
        "-flvflags",
        "no_duration_filesize",
        "-max_muxing_queue_size",
        "1024",
    ]
    output_options += list(ffmpeg_args)

    # Output options apply to the next output file only.  Repeat them for
    # every URL so the optional backup ingest also gets -f flv.
    for url in build_youtube_urls(settings, key):
        args += output_options
        args.append(url)
    return args

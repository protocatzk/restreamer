from __future__ import annotations

from pathlib import Path

from app.ffmpeg_cmd import build_youtube_urls
from app.settings import Settings

PAUSE_WIDTH = 1920
PAUSE_HEIGHT = 1080
PAUSE_FRAMERATE = 30
PAUSE_TEXT_FILENAME = "pause-screen-text.txt"
PAUSE_SUBTEXT_FILENAME = "pause-screen-subtext.txt"

# Schriften, die im Debian-Image (fonts-dejavu-core) bzw. auf üblichen
# Linux-Systemen vorhanden sind. Der erste Treffer wird verwendet.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def find_pause_font(explicit: str = "") -> str | None:
    """Return a usable font file for the pause screen, or None."""
    candidates: list[str] = []
    if explicit.strip():
        candidates.append(explicit.strip())
    candidates.extend(FONT_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return None


def _filter_quote(value: str) -> str:
    """Quote a value for an ffmpeg filtergraph option."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _normalize_color(value: str) -> str:
    color = value.strip()
    if color.startswith(("#", "0x", "0X")):
        color = color[2:] if color[:2].lower() == "0x" else color[1:]
    return f"0x{color}"


def write_pause_texts(
    settings: Settings, text: str, subtext: str
) -> tuple[Path, Path]:
    """Write the configured pause texts to files used by drawtext.

    drawtext's ``text=`` option needs several levels of escaping, which is
    error-prone for user supplied text. ``textfile=`` avoids that entirely.
    """
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    text_path = settings.logs_dir / PAUSE_TEXT_FILENAME
    subtext_path = settings.logs_dir / PAUSE_SUBTEXT_FILENAME
    text_path.write_text(text, encoding="utf-8")
    subtext_path.write_text(subtext, encoding="utf-8")
    return text_path, subtext_path


def build_pause_command(
    settings: Settings,
    key: str,
    text_path: Path,
    subtext_path: Path,
) -> list[str]:
    """Build the ffmpeg command that streams the generated pause screen."""
    args = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        settings.ffmpeg_loglevel,
        "-nostats",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"color=c={_normalize_color(settings.pause_screen_background)}"
        f":s={PAUSE_WIDTH}x{PAUSE_HEIGHT}:r={PAUSE_FRAMERATE}",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]

    font = find_pause_font(settings.pause_screen_font)
    if font is not None:
        fontfile = _filter_quote(font)
        filters = []
        if settings.pause_screen_text.strip():
            filters.append(
                "drawtext="
                f"fontfile={fontfile}"
                f":textfile={_filter_quote(str(text_path))}"
                ":fontcolor=white:fontsize=96"
                ":x=(w-text_w)/2:y=(h-text_h)/2-70"
                ":shadowcolor=black@0.6:shadowx=3:shadowy=3"
            )
        if settings.pause_screen_subtext.strip():
            filters.append(
                "drawtext="
                f"fontfile={fontfile}"
                f":textfile={_filter_quote(str(subtext_path))}"
                ":fontcolor=0xB8C0CC:fontsize=42"
                ":x=(w-text_w)/2:y=(h-text_h)/2+70"
                ":shadowcolor=black@0.6:shadowx=2:shadowy=2"
            )
        if filters:
            args += ["-vf", ",".join(filters)]

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
        settings.reencode_video_bitrate,
        "-maxrate",
        settings.reencode_video_bitrate,
        "-bufsize",
        "5000k",
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
    args += build_youtube_urls(settings, key)
    return args

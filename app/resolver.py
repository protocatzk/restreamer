from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app.models import CommandResult, ResolvedStream, StreamerConfig
from app.settings import Settings

logger = logging.getLogger(__name__)

OFFLINE_MARKERS = (
    "is not currently live",
    "not currently live",
    "user is not live",
    "currently offline",
    "live event will begin",
    "this live event has ended",
    "the live event has ended",
    "no live stream",
    "livestream is not",
)


class ResolveError(RuntimeError):
    """yt-dlp could not resolve a playable URL."""


Runner = Callable[[list[str], int], Awaitable[CommandResult]]


async def run_command(args: list[str], timeout_s: int) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):
            await process.wait()
        raise ResolveError(f"yt-dlp Timeout nach {timeout_s}s") from exc
    except asyncio.CancelledError:
        # Beim Wechsel/Stopp darf kein yt-dlp-Prozess zurückbleiben.
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):
            await process.wait()
        raise

    return CommandResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def is_h264(vcodec: str) -> bool:
    codec = (vcodec or "").lower()
    return codec.startswith(("avc1", "avc3", "h264"))


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def format_height(fmt: dict) -> int:
    height = fmt.get("height")
    if isinstance(height, (int, float)) and height > 0:
        return int(height)
    resolution = str(fmt.get("resolution") or "")
    match = re.match(r"(\d+)x(\d+)", resolution)
    if match:
        return max(int(match.group(1)), int(match.group(2)))
    match = re.search(r"(\d+)p", resolution)
    return int(match.group(1)) if match else 0


def is_audio_only(fmt: dict) -> bool:
    if str(fmt.get("vcodec") or "").lower() == "none":
        return True
    if str(fmt.get("format_id") or "").lower().endswith("-ao"):
        return True
    return "only_audio=1" in str(fmt.get("url") or "")


def select_format(info: dict) -> tuple[str, str, int | None, bool]:
    """Pick the best H.264 video format if available, otherwise the best one.

    Handles both HLS/DASH formats with height metadata and TikTok FLV formats
    that only expose ``resolution`` and a quality rank.
    """
    formats = [
        fmt
        for fmt in (info.get("formats") or [])
        if fmt.get("url") and not is_audio_only(fmt)
    ]

    if formats:
        h264_formats = [fmt for fmt in formats if is_h264(str(fmt.get("vcodec") or ""))]
        pool = h264_formats or formats
        best = max(
            pool,
            key=lambda fmt: (
                1 if format_height(fmt) else 0,
                format_height(fmt),
                _number(fmt.get("quality")),
                _number(fmt.get("tbr")),
                _number(fmt.get("abr")),
            ),
        )
        vcodec = str(best.get("vcodec") or "")
        height = format_height(best) or None
        return (
            str(best["url"]),
            vcodec,
            height,
            bool(vcodec) and not is_h264(vcodec),
        )

    url = info.get("url")
    if not url:
        raise ResolveError("yt-dlp lieferte weder Formate noch eine Stream-URL")

    vcodec = str(info.get("vcodec") or "")
    height = format_height(info) or None
    return str(url), vcodec, height, bool(vcodec) and not is_h264(vcodec)


class Resolver:
    def __init__(self, settings: Settings, runner: Runner | None = None) -> None:
        self.settings = settings
        self._runner = runner or run_command

    def build_args(self, streamer: StreamerConfig) -> list[str]:
        args = [
            self.settings.ytdlp_bin,
            "--dump-single-json",
            "--no-warnings",
            "--no-playlist",
            "--no-progress",
            "--socket-timeout",
            "20",
            "--retries",
            "3",
        ]

        cookies = self.settings.cookies_file
        if cookies.exists() and cookies.stat().st_size > 0:
            args += ["--cookies", str(cookies)]

        args += list(streamer.ytdlp_args)
        args += list(self.settings.ytdlp_extra_args)
        args.append(streamer.live_url)
        return args

    async def resolve(self, streamer: StreamerConfig) -> ResolvedStream:
        args = self.build_args(streamer)
        logger.debug("yt-dlp: %s", " ".join(args))

        result = await self._runner(args, self.settings.resolve_timeout_seconds)

        if result.returncode != 0:
            combined = f"{result.stderr}\n{result.stdout}".lower()
            if any(marker in combined for marker in OFFLINE_MARKERS):
                return ResolvedStream(url="", is_live=False)
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = " | ".join(detail[-3:]) if detail else "unbekannter Fehler"
            raise ResolveError(f"yt-dlp Fehler (rc={result.returncode}): {tail[:500]}")

        payload = result.stdout.strip()
        start = payload.find("{")
        if start < 0:
            raise ResolveError("yt-dlp lieferte kein JSON")
        try:
            info = json.loads(payload[start:])
        except json.JSONDecodeError as exc:
            raise ResolveError(f"yt-dlp JSON nicht lesbar: {exc}") from exc

        live_status = str(info.get("live_status") or "")
        is_live = info.get("is_live")
        if is_live is None:
            is_live = live_status == "is_live" if live_status else True

        if not is_live or live_status in {"not_live", "post_live", "was_live"}:
            return ResolvedStream(url="", is_live=False)

        url, vcodec, height, needs_reencode = select_format(info)
        title = str(info.get("title") or info.get("description") or "")[:200]

        return ResolvedStream(
            url=url,
            title=title,
            is_live=True,
            needs_reencode=needs_reencode,
            source_vcodec=vcodec,
            height=height,
        )

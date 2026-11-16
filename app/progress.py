"""Parsing and state for ffmpeg progress output."""

from __future__ import annotations

import re

from app.timing import Clock

PROGRESS_BITRATE_PATTERN = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*([kmg]?)bits/s$", re.IGNORECASE
)

CODEC_ERROR_MARKERS = (
    "could not find tag for codec",
    "codec not currently supported",
    "unsupported codec",
    "unknown encoder",
    "encoder not found",
    "invalid codec",
)


def parse_progress_bitrate(value: str) -> float | None:
    """Parse ffmpeg's ``bitrate=3999.5kbits/s`` progress value into kbit/s."""
    match = PROGRESS_BITRATE_PATTERN.match(value.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        return number * 1000
    if unit == "g":
        return number * 1_000_000
    if unit == "":
        return number / 1000
    return number


def looks_like_codec_error(lines: list[str]) -> bool:
    text = "\n".join(lines).lower()
    return any(marker in text for marker in CODEC_ERROR_MARKERS)


class ProgressTracker:
    """Accumulate ffmpeg progress values for source and output processes."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self.last_progress: float | None = None
        self.source_progress: float | None = None
        self.source_out_time_us = -1
        self.source_total_size = -1
        self.last_out_time_us = -1
        self.last_total_size = -1
        self.out_time_us = 0
        self.speed = ""
        self.bitrate_kbps: float | None = None

    @staticmethod
    def _pair(line: str) -> tuple[str, str] | None:
        if not line or "=" not in line:
            return None
        key, _, value = line.partition("=")
        return key.strip(), value.strip()

    def feed_source(self, line: str) -> None:
        pair = self._pair(line)
        if pair is None:
            return
        key, value = pair
        if key == "out_time_us":
            try:
                microseconds = int(value)
            except ValueError:
                return
            if microseconds > self.source_out_time_us:
                self.source_out_time_us = microseconds
                self.source_progress = self.clock.monotonic()
        elif key == "total_size":
            try:
                total_size = int(value)
            except ValueError:
                return
            if total_size > self.source_total_size:
                self.source_total_size = total_size
                self.source_progress = self.clock.monotonic()

    def feed_output(self, line: str) -> None:
        pair = self._pair(line)
        if pair is None:
            return
        key, value = pair
        if key == "out_time_us":
            try:
                microseconds = int(value)
            except ValueError:
                return
            if microseconds > self.last_out_time_us:
                self.last_out_time_us = microseconds
                self.out_time_us = microseconds
                self.last_progress = self.clock.monotonic()
        elif key == "total_size":
            try:
                total_size = int(value)
            except ValueError:
                return
            if total_size > self.last_total_size:
                self.last_total_size = total_size
                self.last_progress = self.clock.monotonic()
        elif key == "speed":
            self.speed = "" if value in {"", "N/A"} else value
        elif key == "bitrate":
            kbps = parse_progress_bitrate(value)
            if kbps is not None:
                self.bitrate_kbps = kbps

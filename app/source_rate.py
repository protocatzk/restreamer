from __future__ import annotations

from collections import deque

DEFAULT_WINDOW_SECONDS = 30.0
MIN_WINDOW_FILL = 0.8


class SourceRateMonitor:
    """Sliding-window media-rate monitor for the TikTok source.

    Samples are ``(wallclock_monotonic, source_out_time_us)`` pairs.  The rate
    is the slope between the oldest and newest sample in the window, so short
    progress bursts do not trigger false positives.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        self._samples: deque[tuple[float, int]] = deque()

    def reset(self) -> None:
        self._samples.clear()

    def update(self, now: float, out_time_us: int) -> float | None:
        if out_time_us < 0:
            return None

        self._samples.append((now, out_time_us))
        cutoff = now - self.window_seconds
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()

        if len(self._samples) < 2:
            return None

        first_at, first_out = self._samples[0]
        last_at, last_out = self._samples[-1]
        elapsed = last_at - first_at
        if elapsed < self.window_seconds * MIN_WINDOW_FILL:
            return None

        media_seconds = (last_out - first_out) / 1_000_000
        if media_seconds <= 0:
            return 0.0
        return media_seconds / elapsed

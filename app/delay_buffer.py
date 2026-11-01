from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import suppress

from app.ffmpeg_cmd import RELAY_HOST
from app.settings import Settings

logger = logging.getLogger(__name__)

MAX_BUFFER_BYTES = 32 * 1024 * 1024
MAX_BUFFER_SECONDS = 120
FORWARD_INTERVAL = 0.02
MAX_PACKETS_PER_TICK = 500
PACING_WINDOW_SECONDS = 60.0
PACING_MIN_WINDOW_SECONDS = 5.0
MAX_PACING_BURST_BYTES = 256 * 1024
MIN_BUFFER_SECONDS = 2.0
MAX_EXTRA_DELAY_SECONDS = 5.0
TCP_CHUNK_SIZE = 65536


class TokenBucket:
    """Small byte-based token bucket used to smooth MPEG-TS output."""

    def __init__(self, capacity: float) -> None:
        self.capacity = capacity
        self._tokens = 0.0
        self._last_refill: float | None = None

    def reset(self) -> None:
        self._tokens = 0.0
        self._last_refill = None

    def refill(self, now: float, rate_bps: float) -> None:
        if self._last_refill is None:
            self._last_refill = now
            return
        elapsed = max(0.0, min(now - self._last_refill, 1.0))
        self._last_refill = now
        self._tokens = min(
            self.capacity, self._tokens + rate_bps / 8.0 * elapsed
        )

    def take(self, size: int) -> bool:
        if self._tokens < size:
            return False
        self._tokens -= size
        return True


class DelayBuffer:
    """Lossless local TCP delay line between source and output ffmpeg.

    The source keeps writing to ``relay_port``.  Data is held for
    ``relay_delay_seconds`` and then forwarded to ``relay_port + 1`` where the
    long-lived output ffmpeg listens.  The output is paced at the measured
    source bitrate so the encoder does not receive TikTok's network bursts.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._queue: deque[tuple[float, bytes]] = deque()
        self._bytes = 0
        self._arrivals: deque[tuple[float, int]] = deque()
        self._bucket = TokenBucket(MAX_PACING_BURST_BYTES)
        self._pacing_rate_bps: float | None = None
        self._source_server: asyncio.Server | None = None
        self._output_server: asyncio.Server | None = None
        self._source_writer: asyncio.StreamWriter | None = None
        self._output_writer: asyncio.StreamWriter | None = None
        self._forward_task: asyncio.Task | None = None
        self._dropped = 0
        self._source_addr = (RELAY_HOST, settings.relay_port)
        self._output_addr = (RELAY_HOST, settings.relay_port + 1)

    async def start(self) -> None:
        self._source_server = await asyncio.start_server(
            self._handle_source,
            *self._source_addr,
            limit=1024 * 1024,
        )
        self._output_server = await asyncio.start_server(
            self._handle_output,
            *self._output_addr,
            limit=1024 * 1024,
        )
        self._forward_task = asyncio.create_task(self._forward_loop())
        logger.info(
            "Delay-Puffer: %d s, TCP %s:%d -> %s:%d",
            self.settings.relay_delay_seconds,
            *self._source_addr,
            *self._output_addr,
        )

    async def stop(self) -> None:
        if self._forward_task is not None:
            self._forward_task.cancel()
            try:
                await self._forward_task
            except asyncio.CancelledError:
                pass
            self._forward_task = None

        for writer in (self._source_writer, self._output_writer):
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
        self._source_writer = None
        self._output_writer = None

        for server in (self._source_server, self._output_server):
            if server is not None:
                server.close()
                with suppress(Exception):
                    await server.wait_closed()
        self._source_server = None
        self._output_server = None

    def reset(self) -> None:
        if self._queue:
            logger.info("Delay-Puffer: geleert (%d Bytes)", self._bytes)
        self._queue.clear()
        self._bytes = 0
        self._arrivals.clear()
        self._bucket.reset()
        self._pacing_rate_bps = None

    def buffer_seconds(self) -> float:
        if not self._queue:
            return 0.0
        return max(0.0, time.monotonic() - self._queue[0][0])

    def enqueue(self, data: bytes) -> None:
        now = time.monotonic()
        self._queue.append((now, data))
        self._bytes += len(data)
        self._arrivals.append((now, len(data)))
        cutoff = now - PACING_WINDOW_SECONDS
        while self._arrivals and self._arrivals[0][0] < cutoff:
            self._arrivals.popleft()
        self._trim(now)

        if self._dropped:
            logger.warning(
                "Delay-Puffer: %d alte Bytes verworfen (Puffer voll)",
                self._dropped,
            )
            self._dropped = 0

    def _average_rate_bps(self, now: float) -> float | None:
        cutoff = now - PACING_WINDOW_SECONDS
        while self._arrivals and self._arrivals[0][0] < cutoff:
            self._arrivals.popleft()
        if len(self._arrivals) < 2:
            return None
        span = self._arrivals[-1][0] - self._arrivals[0][0]
        if span < PACING_MIN_WINDOW_SECONDS:
            return None
        total_bytes = sum(size for _, size in self._arrivals)
        return total_bytes * 8.0 / span

    def _trim(self, now: float) -> None:
        delay = max(0, self.settings.relay_delay_seconds)
        max_seconds = max(MAX_BUFFER_SECONDS, delay + 30)
        while self._queue and (
            self._bytes > MAX_BUFFER_BYTES
            or self._queue[0][0] < now - max_seconds
        ):
            _, old = self._queue.popleft()
            self._bytes -= len(old)
            self._dropped += len(old)

    async def _handle_source(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        old = self._source_writer
        if old is not None and old is not writer:
            old.close()
        self._source_writer = writer
        try:
            while True:
                data = await reader.read(TCP_CHUNK_SIZE)
                if not data:
                    break
                self.enqueue(data)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            if self._source_writer is writer:
                self._source_writer = None

    async def _handle_output(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        old = self._output_writer
        if old is not None and old is not writer:
            old.close()
        self._output_writer = writer
        try:
            # The output ffmpeg only reads; this waits for disconnect/EOF.
            while await reader.read(4096):
                pass
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            if self._output_writer is writer:
                self._output_writer = None

    async def _forward_loop(self) -> None:
        while True:
            now = time.monotonic()
            delay = max(0, int(self.settings.relay_delay_seconds))

            average = self._average_rate_bps(now)
            if average is not None:
                if self._pacing_rate_bps is None:
                    self._pacing_rate_bps = average
                else:
                    self._pacing_rate_bps = (
                        0.9 * self._pacing_rate_bps + 0.1 * average
                    )

            rate = self._pacing_rate_bps
            if rate is not None:
                self._bucket.refill(now, rate)

            buffer_age = self.buffer_seconds()
            bypass = (
                delay <= 0
                or rate is None
                or buffer_age < MIN_BUFFER_SECONDS
                or buffer_age > delay + MAX_EXTRA_DELAY_SECONDS
            )

            writer = self._output_writer
            if writer is not None and writer.is_closing():
                writer = None
                self._output_writer = None

            cutoff = now - delay
            sent = 0
            wrote = False
            while (
                self._queue
                and self._queue[0][0] <= cutoff
                and sent < MAX_PACKETS_PER_TICK
            ):
                _, data = self._queue[0]
                if not bypass and not self._bucket.take(len(data)):
                    break
                self._queue.popleft()
                self._bytes -= len(data)
                if writer is not None:
                    writer.write(data)
                    wrote = True
                sent += 1

            if wrote and writer is not None:
                try:
                    await writer.drain()
                except (ConnectionError, RuntimeError):
                    writer.close()
                    if self._output_writer is writer:
                        self._output_writer = None

            await asyncio.sleep(FORWARD_INTERVAL)

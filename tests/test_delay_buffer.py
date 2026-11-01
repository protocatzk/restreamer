from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import replace

import pytest

from app.delay_buffer import DelayBuffer, TokenBucket
from app.settings import Settings


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def free_port_pair() -> int:
    for _ in range(50):
        port = free_tcp_port()
        if port >= 65535:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port + 1))
            except OSError:
                continue
        return port
    raise RuntimeError("kein freies TCP-Portpaar gefunden")


async def open_pair(port: int):
    source_reader, source_writer = await asyncio.open_connection(
        "127.0.0.1", port
    )
    output_reader, output_writer = await asyncio.open_connection(
        "127.0.0.1", port + 1
    )
    await asyncio.sleep(0.05)
    return source_reader, source_writer, output_reader, output_writer


async def close_writers(*writers: asyncio.StreamWriter) -> None:
    for writer in writers:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def test_token_bucket_refills_and_takes() -> None:
    bucket = TokenBucket(1000)

    bucket.refill(0.0, 800)
    bucket.refill(1.0, 800)
    assert bucket.take(100) is True
    assert bucket.take(1) is False

    bucket.refill(2.0, 800)
    assert bucket.take(100) is True


async def test_delay_buffer_holds_packets(settings: Settings) -> None:
    port = free_port_pair()
    buffer = DelayBuffer(
        replace(settings, relay_port=port, relay_delay_seconds=1)
    )
    await buffer.start()
    _source_reader, source_writer, output_reader, output_writer = (
        await open_pair(port)
    )

    try:
        started = time.monotonic()
        source_writer.write(b"first")
        await source_writer.drain()

        await asyncio.sleep(0.3)
        assert buffer.buffer_seconds() > 0.2
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(output_reader.read(5), timeout=0.2)

        data = await asyncio.wait_for(output_reader.read(5), timeout=1.5)
        assert data == b"first"
        assert time.monotonic() - started >= 0.9
    finally:
        await close_writers(source_writer, output_writer)
        await buffer.stop()


async def test_delay_buffer_reset_drops_buffered_packets(
    settings: Settings,
) -> None:
    port = free_port_pair()
    buffer = DelayBuffer(
        replace(settings, relay_port=port, relay_delay_seconds=1)
    )
    await buffer.start()
    _source_reader, source_writer, output_reader, output_writer = (
        await open_pair(port)
    )

    try:
        source_writer.write(b"drop")
        await source_writer.drain()
        await asyncio.sleep(0.1)
        buffer.reset()

        await asyncio.sleep(1.1)
        assert buffer.buffer_seconds() == 0.0
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(output_reader.read(5), timeout=0.2)
    finally:
        await close_writers(source_writer, output_writer)
        await buffer.stop()


async def test_delay_buffer_zero_delay_forwards_immediately(
    settings: Settings,
) -> None:
    port = free_port_pair()
    buffer = DelayBuffer(
        replace(settings, relay_port=port, relay_delay_seconds=0)
    )
    await buffer.start()
    _source_reader, source_writer, output_reader, output_writer = (
        await open_pair(port)
    )

    try:
        source_writer.write(b"now")
        await source_writer.drain()
        data = await asyncio.wait_for(output_reader.read(3), timeout=0.5)
        assert data == b"now"
    finally:
        await close_writers(source_writer, output_writer)
        await buffer.stop()


async def test_delay_buffer_output_reconnect(settings: Settings) -> None:
    port = free_port_pair()
    buffer = DelayBuffer(
        replace(settings, relay_port=port, relay_delay_seconds=1)
    )
    await buffer.start()
    _source_reader, source_writer, output_reader, output_writer = (
        await open_pair(port)
    )
    output_writer2: asyncio.StreamWriter | None = None

    try:
        source_writer.write(b"one")
        await source_writer.drain()
        assert (
            await asyncio.wait_for(output_reader.read(3), timeout=1.5)
            == b"one"
        )

        output_writer.close()
        await output_writer.wait_closed()
        await asyncio.sleep(0.1)

        output_reader2, output_writer2 = await asyncio.open_connection(
            "127.0.0.1", port + 1
        )
        await asyncio.sleep(0.05)

        source_writer.write(b"two")
        await source_writer.drain()
        assert (
            await asyncio.wait_for(output_reader2.read(3), timeout=1.5)
            == b"two"
        )
    finally:
        writers = [source_writer, output_writer]
        if output_writer2 is not None:
            writers.append(output_writer2)
        await close_writers(*writers)
        await buffer.stop()

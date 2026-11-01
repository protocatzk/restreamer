from __future__ import annotations

from app.source_rate import SourceRateMonitor


def test_realtime_source_rate() -> None:
    monitor = SourceRateMonitor(window_seconds=10)

    assert monitor.update(0.0, 0) is None
    assert monitor.update(5.0, 5_000_000) is None
    assert monitor.update(10.0, 10_000_000) == 1.0


def test_slow_source_rate() -> None:
    monitor = SourceRateMonitor(window_seconds=10)

    rate = None
    for second in range(11):
        rate = monitor.update(float(second), int(second * 0.5 * 1_000_000))

    assert rate == 0.5


def test_rate_uses_current_window() -> None:
    monitor = SourceRateMonitor(window_seconds=10)

    monitor.update(0.0, 0)
    monitor.update(10.0, 10_000_000)
    assert monitor.update(10.0, 10_000_000) == 1.0

    # A later, faster sample must be based on the current window only.
    monitor.update(15.0, 15_000_000)
    assert monitor.update(20.0, 20_000_000) == 1.0


def test_reset_clears_samples() -> None:
    monitor = SourceRateMonitor(window_seconds=10)

    monitor.update(0.0, 0)
    monitor.update(10.0, 10_000_000)
    monitor.reset()

    assert monitor.update(20.0, 20_000_000) is None

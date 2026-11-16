"""Earnings parsing, HTTP source, monitor and overlay.

The implementation lives in three focused modules; this facade keeps the
historic public import path stable for the supervisor and tests.
"""

from __future__ import annotations

from app.earnings_monitor import (
    EarningsMonitor,
    EarningsSource,
    HttpEarningsSource,
    default_summary,
)
from app.earnings_overlay import (
    EARNINGS_OVERLAY_FILENAME,
    build_overlay_filter,
    earnings_overlay_path,
    ensure_earnings_overlay_file,
    render_overlay_text,
)
from app.earnings_parse import (
    EARNINGS_PERIOD_LABELS,
    EarningsEntry,
    parse_earnings,
)
from app.settings import EARNINGS_OVERLAY_POSITIONS, EARNINGS_PERIODS

__all__ = [
    "EARNINGS_OVERLAY_FILENAME",
    "EARNINGS_PERIOD_LABELS",
    "EARNINGS_PERIODS",
    "EARNINGS_OVERLAY_POSITIONS",
    "EarningsEntry",
    "EarningsMonitor",
    "EarningsSource",
    "HttpEarningsSource",
    "build_overlay_filter",
    "default_summary",
    "ensure_earnings_overlay_file",
    "earnings_overlay_path",
    "parse_earnings",
    "render_overlay_text",
]

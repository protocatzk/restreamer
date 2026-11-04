from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.earnings import (
    EarningsEntry,
    EarningsMonitor,
    HttpEarningsSource,
    build_overlay_filter,
    default_summary,
    earnings_overlay_path,
    ensure_earnings_overlay_file,
    parse_earnings,
    render_overlay_text,
)
from app.settings import Settings
from app.store import StreamerStore

CONFIG = """
streamers:
  - id: alice
    name: Alice
    tiktok: alice
    youtube_key_env: ALICE_YT_KEY
  - id: bob
    name: Bob
    tiktok: bob
    youtube_key_env: BOB_YT_KEY
"""


class FakeEarningsSource:
    def __init__(
        self, entries: dict[str, EarningsEntry] | None = None
    ) -> None:
        self.entries = entries or {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    def fetch(self, username: str = "") -> dict[str, EarningsEntry]:
        self.calls.append(username)
        if self.error is not None:
            raise self.error
        return self.entries


def make_store(settings: Settings) -> StreamerStore:
    settings.streamers_file.write_text(CONFIG, encoding="utf-8")
    return StreamerStore(settings.streamers_file)


def _settings(tmp_path: Path, **kwargs) -> Settings:
    values = {"logs_dir": tmp_path}
    values.update(kwargs)
    return Settings(**values)


def _entry(username: str = "alice", coins: float = 1234) -> EarningsEntry:
    return EarningsEntry(
        username=username,
        display_name=username.title(),
        is_live=True,
        current_viewers=42,
        follower_count=1234,
        last_seen_at="2026-11-04T22:49:22",
        coins=coins,
        usd_net=6.17,
        eur_net=5.68,
        usd_gross=13.08,
        eur_gross=12.04,
        gift_events=12,
        gifts_sent=20,
        unique_gifters=4,
        top_gift={"gift_name": "TikTok Universe", "coins": 500, "count": 2},
        top_gifter={"sender_username": "fan", "coins": 250},
        session_info={"id": 12, "started_at": "2026-11-04T22:38:24"},
        rates={
            "usd_per_diamond": 0.005,
            "gross_usd_per_coin": 0.0106,
            "usd_to_eur_rate": 0.92,
        },
        period="today",
    )


def test_parse_toktracker_overview_computes_money() -> None:
    payload = {
        "period": "today",
        "rates": {
            "usd_per_diamond": 0.005,
            "gross_usd_per_coin": 0.0106,
            "usd_to_eur_rate": 0.92,
        },
        "streamers": [
            {
                "username": "alice",
                "display_name": "Alice Live",
                "is_live": 1,
                "current_viewers": 42,
                "follower_count": 1234,
                "last_seen_at": "2026-11-04T22:49:22",
                "total_coins": 1000,
                "gift_events_count": 7,
                "total_gifts_sent": 9,
                "unique_gifters": 3,
                "top_gift": {
                    "gift_name": "TikTok Universe",
                    "coins": 500,
                    "count": 2,
                },
                "top_gifter": {
                    "sender_username": "fan",
                    "coins": 250,
                },
                "active_session": {
                    "id": 12,
                    "started_at": "2026-11-04T22:38:24",
                    "peak_viewers": 55,
                    "last_fan_tickets": 7000,
                },
            },
            {
                "username": "@Bob",
                "total_coins": 2000,
                "usd_net": 10.0,
                "eur_net": 9.2,
            },
        ],
    }

    earnings = parse_earnings(payload)

    alice = earnings["alice"]
    assert alice.coins == 1000
    assert alice.usd_net == 5.0
    assert alice.eur_net == 4.6
    assert alice.usd_gross == 10.6
    assert alice.eur_gross == 9.75
    assert alice.gift_events == 7
    assert alice.gifts_sent == 9
    assert alice.unique_gifters == 3
    assert alice.period == "today"
    assert alice.display_name == "Alice Live"
    assert alice.is_live is True
    assert alice.current_viewers == 42
    assert alice.follower_count == 1234
    assert alice.last_seen_at == "2026-11-04T22:49:22"
    assert alice.top_gift == {
        "gift_name": "TikTok Universe",
        "coins": 500,
        "count": 2,
    }
    assert alice.top_gifter == {"sender_username": "fan", "coins": 250}
    assert alice.session_info == {
        "id": 12,
        "started_at": "2026-11-04T22:38:24",
        "peak_viewers": 55,
        "last_fan_tickets": 7000,
    }
    assert alice.rates == {
        "usd_per_diamond": 0.005,
        "gross_usd_per_coin": 0.0106,
        "usd_to_eur_rate": 0.92,
    }

    bob = earnings["bob"]
    assert bob.usd_net == 10.0
    assert bob.eur_net == 9.2
    assert bob.usd_gross == 21.2
    assert bob.eur_gross == 19.5


def test_parse_detail_payload_includes_session_values() -> None:
    payload = {
        "streamer": {"username": "alice", "is_live": True},
        "summary": {
            "total_coins": 500,
            "usd_net": 2.5,
            "eur_net": 2.3,
            "usd_gross": 5.3,
            "eur_gross": 4.88,
        },
        "sessions": [
            {
                "status": "active",
                "session_coins": 120,
                "usd_net": 0.6,
                "eur_net": 0.55,
                "started_at": "2026-11-04T22:38:24",
                "peak_viewers": 99,
            }
        ],
        "worker_diagnostics": {
            "state": "connected",
            "connected_since": "2026-11-04T22:38:24",
            "last_event_at": "2026-11-04T22:49:51",
            "last_message": "Gift erfasst",
            "reconnect_attempts": 0,
            "current_viewers": 50,
            "peak_viewers": 100,
            "room_fan_tickets": 7000,
            "gifts_received_session": 4,
            "coins_received_session": 120,
            "session_id": 12,
            "active_battle": {"battle_id": "1"},
        },
        "rates": {
            "usd_per_diamond": 0.005,
            "gross_usd_per_coin": 0.0106,
            "usd_to_eur_rate": 0.92,
        },
    }

    entry = parse_earnings(payload)["alice"]

    assert entry.coins == 500
    assert entry.net == 2.3
    assert entry.gross == 4.88
    assert entry.session_coins == 120
    assert entry.session_net == 0.55
    assert entry.session_gifts == 4
    assert entry.session_info == {
        "status": "active",
        "started_at": "2026-11-04T22:38:24",
        "peak_viewers": 99,
        "session_coins": 120,
        "usd_net": 0.6,
        "eur_net": 0.55,
        "id": 12,
        "last_fan_tickets": 7000,
        "gift_events": 4,
    }
    assert entry.diagnostics == {
        "state": "connected",
        "connected_since": "2026-11-04T22:38:24",
        "last_event_at": "2026-11-04T22:49:51",
        "last_message": "Gift erfasst",
        "reconnect_attempts": 0,
        "current_viewers": 50,
        "peak_viewers": 100,
        "room_fan_tickets": 7000,
        "gifts_received_session": 4,
        "coins_received_session": 120,
        "session_id": 12,
    }
    assert "active_battle" not in (entry.diagnostics or {})


def test_parse_skips_entries_without_username() -> None:
    payload = {
        "streamers": [
            {"display_name": "ohne Handle", "total_coins": 100},
            {"username": "alice", "total_coins": 100},
        ]
    }

    assert list(parse_earnings(payload)) == ["alice"]


def test_http_source_adds_period_and_api_key() -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append((url, headers, timeout))
        return {"streamers": [{"username": "alice", "total_coins": 100}]}

    settings = _settings(
        Path("/tmp"),
        earnings_source_url="https://example.test/overview?foo=1",
        earnings_source_api_key="secret-key",
        earnings_period="24h",
        earnings_timeout_seconds=15,
    )
    source = HttpEarningsSource(settings, fetcher=fake_fetcher)

    assert source.fetch("alice")["alice"].coins == 100
    assert calls == [
        (
            "https://example.test/overview?foo=1&period=24h",
            {
                "Accept": "application/json",
                "User-Agent": "restreamer-earnings-source/1",
                "X-API-Key": "secret-key",
            },
            15.0,
        )
    ]


def test_http_source_keeps_explicit_period() -> None:
    calls: list[str] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append(url)
        return {"streamers": []}

    settings = _settings(
        Path("/tmp"),
        earnings_source_url="https://example.test/overview?period=7d",
        earnings_period="today",
    )
    source = HttpEarningsSource(settings, fetcher=fake_fetcher)

    source.fetch("alice")

    assert calls == ["https://example.test/overview?period=7d"]


def test_http_source_replaces_username_placeholder() -> None:
    calls: list[str] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append(url)
        return {"streamer": {"username": "alice"}, "summary": {"total_coins": 1}}

    settings = _settings(
        Path("/tmp"),
        earnings_source_url=(
            "https://example.test/streamers/{username}?period=today"
        ),
    )
    source = HttpEarningsSource(settings, fetcher=fake_fetcher)

    source.fetch("https://www.tiktok.com/@Alice/live")

    assert calls == ["https://example.test/streamers/alice?period=today"]


def test_http_source_without_url_does_not_fetch() -> None:
    calls: list[str] = []

    def fake_fetcher(url: str, headers: dict[str, str], timeout: float) -> object:
        calls.append(url)
        return {}

    source = HttpEarningsSource(
        _settings(Path("/tmp")), fetcher=fake_fetcher
    )

    assert source.fetch("alice") == {}
    assert calls == []


def test_render_overlay_text() -> None:
    entry = _entry()

    text = render_overlay_text(
        "{name}: {period} · {net} · Brutto {gross} · {coins} Coins",
        entry,
        name="Alice",
        username="@alice",
        period="today",
    )

    assert text == (
        "Alice: Heute · 5,68 € · Brutto 12,04 € · 1.234 Coins"
    )


def test_render_overlay_text_exposes_extended_fields() -> None:
    entry = EarningsEntry(
        username="alice",
        display_name="Alice Live",
        is_live=True,
        current_viewers=42,
        follower_count=1234,
        last_seen_at="2026-11-04T22:49:22",
        session_coins=100,
        session_eur_net=1.23,
        top_gift={"gift_name": "TikTok Universe", "coins": 500, "count": 2},
        top_gifter={"sender_username": "fan", "coins": 250},
    )

    text = render_overlay_text(
        "{display_name}|{live}|{viewers}|{followers}|{top_gift}|"
        "{top_gifter}|{session_net}|{session_coins}|{last_seen}",
        entry,
    )

    assert text == (
        "Alice Live|live|42|1.234|TikTok Universe · 2× · 500 Coins|"
        "@fan · 250 Coins|1,23 €|100|2026-11-04T22:49:22"
    )


def test_render_overlay_text_handles_missing_values() -> None:
    entry = EarningsEntry(username="alice")

    text = render_overlay_text(
        "{period}: {net}", entry, period="all"
    )

    assert text == "Gesamt: –"


def test_build_overlay_filter_uses_reload_and_textfile(tmp_path: Path) -> None:
    font = tmp_path / "font.ttf"
    font.write_bytes(b"fake-font")
    settings = _settings(
        tmp_path,
        earnings_overlay_enabled=True,
        earnings_overlay_font=str(font),
        earnings_overlay_font_size=30,
        earnings_overlay_position="bottom-left",
    )
    ensure_earnings_overlay_file(settings)

    filter_value = build_overlay_filter(settings)

    assert filter_value is not None
    assert "drawtext=" in filter_value
    assert "reload=1" in filter_value
    assert "expansion=none" in filter_value
    assert "fontsize=30" in filter_value
    assert "x=24" in filter_value
    assert "y=h-text_h-" in filter_value
    assert str(earnings_overlay_path(settings)) in filter_value


def test_build_overlay_filter_is_disabled_by_default(tmp_path: Path) -> None:
    assert build_overlay_filter(_settings(tmp_path)) is None


async def test_monitor_selects_active_streamer_and_switches(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=True,
        earnings_source_url="https://example.test/overview",
        earnings_period="today",
        earnings_overlay_enabled=False,
    )
    store = make_store(settings)
    source = FakeEarningsSource(
        {"alice": _entry("alice", 100), "bob": _entry("bob", 200)}
    )
    active = {"id": "alice"}
    monitor = EarningsMonitor(
        settings, store, source, is_active=lambda: active["id"]
    )

    await monitor._tick()
    summary = monitor.summary()
    assert source.calls == ["alice"]
    assert summary["data"]["coins"] == 100
    assert summary["status"] == "ok"
    assert summary["view"] == "standard"
    assert summary["rates"]["usd_to_eur_rate"] == 0.92
    assert summary["data"]["display_name"] == "Alice"
    assert summary["data"]["current_viewers"] == 42
    assert summary["data"]["top_gift"]["gift_name"] == "TikTok Universe"
    assert summary["data"]["session_info"]["id"] == 12

    active["id"] = "bob"
    await monitor._tick()
    assert source.calls == ["alice", "bob"]
    assert monitor.summary()["data"]["coins"] == 200
    assert monitor.summary()["active_streamer"]["id"] == "bob"


async def test_monitor_writes_overlay_and_clears_on_inactive(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=True,
        earnings_source_url="https://example.test/overview",
        earnings_period="today",
        earnings_overlay_enabled=True,
        earnings_overlay_template="{period}: {net}",
        earnings_overlay_font_size=30,
    )
    store = make_store(settings)
    source = FakeEarningsSource({"alice": _entry("alice", 100)})
    active = {"id": "alice"}
    monitor = EarningsMonitor(
        settings, store, source, is_active=lambda: active["id"]
    )

    await monitor._tick()
    assert (
        earnings_overlay_path(settings).read_text(encoding="utf-8")
        == "Heute: 5,68 €"
    )

    active["id"] = None
    await monitor._tick()
    assert earnings_overlay_path(settings).read_text(encoding="utf-8") == ""
    assert monitor.summary()["status"] == "inactive"


async def test_monitor_keeps_last_values_on_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=True,
        earnings_source_url="https://example.test/overview",
        earnings_period="today",
    )
    store = make_store(settings)
    source = FakeEarningsSource({"alice": _entry("alice", 100)})
    monitor = EarningsMonitor(
        settings, store, source, is_active=lambda: "alice"
    )

    await monitor._tick()
    last_run_at = monitor.summary()["last_run_at"]
    source.error = RuntimeError("HTTP 503")
    monitor.request_check()
    await monitor._tick()

    summary = monitor.summary()
    assert summary["status"] == "error"
    assert "503" in summary["error"]
    assert summary["data"]["coins"] == 100
    assert summary["last_run_at"] == last_run_at


async def test_monitor_reports_missing_streamer(settings, monkeypatch) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=True,
        earnings_source_url="https://example.test/overview",
        earnings_period="today",
    )
    store = make_store(settings)
    source = FakeEarningsSource({"bob": _entry("bob")})
    monitor = EarningsMonitor(
        settings, store, source, is_active=lambda: "alice"
    )

    await monitor._tick()

    summary = monitor.summary()
    assert summary["status"] == "missing"
    assert summary["data"] is None
    assert summary["missing"] is True
    assert summary["last_run_at"] is not None


async def test_monitor_does_not_fetch_without_active_streamer(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=True,
        earnings_source_url="https://example.test/overview",
    )
    store = make_store(settings)
    source = FakeEarningsSource()
    monitor = EarningsMonitor(settings, store, source, is_active=lambda: None)

    await monitor._tick()

    assert source.calls == []
    assert monitor.summary()["status"] == "inactive"


async def test_monitor_does_not_fetch_when_disabled(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr("app.earnings.EARNINGS_RENDER_INTERVAL_SECONDS", 0.01)
    settings = replace(
        settings,
        earnings_enabled=False,
        earnings_source_url="https://example.test/overview",
    )
    store = make_store(settings)
    source = FakeEarningsSource({"alice": _entry()})
    monitor = EarningsMonitor(
        settings, store, source, is_active=lambda: "alice"
    )

    await monitor._tick()

    assert source.calls == []
    assert monitor.summary()["status"] == "disabled"


def test_default_summary_reflects_settings(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        earnings_source_url="https://example.test/overview",
        earnings_period="7d",
        earnings_refresh_seconds=120,
        earnings_overlay_enabled=True,
    )

    summary = default_summary(settings)

    assert summary["enabled"] is True
    assert summary["configured"] is True
    assert summary["period"] == "7d"
    assert summary["period_label"] == "Letzte 7 Tage"
    assert summary["interval_seconds"] == 120
    assert summary["overlay_enabled"] is True
    assert summary["view"] == "standard"
    assert summary["status"] == "inactive"

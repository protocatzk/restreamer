"""Pure parsing of TokTracker-compatible earnings payloads."""

from __future__ import annotations

from dataclasses import dataclass

from app.handles import normalize_handle

EARNINGS_PERIOD_LABELS = {
    "today": "Heute",
    "24h": "Letzte 24 h",
    "7d": "Letzte 7 Tage",
    "30d": "Letzte 30 Tage",
    "all": "Gesamt",
}


def _first_value(item: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_str(item: dict[str, object], *keys: str) -> str:
    value = _first_value(item, *keys)
    return "" if value is None else str(value).strip()


def _parse_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "live", "online", "streaming"}:
        return True
    if text in {"0", "false", "no", "off", "offline", "ended", "not_live"}:
        return False
    return None


def _dict_field(
    item: dict[str, object], *keys: str
) -> dict[str, object] | None:
    value = _first_value(item, *keys)
    return dict(value) if isinstance(value, dict) else None


def _selected_fields(
    value: dict[str, object] | None, keys: tuple[str, ...]
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    selected = {key: value[key] for key in keys if key in value}
    return selected or None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    for marker in ("EUR", "USD", "€", "$"):
        text = text.replace(marker, "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(round(number))


def _first_float(item: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        if key not in item:
            continue
        number = _as_float(item.get(key))
        if number is not None:
            return number
    return None


def _first_int(item: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        if key not in item:
            continue
        number = _as_int(item.get(key))
        if number is not None:
            return number
    return None


def _item_username(item: dict[str, object]) -> str:
    value = _first_value(
        item,
        "username",
        "unique_id",
        "uniqueId",
        "handle",
        "tiktok",
        "streamer_username",
        "streamerUsername",
        "user",
        "streamer",
        "channel",
    )
    if isinstance(value, dict):
        value = _first_value(
            value, "username", "unique_id", "uniqueId", "handle", "tiktok"
        )
    if value in (None, ""):
        return ""
    return str(value).strip()


def _merge_missing(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        if target.get(key) in (None, ""):
            target[key] = value


def _extract_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    streamer = payload.get("streamer")
    if isinstance(streamer, dict) and _item_username(streamer):
        merged = dict(streamer)
        for key in ("summary", "totals"):
            extra = payload.get(key)
            if isinstance(extra, dict):
                _merge_missing(merged, extra)
        sessions = payload.get("sessions")
        if isinstance(sessions, list):
            merged["__sessions"] = sessions
        rates = payload.get("rates")
        if isinstance(rates, dict):
            merged["__rates"] = rates
        for key in ("worker_diagnostics", "workerDiagnostics", "active_session"):
            value = payload.get(key)
            if isinstance(value, dict):
                merged[key] = value
        return [merged]

    for key in ("streamers", "data", "items", "results", "channels"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for nested in ("streamers", "items", "results", "channels"):
                found = candidate.get(nested)
                if isinstance(found, list):
                    return [item for item in found if isinstance(item, dict)]
            if _item_username(candidate):
                return [candidate]

    for key in ("streamer", "channel", "user"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and _item_username(candidate):
            return [candidate]

    if _item_username(payload):
        return [payload]
    return []


def _extract_rates(payload: object) -> dict[str, float | None]:
    candidates: list[dict[str, object]] = []
    if isinstance(payload, dict):
        for key in ("rates", "settings"):
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                candidates.append(candidate)
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("rates", "settings"):
                candidate = data.get(key)
                if isinstance(candidate, dict):
                    candidates.append(candidate)

    def find(*keys: str) -> float | None:
        for candidate in candidates:
            for key in keys:
                number = _as_float(candidate.get(key))
                if number is not None:
                    return number
        return None

    return {
        "usd_per_diamond": find("usd_per_diamond", "usdPerDiamond"),
        "gross_usd_per_coin": find("gross_usd_per_coin", "grossUsdPerCoin"),
        "usd_to_eur_rate": find("usd_to_eur_rate", "usdToEurRate"),
    }


def _session_values(
    item: dict[str, object],
    rates: dict[str, float | None],
) -> tuple[float | None, float | None, float | None, float | None, int | None]:
    session_coins: float | None = None
    session_usd_net: float | None = None
    session_eur_net: float | None = None
    session_usd_gross: float | None = None
    session_gifts: int | None = None

    sessions = item.get("__sessions")
    if isinstance(sessions, list):
        session_items = [entry for entry in sessions if isinstance(entry, dict)]
        active = next(
            (
                entry
                for entry in session_items
                if str(entry.get("status", "")).strip().lower() == "active"
            ),
            session_items[0] if session_items else None,
        )
        if active is not None:
            session_coins = _first_float(
                active, "session_coins", "total_coins", "coins"
            )
            session_usd_net = _first_float(active, "usd_net", "usdNet")
            session_eur_net = _first_float(active, "eur_net", "eurNet")
            session_gifts = _first_int(
                active, "gift_events", "gifts_received_session"
            )

    worker = item.get("worker_diagnostics")
    if isinstance(worker, dict):
        if session_coins is None:
            session_coins = _first_float(
                worker, "coins_received_session", "session_coins"
            )
        if session_gifts is None:
            session_gifts = _first_int(
                worker, "gifts_received_session", "session_gifts"
            )

    item_rates = _extract_rates(item)
    for key, value in rates.items():
        if item_rates.get(key) is None:
            item_rates[key] = value

    usd_per_diamond = item_rates["usd_per_diamond"]
    gross_usd_per_coin = item_rates["gross_usd_per_coin"]
    usd_to_eur_rate = item_rates["usd_to_eur_rate"]

    if session_coins is not None:
        if session_usd_net is None and usd_per_diamond is not None:
            session_usd_net = session_coins * usd_per_diamond
        if session_usd_gross is None and gross_usd_per_coin is not None:
            session_usd_gross = session_coins * gross_usd_per_coin
        if session_eur_net is None and session_usd_net is not None:
            if usd_to_eur_rate is not None:
                session_eur_net = session_usd_net * usd_to_eur_rate

    return (
        session_coins,
        _round_money(session_usd_net),
        _round_money(session_eur_net),
        _round_money(session_usd_gross),
        session_gifts,
    )


def _round_money(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


_SESSION_FIELDS = (
    "id",
    "session_id",
    "room_id",
    "started_at",
    "ended_at",
    "status",
    "peak_viewers",
    "last_fan_tickets",
    "session_coins",
    "gift_events",
    "usd_net",
    "eur_net",
    "usd_gross",
    "eur_gross",
)

_DIAGNOSTIC_FIELDS = (
    "state",
    "is_running",
    "connected_since",
    "last_event_at",
    "last_message",
    "reconnect_attempts",
    "current_viewers",
    "peak_viewers",
    "room_fan_tickets",
    "gifts_received_session",
    "coins_received_session",
    "session_id",
)


def _session_info(item: dict[str, object]) -> dict[str, object] | None:
    info = dict(
        _selected_fields(
            _dict_field(item, "active_session", "activeSession"),
            _SESSION_FIELDS,
        )
        or {}
    )
    sessions = item.get("__sessions")
    if isinstance(sessions, list):
        session_items = [entry for entry in sessions if isinstance(entry, dict)]
        active = next(
            (
                entry
                for entry in session_items
                if str(entry.get("status", "")).strip().lower() == "active"
            ),
            session_items[0] if session_items else None,
        )
        if active is not None:
            for key, value in active.items():
                if key in _SESSION_FIELDS and info.get(key) in (None, ""):
                    info[key] = value

    worker = _dict_field(item, "worker_diagnostics", "workerDiagnostics")
    if worker is not None:
        if info.get("id") in (None, "") and worker.get("session_id") is not None:
            info["id"] = worker.get("session_id")
        if info.get("started_at") in (None, ""):
            info["started_at"] = worker.get("connected_since")
        if info.get("peak_viewers") in (None, ""):
            info["peak_viewers"] = worker.get("peak_viewers")
        if info.get("last_fan_tickets") in (None, ""):
            info["last_fan_tickets"] = worker.get("room_fan_tickets")
        if info.get("gift_events") in (None, ""):
            info["gift_events"] = worker.get("gifts_received_session")
        if info.get("session_coins") in (None, ""):
            info["session_coins"] = worker.get("coins_received_session")

    return info or None


def _diagnostics(item: dict[str, object]) -> dict[str, object] | None:
    return _selected_fields(
        _dict_field(item, "worker_diagnostics", "workerDiagnostics"),
        _DIAGNOSTIC_FIELDS,
    )
@dataclass(frozen=True)
class EarningsEntry:
    username: str
    display_name: str = ""
    avatar_url: str = ""
    room_id: str = ""
    is_live: bool | None = None
    follower_count: int | None = None
    current_viewers: int | None = None
    last_seen_at: str = ""
    coins: float | None = None
    usd_net: float | None = None
    eur_net: float | None = None
    usd_gross: float | None = None
    eur_gross: float | None = None
    gift_events: int | None = None
    gifts_sent: int | None = None
    unique_gifters: int | None = None
    session_coins: float | None = None
    session_usd_net: float | None = None
    session_eur_net: float | None = None
    session_usd_gross: float | None = None
    session_gifts: int | None = None
    top_gift: dict[str, object] | None = None
    top_gifter: dict[str, object] | None = None
    session_info: dict[str, object] | None = None
    diagnostics: dict[str, object] | None = None
    rates: dict[str, float | None] | None = None
    period: str = ""

    @property
    def currency(self) -> str:
        if self.eur_net is not None or self.eur_gross is not None:
            return "EUR"
        if self.usd_net is not None or self.usd_gross is not None:
            return "USD"
        return ""

    @property
    def net(self) -> float | None:
        return self.eur_net if self.currency == "EUR" else self.usd_net

    @property
    def gross(self) -> float | None:
        return self.eur_gross if self.currency == "EUR" else self.usd_gross

    @property
    def session_currency(self) -> str:
        if self.session_eur_net is not None:
            return "EUR"
        if self.session_usd_net is not None:
            return "USD"
        return ""

    @property
    def session_net(self) -> float | None:
        if self.session_currency == "EUR":
            return self.session_eur_net
        return self.session_usd_net

    @property
    def session_gross(self) -> float | None:
        if self.session_currency == "EUR":
            if self.session_eur_net is None:
                return None
            if self.session_usd_net and self.session_eur_net:
                ratio = self.session_eur_net / self.session_usd_net
                if self.session_usd_gross is not None:
                    return round(self.session_usd_gross * ratio, 2)
            return None
        return self.session_usd_gross


def parse_earnings(payload: object) -> dict[str, EarningsEntry]:
    """Parse a TokTracker-compatible earnings response.

    The contract accepts the public overview (``streamers`` list), the
    single-streamer detail response (``streamer`` + ``summary`` + ``sessions``)
    and a bare list. Missing values stay unknown; the monitor then shows the
    last known values or an explicit "no data" state.
    """
    items = _extract_items(payload)
    if not items:
        return {}

    rates = _extract_rates(payload)
    period = ""
    if isinstance(payload, dict):
        period = str(payload.get("period") or "").strip()

    earnings: dict[str, EarningsEntry] = {}
    for item in items:
        username = normalize_handle(_item_username(item))
        if not username:
            continue

        coins = _first_float(
            item, "total_coins", "coins", "diamonds", "diamond_count"
        )
        usd_net = _first_float(item, "usd_net", "usdNet", "net_usd")
        eur_net = _first_float(item, "eur_net", "eurNet", "net_eur")
        usd_gross = _first_float(item, "usd_gross", "usdGross", "gross_usd")
        eur_gross = _first_float(item, "eur_gross", "eurGross", "gross_eur")

        item_rates = _extract_rates(item)
        for key, value in rates.items():
            if item_rates.get(key) is None:
                item_rates[key] = value
        usd_per_diamond = item_rates["usd_per_diamond"]
        gross_usd_per_coin = item_rates["gross_usd_per_coin"]
        usd_to_eur_rate = item_rates["usd_to_eur_rate"]

        if coins is not None:
            if usd_net is None and usd_per_diamond is not None:
                usd_net = coins * usd_per_diamond
            if usd_gross is None and gross_usd_per_coin is not None:
                usd_gross = coins * gross_usd_per_coin
        if usd_net is not None and eur_net is None:
            if usd_to_eur_rate is not None:
                eur_net = usd_net * usd_to_eur_rate
        if usd_gross is not None and eur_gross is None:
            if usd_to_eur_rate is not None:
                eur_gross = usd_gross * usd_to_eur_rate

        (
            session_coins,
            session_usd_net,
            session_eur_net,
            session_usd_gross,
            session_gifts,
        ) = _session_values(item, rates)

        earnings[username] = EarningsEntry(
            username=username,
            display_name=_first_str(
                item, "display_name", "displayName", "nickname", "name"
            ),
            avatar_url=_first_str(
                item, "avatar_url", "avatarUrl", "avatar", "profile_picture"
            ),
            room_id=_first_str(item, "room_id", "roomId"),
            is_live=_parse_bool(
                _first_value(
                    item,
                    "is_live",
                    "isLive",
                    "live",
                    "online",
                    "is_online",
                    "isOnline",
                )
            ),
            follower_count=_first_int(
                item, "follower_count", "followerCount", "followers"
            ),
            current_viewers=_first_int(
                item, "current_viewers", "currentViewers", "viewers"
            ),
            last_seen_at=_first_str(
                item, "last_seen_at", "lastSeenAt", "last_seen"
            ),
            coins=coins,
            usd_net=_round_money(usd_net),
            eur_net=_round_money(eur_net),
            usd_gross=_round_money(usd_gross),
            eur_gross=_round_money(eur_gross),
            gift_events=_first_int(
                item, "gift_events_count", "giftEventsCount", "gift_events"
            ),
            gifts_sent=_first_int(
                item, "total_gifts_sent", "totalGiftsSent", "gifts_sent"
            ),
            unique_gifters=_first_int(
                item, "unique_gifters", "uniqueGifters", "gifters"
            ),
            session_coins=session_coins,
            session_usd_net=session_usd_net,
            session_eur_net=session_eur_net,
            session_usd_gross=session_usd_gross,
            session_gifts=session_gifts,
            top_gift=_dict_field(item, "top_gift", "topGift"),
            top_gifter=_dict_field(item, "top_gifter", "topGifter"),
            session_info=_session_info(item),
            diagnostics=_diagnostics(item),
            rates=item_rates,
            period=period,
        )
    return earnings

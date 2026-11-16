"""Pure rendering of supervisor state for the dashboard and Telegram bot.

The functions only read a ``StatusContext`` snapshot; they never touch
processes, locks or asyncio.  That keeps the growing set of status views
testable in isolation and stops the supervisor from owning presentation
details.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.keys import configured_key, key_source
from app.models import PlaylistConfig, StreamerConfig, StreamState
from app.online import OnlineStatusMonitor
from app.playlist import PreferredReturn
from app.store import StreamerStore


@dataclass(frozen=True)
class StatusContext:
    """Immutable snapshot of everything the views need."""

    store: StreamerStore
    online: OnlineStatusMonitor | None
    active_id: str | None
    state: StreamState
    playlist_mode: bool
    playlist_id: str | None
    offline_cycle: set[str]
    playlist_end_at: str | None
    playlist_end_at_monotonic: float | None
    switch_count: int
    last_switch_reason: str
    preferred_return: PreferredReturn | None
    now: float


def active_playlist_id(ctx: StatusContext) -> str | None:
    return ctx.playlist_id if ctx.playlist_mode else None


def online_view(ctx: StatusContext, cfg: StreamerConfig) -> dict:
    if cfg.id == ctx.active_id:
        if ctx.state == StreamState.LIVE:
            online: bool | None = True
        elif ctx.state == StreamState.OFFLINE:
            online = False
        else:
            online = None
        return {
            "online": online,
            "online_checked_at": None,
            "online_error": "",
            "online_checking": False,
        }
    if ctx.online is None:
        return {
            "online": None,
            "online_checked_at": None,
            "online_error": "",
            "online_checking": False,
        }
    return ctx.online.view(cfg.id)


def preferred_return_view(ctx: StatusContext) -> dict | None:
    record = ctx.preferred_return
    if record is None:
        return None
    streamer = ctx.store.get(record.streamer_id)
    return {
        "streamer_id": record.streamer_id,
        "name": streamer.name if streamer is not None else record.streamer_id,
        "preferred_id": record.preferred_id,
    }


def streamer_view(ctx: StatusContext, cfg: StreamerConfig) -> dict:
    source = key_source(cfg.youtube_key, cfg.youtube_key_env)
    return {
        "id": cfg.id,
        "name": cfg.name,
        "tiktok": cfg.tiktok,
        "enabled": cfg.enabled,
        "auto_start": cfg.auto_start,
        "video_mode": cfg.video_mode,
        "preferred": cfg.preferred,
        "own_stream": cfg.own_stream,
        "youtube_key_env": cfg.youtube_key_env,
        "asr_watchlist": cfg.asr_watchlist,
        "key_configured": source != "missing",
        "key_source": source,
        "active": cfg.id == ctx.active_id,
        **online_view(ctx, cfg),
    }


def playlist_view(ctx: StatusContext, playlist: PlaylistConfig) -> dict:
    shared_source = key_source(playlist.youtube_key, playlist.youtube_key_env)
    mode_active = (
        ctx.playlist_mode
        and ctx.playlist_id == playlist.id
        and ctx.active_id is not None
    )
    shared_key_value = configured_key(
        playlist.youtube_key, playlist.youtube_key_env
    )
    channels: list[dict] = []
    for number, channel_id in enumerate(playlist.channels, start=1):
        cfg = ctx.store.get(channel_id)
        if cfg is None:
            continue
        source = key_source(cfg.youtube_key, cfg.youtube_key_env)
        own_key_value = configured_key(cfg.youtube_key, cfg.youtube_key_env)
        online = online_view(ctx, cfg)
        channels.append(
            {
                "id": cfg.id,
                "name": cfg.name,
                "tiktok": cfg.tiktok,
                "number": number,
                "enabled": cfg.enabled,
                "key_configured": source != "missing",
                "preferred": cfg.preferred,
                "own_stream": cfg.own_stream,
                "own_stream_conflicts": bool(
                    cfg.own_stream
                    and shared_key_value
                    and own_key_value == shared_key_value
                ),
                "active": cfg.id == ctx.active_id,
                "offline": mode_active and cfg.id in ctx.offline_cycle,
                "online": online["online"],
                "online_checked_at": online["online_checked_at"],
                "online_checking": online["online_checking"],
            }
        )

    active_index = None
    if ctx.active_id is not None and ctx.active_id in playlist.channels:
        active_index = playlist.channels.index(ctx.active_id)

    end_in_seconds = None
    if mode_active and ctx.playlist_end_at_monotonic is not None:
        end_in_seconds = max(
            0,
            int(round(ctx.playlist_end_at_monotonic - ctx.now)),
        )

    return {
        "id": playlist.id,
        "name": playlist.name,
        "auto_advance": playlist.auto_advance,
        "wrap": playlist.wrap,
        "advance_delay_seconds": playlist.advance_delay_seconds,
        "end_time": playlist.end_time,
        "end_after_minutes": playlist.end_after_minutes,
        "ends_at": ctx.playlist_end_at if mode_active else None,
        "end_in_seconds": end_in_seconds,
        "youtube_key_env": playlist.youtube_key_env,
        "channels": channels,
        "channel_ids": list(playlist.channels),
        "total": len(playlist.channels),
        "shared_key_configured": shared_source != "missing",
        "shared_key_source": shared_source,
        "shared_key_stored": bool(playlist.youtube_key.strip()),
        "mode_active": mode_active,
        "index": active_index,
        "number": active_index + 1 if active_index is not None else None,
        "switch_count": ctx.switch_count if mode_active else 0,
        "last_switch_reason": ctx.last_switch_reason if mode_active else "",
    }


def playlists_view(ctx: StatusContext) -> dict:
    return {
        "playlists": [
            playlist_view(ctx, playlist) for playlist in ctx.store.playlists
        ],
        "active_playlist": active_playlist_id(ctx),
    }

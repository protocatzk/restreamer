"""Pydantic response models for the JSON API.

The dashboard consumes the full supervisor/settings payloads, which evolve
faster than the API contract.  The models therefore document and validate the
stable top-level fields while ``extra="allow"`` preserves every additional
field the frontend already relies on.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model that keeps unknown fields for forwards compatibility."""

    model_config = ConfigDict(extra="allow")


class HealthResponse(ApiModel):
    status: str = "ok"
    version: str = ""
    active: str | None = None
    state: str = "idle"


class StatusResponse(ApiModel):
    active: str | None = None
    state: str = "idle"
    state_since: str | None = None
    started_at: str | None = None
    pid: int | None = None
    source_pid: int | None = None
    output_pid: int | None = None
    sessions: int = 0
    source_sessions: int = 0
    youtube_sessions: int = 0
    title: str = ""
    last_error: str = ""
    output_error: str = ""
    active_playlist: str | None = None
    preferred_return: dict[str, Any] | None = None
    playlists: list[dict[str, Any]] = Field(default_factory=list)
    streamers: list[dict[str, Any]] = Field(default_factory=list)


class OkResponse(ApiModel):
    ok: bool = True


class StreamerListResponse(OkResponse):
    streamers: list[str] = Field(default_factory=list)


class OnlineCheckResponse(OkResponse):
    online_check: dict[str, Any] = Field(default_factory=dict)


class EarningsResponse(OkResponse):
    earnings: dict[str, Any] = Field(default_factory=dict)


class WordsResponse(OkResponse):
    enabled: bool = False
    total: int = 0
    watchlist: list[Any] = Field(default_factory=list)
    words: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None
    window_seconds: int | None = None
    streamer_id: str | None = None
    streamer_name: str | None = None


class SettingsResponse(ApiModel):
    streamers_file: str = ""
    settings_file: str = ""
    logs_dir: str = ""
    cookies_file: str = ""
    cookies_file_present: bool = False
    asr_watchlist_file: str = ""
    asr_words_file: str = ""
    asr_active_file: str = ""


class PlaylistsResponse(ApiModel):
    playlists: list[dict[str, Any]] = Field(default_factory=list)
    active_playlist: str | None = None


class PlaylistMutationResponse(PlaylistsResponse):
    playlist_id: str = ""


class QuickStreamer(ApiModel):
    id: str = ""
    name: str = ""
    tiktok: str = ""


class QuickAddResponse(StreamerListResponse):
    created: list[QuickStreamer] = Field(default_factory=list)
    skipped: list[Any] = Field(default_factory=list)
    playlist_id: str | None = None
    playlists: list[dict[str, Any]] = Field(default_factory=list)


class LogsResponse(ApiModel):
    lines: list[str] = Field(default_factory=list)


class ReloadResponse(OkResponse):
    error: str = ""
    streamers: list[str] = Field(default_factory=list)

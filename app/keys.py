"""Helpers for resolving YouTube keys from config and environment."""

from __future__ import annotations

import os

from app.models import PlaylistConfig, StreamerConfig


def configured_key(key: str, env_name: str) -> str:
    """Return the stored key or the environment value, never both."""
    if key.strip():
        return key.strip()
    if env_name:
        return os.getenv(env_name, "").strip()
    return ""


def key_source(key: str, env_name: str) -> str:
    """Describe where a key comes from: ``config``, ``env`` or ``missing``."""
    if key.strip():
        return "config"
    if env_name and os.getenv(env_name, "").strip():
        return "env"
    return "missing"


def shared_output_configured(playlist: PlaylistConfig) -> bool:
    """True when a playlist declares a shared YouTube target."""
    return bool(
        playlist.youtube_key.strip() or playlist.youtube_key_env.strip()
    )


def shared_output_available(playlist: PlaylistConfig) -> bool:
    """True when the playlist's shared key can actually be resolved."""
    return (
        key_source(playlist.youtube_key, playlist.youtube_key_env) != "missing"
    )


def channel_can_output(
    cfg: StreamerConfig, playlist: PlaylistConfig
) -> bool:
    """Whether a channel has a usable YouTube key inside a playlist."""
    if cfg.own_stream:
        return key_source(cfg.youtube_key, cfg.youtube_key_env) != "missing"
    if shared_output_configured(playlist):
        return shared_output_available(playlist)
    return key_source(cfg.youtube_key, cfg.youtube_key_env) != "missing"

"""Playlist endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.schemas import (
    PlaylistMutationResponse,
    PlaylistsResponse,
    StatusResponse,
)
from app.store import ConfigError, build_playlist
from app.supervisor import (
    KeyNotConfigured,
    PlaylistEmpty,
    PlaylistNotFound,
    StreamerDisabled,
    StreamerNotFound,
)
from app.web.context import AppContext
from app.web.http import json_object as _json_object
from app.web.http import watch_flag as _watch_flag


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    store = ctx.store
    supervisor = ctx.supervisor

    @router.get("/api/playlists", response_model=PlaylistsResponse)
    async def api_get_playlists():
        return supervisor.playlists_view()

    @router.post(
        "/api/playlists",
        status_code=201,
        response_model=PlaylistMutationResponse,
    )
    async def api_create_playlist(request: Request):
        payload = await _json_object(request)
        try:
            playlist = build_playlist(
                payload, existing=None, streamers=store.streamers
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if store.playlist(playlist.id) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Senderlisten-ID existiert bereits: {playlist.id}",
            )

        try:
            store.save_playlists([*store.playlists, playlist])
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Senderliste nicht speicherbar: {exc}"
            ) from exc

        return {"playlist_id": playlist.id, **supervisor.playlists_view()}

    @router.put(
        "/api/playlists/{playlist_id}",
        response_model=PlaylistMutationResponse,
    )
    async def api_update_playlist(playlist_id: str, request: Request):
        existing = store.playlist(playlist_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Senderliste nicht gefunden")

        payload = await _json_object(request)
        try:
            playlist = build_playlist(
                payload, existing=existing, streamers=store.streamers
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            store.save_playlists(
                [
                    playlist if item.id == playlist_id else item
                    for item in store.playlists
                ]
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Senderliste nicht speicherbar: {exc}"
            ) from exc

        return {"playlist_id": playlist.id, **supervisor.playlists_view()}

    @router.delete(
        "/api/playlists/{playlist_id}",
        response_model=PlaylistsResponse,
    )
    async def api_delete_playlist(playlist_id: str):
        if store.playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Senderliste nicht gefunden")
        if supervisor.active_playlist_id() == playlist_id:
            raise HTTPException(
                status_code=409,
                detail="Aktive Senderliste zuerst stoppen",
            )

        try:
            store.save_playlists(
                [
                    item
                    for item in store.playlists
                    if item.id != playlist_id
                ]
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Senderliste nicht speicherbar: {exc}"
            ) from exc

        return {"ok": True, **supervisor.playlists_view()}

    @router.post("/api/playlists/stop", response_model=StatusResponse)
    async def api_playlists_stop():
        return await supervisor.stop()

    @router.post(
        "/api/playlists/{playlist_id}/start",
        response_model=StatusResponse,
    )
    async def api_playlist_start(playlist_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.playlist_play(playlist_id, watch=watch)
        except PlaylistNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlaylistEmpty as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/playlists/{playlist_id}/next",
        response_model=StatusResponse,
    )
    async def api_playlist_next(playlist_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.playlist_step(playlist_id, 1, watch=watch)
        except PlaylistNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlaylistEmpty as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/playlists/{playlist_id}/previous",
        response_model=StatusResponse,
    )
    async def api_playlist_previous(playlist_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.playlist_step(playlist_id, -1, watch=watch)
        except PlaylistNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlaylistEmpty as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/playlists/{playlist_id}/tune/{streamer_id}",
        response_model=StatusResponse,
    )
    async def api_playlist_tune(
        playlist_id: str, streamer_id: str, request: Request
    ):
        watch = await _watch_flag(request)
        try:
            return await supervisor.playlist_tune(
                playlist_id, streamer_id, watch=watch
            )
        except (PlaylistNotFound, StreamerNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router

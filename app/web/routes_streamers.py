"""Streamer and log endpoints."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import (
    LogsResponse,
    QuickAddResponse,
    StatusResponse,
    StreamerListResponse,
)
from app.store import (
    QUICK_ADD_MAX_ITEMS,
    ConfigError,
    build_quick_streamers,
    build_streamer,
    resolve_quick_key,
    split_quick_tiktoks,
)
from app.supervisor import (
    KeyNotConfigured,
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

    @router.post(
        "/api/streamers",
        status_code=201,
        response_model=StreamerListResponse,
    )
    async def api_create_streamer(request: Request):
        payload = await _json_object(request)
        try:
            streamer = build_streamer(payload)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if store.get(streamer.id) is not None:
            raise HTTPException(
                status_code=409, detail=f"Streamer-ID existiert bereits: {streamer.id}"
            )

        try:
            store.upsert(streamer)
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Konfiguration nicht speicherbar: {exc}"
            ) from exc

        return {"ok": True, "streamers": [cfg.id for cfg in store.streamers]}

    @router.post("/api/streamers/quick", response_model=QuickAddResponse)
    async def api_quick_add_streamers(request: Request):
        payload = await _json_object(request)
        raw = payload.get("tiktoks", payload.get("tiktok", []))
        try:
            tiktoks = split_quick_tiktoks(raw)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not tiktoks:
            raise HTTPException(
                status_code=400,
                detail="Mindestens ein TikTok-Handle oder eine Live-URL erforderlich",
            )
        if len(tiktoks) > QUICK_ADD_MAX_ITEMS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximal {QUICK_ADD_MAX_ITEMS} Streamer pro Anfrage",
            )

        playlist_id = str(payload.get("playlist_id") or "").strip().lower()
        playlist = None
        if playlist_id:
            playlist = store.playlist(playlist_id)
            if playlist is None:
                raise HTTPException(
                    status_code=404, detail="Senderliste nicht gefunden"
                )

        try:
            youtube_key, youtube_key_env = resolve_quick_key(
                str(payload.get("youtube_key") or ""), playlist
            )
            created, skipped = build_quick_streamers(
                tiktoks,
                youtube_key=youtube_key,
                youtube_key_env=youtube_key_env,
                existing=store.streamers,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if created:
            new_ids = [streamer.id for streamer in created]
            playlists = store.playlists
            if playlist is not None:
                playlists = [
                    replace(
                        item,
                        channels=(*item.channels, *new_ids),
                    )
                    if item.id == playlist.id
                    else item
                    for item in playlists
                ]

            try:
                store.save([*store.streamers, *created], playlists)
            except ConfigError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Konfiguration nicht speicherbar: {exc}",
                ) from exc

        return {
            "ok": True,
            "created": [
                {"id": streamer.id, "name": streamer.name, "tiktok": streamer.tiktok}
                for streamer in created
            ],
            "skipped": skipped,
            "playlist_id": playlist.id if playlist is not None else None,
            "streamers": [cfg.id for cfg in store.streamers],
            "playlists": supervisor.playlists_view()["playlists"],
        }

    @router.put(
        "/api/streamers/{streamer_id}",
        response_model=StreamerListResponse,
    )
    async def api_update_streamer(streamer_id: str, request: Request):
        existing = store.get(streamer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden")

        payload = await _json_object(request)
        try:
            streamer = build_streamer(payload, existing=existing)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        status = supervisor.status()
        if status["active"] == streamer_id and streamer.id != streamer_id:
            raise HTTPException(
                status_code=409,
                detail="Aktiven Streamer zuerst stoppen, bevor die ID geändert wird",
            )
        if streamer.id != streamer_id and store.get(streamer.id) is not None:
            raise HTTPException(
                status_code=409, detail=f"Streamer-ID existiert bereits: {streamer.id}"
            )

        try:
            store.upsert(streamer, original_id=streamer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden") from exc
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Konfiguration nicht speicherbar: {exc}"
            ) from exc

        return {"ok": True, "streamers": [cfg.id for cfg in store.streamers]}

    @router.delete(
        "/api/streamers/{streamer_id}",
        response_model=StreamerListResponse,
    )
    async def api_delete_streamer(streamer_id: str):
        if store.get(streamer_id) is None:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden")
        if supervisor.status()["active"] == streamer_id:
            raise HTTPException(
                status_code=409, detail="Aktiven Streamer zuerst stoppen"
            )

        try:
            store.delete(streamer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden") from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Konfiguration nicht speicherbar: {exc}"
            ) from exc

        return {"ok": True, "streamers": [cfg.id for cfg in store.streamers]}

    # -------------------------------------------------------------------- Control

    @router.post(
        "/api/streamers/{streamer_id}/start",
        response_model=StatusResponse,
    )
    async def api_start(streamer_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.start(streamer_id, watch=watch)
        except StreamerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/api/streamers/{streamer_id}/stop",
        response_model=StatusResponse,
    )
    async def api_stop(streamer_id: str):
        try:
            return await supervisor.stop()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post(
        "/api/streamers/{streamer_id}/switch",
        response_model=StatusResponse,
    )
    async def api_switch(streamer_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.switch(streamer_id, watch=watch)
        except StreamerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/logs/{streamer_id}", response_model=LogsResponse)
    async def api_logs(
        streamer_id: str, lines: int = Query(default=200, ge=1, le=2000)
    ):
        if store.get(streamer_id) is None:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden")
        return {"lines": supervisor.tail_log(streamer_id, count=lines)}

    return router

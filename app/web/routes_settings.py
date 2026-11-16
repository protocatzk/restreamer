"""Settings and reload endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.chat import ensure_chat_overlay_file
from app.cookies import write_tiktok_cookies
from app.earnings import ensure_earnings_overlay_file
from app.schemas import SettingsResponse
from app.settings import SECRET_FIELDS
from app.web.context import AppContext
from app.web.http import json_object as _json_object
from app.words import write_watchlist

logger = logging.getLogger(__name__)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    settings = ctx.settings
    store = ctx.store
    supervisor = ctx.supervisor
    online_monitor = ctx.online_monitor
    earnings_monitor = ctx.earnings_monitor

    @router.get("/api/settings", response_model=SettingsResponse)
    async def api_get_settings():
        return settings.to_public_dict()

    @router.put("/api/settings", response_model=SettingsResponse)
    async def api_put_settings(request: Request):
        payload = await _json_object(request)

        clear_password = bool(payload.pop("clear_dashboard_password", False))
        clear_session = bool(payload.pop("clear_tiktok_session", False))
        clear_chat_key = bool(payload.pop("clear_chat_source_api_key", False))
        clear_online_key = bool(
            payload.pop("clear_online_source_api_key", False)
        )
        clear_earnings_key = bool(
            payload.pop("clear_earnings_source_api_key", False)
        )

        updates = dict(payload)
        for field in SECRET_FIELDS:
            if field not in updates or not str(updates.get(field) or "").strip():
                updates.pop(field, None)
        if clear_password:
            updates["dashboard_password"] = ""
        if clear_session:
            updates["tiktok_session_id"] = ""
        if clear_chat_key:
            updates["chat_source_api_key"] = ""
        if clear_online_key:
            updates["online_source_api_key"] = ""
        if clear_earnings_key:
            updates["earnings_source_api_key"] = ""

        try:
            candidate = settings.updated(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        output_needs_reload = (
            candidate.output_orientation != settings.output_orientation
            or candidate.reencode_video_bitrate != settings.reencode_video_bitrate
            or candidate.audio_bitrate != settings.audio_bitrate
            or candidate.output_fps != settings.output_fps
            or candidate.output_stretch_percent
            != settings.output_stretch_percent
            or candidate.chat_enabled != settings.chat_enabled
            or candidate.chat_overlay_engine_enabled
            != settings.chat_overlay_engine_enabled
            or candidate.chat_overlay_render_fps
            != settings.chat_overlay_render_fps
            or candidate.chat_overlay_position
            != settings.chat_overlay_position
            or candidate.chat_overlay_font_size
            != settings.chat_overlay_font_size
            or candidate.chat_overlay_width != settings.chat_overlay_width
            or candidate.chat_overlay_font != settings.chat_overlay_font
            or candidate.earnings_overlay_enabled
            != settings.earnings_overlay_enabled
            or candidate.earnings_overlay_position
            != settings.earnings_overlay_position
            or candidate.earnings_overlay_font_size
            != settings.earnings_overlay_font_size
            or candidate.earnings_overlay_font
            != settings.earnings_overlay_font
            or candidate.clock_enabled != settings.clock_enabled
            or candidate.clock_position != settings.clock_position
            or candidate.clock_show_seconds != settings.clock_show_seconds
            or candidate.clock_font_size != settings.clock_font_size
            or candidate.clock_font != settings.clock_font
            or candidate.text_overlay_enabled != settings.text_overlay_enabled
            or candidate.text_overlay_text != settings.text_overlay_text
            or candidate.text_overlay_position
            != settings.text_overlay_position
            or candidate.text_overlay_font_size
            != settings.text_overlay_font_size
            or candidate.text_overlay_font != settings.text_overlay_font
            or candidate.text_overlay_speed != settings.text_overlay_speed
            or candidate.version_overlay_enabled
            != settings.version_overlay_enabled
        )

        try:
            ensure_chat_overlay_file(candidate)
        except OSError:
            logger.warning(
                "Chat-Overlay-Datei konnte nicht angelegt werden", exc_info=True
            )
        try:
            ensure_earnings_overlay_file(candidate)
        except OSError:
            logger.warning(
                "Einnahmen-Overlay-Datei konnte nicht angelegt werden",
                exc_info=True,
            )

        try:
            candidate.save_runtime()
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Einstellungen nicht speicherbar: {exc}"
            ) from exc

        settings.apply_from(candidate)
        online_monitor.request_check()
        earnings_monitor.request_check()
        try:
            write_watchlist(
                settings.asr_watchlist_file, settings.asr_watchlist
            )
        except OSError:
            logger.warning(
                "ASR-Watchlist konnte nicht geschrieben werden", exc_info=True
            )

        if output_needs_reload:
            await supervisor.reload_output()

        if clear_session and settings.cookies_file.exists():
            try:
                settings.cookies_file.unlink()
            except OSError:
                logger.warning("cookies.txt konnte nicht gelöscht werden")

        if write_tiktok_cookies(
            settings.cookies_file,
            settings.tiktok_session_id,
            settings.tiktok_target_idc,
        ):
            logger.info("cookies.txt nach Einstellungsänderung neu geschrieben")

        return settings.to_public_dict()

    # ---------------------------------------------------------------- Senderlisten

    @router.post("/api/reload")
    async def api_reload():
        store.reload()
        return JSONResponse(
            {
                "ok": not bool(store.error),
                "error": store.error,
                "streamers": [cfg.id for cfg in store.streamers],
            }
        )

    return router

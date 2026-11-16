"""Status, monitoring and overlay endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response

from app import __version__
from app.schemas import (
    EarningsResponse,
    HealthResponse,
    OnlineCheckResponse,
    StatusResponse,
    WordsResponse,
)
from app.web.context import AppContext

logger = logging.getLogger(__name__)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    supervisor = ctx.supervisor
    online_monitor = ctx.online_monitor
    earnings_monitor = ctx.earnings_monitor

    @router.get("/healthz", response_model=HealthResponse)
    async def healthz():
        state = supervisor.status()
        return {
            "status": "ok",
            "version": __version__,
            "active": state["active"],
            "state": state["state"],
        }

    @router.get("/api/status", response_model=StatusResponse)
    async def api_status():
        return supervisor.status()

    @router.post("/api/online/check", response_model=OnlineCheckResponse)
    async def api_online_check():
        online_monitor.request_check()
        return {"ok": True, "online_check": online_monitor.summary()}

    @router.get("/api/earnings", response_model=EarningsResponse)
    async def api_earnings():
        return {"ok": True, "earnings": earnings_monitor.summary()}

    @router.post("/api/earnings/check", response_model=EarningsResponse)
    async def api_earnings_check():
        earnings_monitor.request_check()
        return {"ok": True, "earnings": earnings_monitor.summary()}

    @router.get("/api/words", response_model=WordsResponse)
    async def api_words():
        return {"ok": True, **supervisor.words_status()}

    @router.get("/api/overlay/preview.png")
    async def api_overlay_preview(sample: bool = Query(False)):
        try:
            content = supervisor.overlay_preview_png(sample=sample)
        except Exception as exc:  # noqa: BLE001 - preview must not kill the app
            logger.exception("Overlay-Vorschau fehlgeschlagen")
            raise HTTPException(
                status_code=500,
                detail=f"Overlay-Vorschau fehlgeschlagen: {exc}",
            ) from exc
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------- Einstellungen

    return router

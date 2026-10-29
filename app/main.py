from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.resolver import Resolver
from app.settings import Settings
from app.store import ConfigError, StreamerStore
from app.supervisor import (
    KeyNotConfigured,
    StreamerDisabled,
    StreamerNotFound,
    Supervisor,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PUBLIC_PATHS = {"/healthz"}


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        content="Authentifizierung erforderlich",
        headers={"WWW-Authenticate": 'Basic realm="py_restream", charset="UTF-8"'},
    )


def _basic_auth_ok(header: str | None, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, provided = decoded.partition(":")
    return secrets.compare_digest(provided, password)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = StreamerStore(settings.streamers_file)
    resolver = Resolver(settings)
    supervisor = Supervisor(settings, store, resolver)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logger.info("py_restream %s startet", __version__)
        if store.error:
            logger.warning("Konfigurationsfehler: %s", store.error)

        if settings.auto_start:
            for candidate in store.streamers:
                if candidate.enabled and candidate.auto_start:
                    try:
                        await supervisor.start(candidate.id, watch=True)
                        logger.info("Auto-Start: %s", candidate.id)
                    except Exception:
                        logger.exception("Auto-Start für %s fehlgeschlagen", candidate.id)
                    break

        yield

        await supervisor.shutdown()
        logger.info("py_restream beendet")

    app = FastAPI(
        title="py_restream",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.supervisor = supervisor

    @app.middleware("http")
    async def basic_auth_middleware(request: Request, call_next):
        password = settings.dashboard_password
        path = request.url.path
        if (
            not password
            or path in PUBLIC_PATHS
            or path.startswith("/static/")
        ):
            return await call_next(request)

        if not _basic_auth_ok(request.headers.get("authorization"), password):
            return _unauthorized()
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "version": __version__,
                "password_required": bool(settings.dashboard_password),
            },
        )

    @app.get("/healthz")
    async def healthz():
        state = supervisor.status()
        return {
            "status": "ok",
            "version": __version__,
            "active": state["active"],
            "state": state["state"],
        }

    @app.get("/api/status")
    async def api_status():
        return supervisor.status()

    @app.post("/api/streamers/{streamer_id}/start")
    async def api_start(streamer_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.start(streamer_id, watch=watch)
        except StreamerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/streamers/{streamer_id}/stop")
    async def api_stop(streamer_id: str):
        try:
            return await supervisor.stop()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/streamers/{streamer_id}/switch")
    async def api_switch(streamer_id: str, request: Request):
        watch = await _watch_flag(request)
        try:
            return await supervisor.switch(streamer_id, watch=watch)
        except StreamerNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (StreamerDisabled, KeyNotConfigured, ConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/logs/{streamer_id}")
    async def api_logs(
        streamer_id: str, lines: int = Query(default=200, ge=1, le=2000)
    ):
        if store.get(streamer_id) is None:
            raise HTTPException(status_code=404, detail="Streamer nicht gefunden")
        return {"lines": supervisor.tail_log(streamer_id, count=lines)}

    @app.post("/api/reload")
    async def api_reload():
        store.reload()
        return JSONResponse(
            {
                "ok": not bool(store.error),
                "error": store.error,
                "streamers": [cfg.id for cfg in store.streamers],
            }
        )

    return app


async def _watch_flag(request: Request) -> bool:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        return True
    if isinstance(payload, dict):
        return bool(payload.get("watch", True))
    return True


app = create_app()

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
from app.cookies import write_tiktok_cookies
from app.resolver import Resolver
from app.settings import SECRET_FIELDS, Settings
from app.store import (
    ConfigError,
    StreamerStore,
    build_playlist,
    build_streamer,
)
from app.supervisor import (
    KeyNotConfigured,
    PlaylistEmpty,
    PlaylistNotFound,
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
        headers={"WWW-Authenticate": 'Basic realm="Restreamer", charset="UTF-8"'},
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


async def _json_object(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON-Objekt erwartet")
    return payload


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
        logger.info("Restreamer %s startet", __version__)
        if store.error:
            logger.warning("Konfigurationsfehler: %s", store.error)

        if write_tiktok_cookies(
            settings.cookies_file,
            settings.tiktok_session_id,
            settings.tiktok_target_idc,
        ):
            logger.info("cookies.txt aus den Einstellungen geschrieben")

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
        logger.info("Restreamer beendet")

    app = FastAPI(
        title="Restreamer",
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
        if not password or path in PUBLIC_PATHS or path.startswith("/static/"):
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

    # ------------------------------------------------------------- Einstellungen

    @app.get("/api/settings")
    async def api_get_settings():
        return settings.to_public_dict()

    @app.put("/api/settings")
    async def api_put_settings(request: Request):
        payload = await _json_object(request)

        clear_password = bool(payload.pop("clear_dashboard_password", False))
        clear_session = bool(payload.pop("clear_tiktok_session", False))

        updates = dict(payload)
        for field in SECRET_FIELDS:
            if field not in updates or not str(updates.get(field) or "").strip():
                updates.pop(field, None)
        if clear_password:
            updates["dashboard_password"] = ""
        if clear_session:
            updates["tiktok_session_id"] = ""

        try:
            candidate = settings.updated(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            candidate.save_runtime()
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Einstellungen nicht speicherbar: {exc}"
            ) from exc

        settings.apply_from(candidate)

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

    @app.get("/api/playlists")
    async def api_get_playlists():
        return supervisor.playlists_view()

    @app.post("/api/playlists", status_code=201)
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

    @app.put("/api/playlists/{playlist_id}")
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

    @app.delete("/api/playlists/{playlist_id}")
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

    @app.post("/api/playlists/stop")
    async def api_playlists_stop():
        return await supervisor.stop()

    @app.post("/api/playlists/{playlist_id}/start")
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

    @app.post("/api/playlists/{playlist_id}/next")
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

    @app.post("/api/playlists/{playlist_id}/previous")
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

    @app.post("/api/playlists/{playlist_id}/tune/{streamer_id}")
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

    # ------------------------------------------------------------------ Streamer

    @app.post("/api/streamers", status_code=201)
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

    @app.put("/api/streamers/{streamer_id}")
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

    @app.delete("/api/streamers/{streamer_id}")
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

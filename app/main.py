from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.auth import AuthManager
from app.chat import ensure_chat_overlay_file
from app.cookies import write_tiktok_cookies
from app.delay_buffer import DelayBuffer
from app.earnings import EarningsMonitor, ensure_earnings_overlay_file
from app.online import OnlineStatusMonitor
from app.resolver import Resolver
from app.settings import Settings
from app.store import (
    StreamerStore,
)
from app.supervisor import (
    Supervisor,
)
from app.telegram_bot import RestreamerBot
from app.web.context import AppContext
from app.web.http import json_object as _json_object
from app.web.routes_playlists import build_router as build_playlists_router
from app.web.routes_settings import build_router as build_settings_router
from app.web.routes_streamers import build_router as build_streamers_router
from app.web.routes_system import build_router as build_system_router
from app.words import write_active_streamer, write_watchlist

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PUBLIC_PATHS = {"/healthz"}
AUTH_PATHS = {
    "/auth",
    "/auth/request",
    "/auth/check",
    "/auth/logout",
}


def _is_public_path(path: str) -> bool:
    return (
        path in PUBLIC_PATHS
        or path in AUTH_PATHS
        or path.startswith("/static/")
    )


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def _is_secure_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _safe_next(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _client_info(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "?")
    )
    agent = request.headers.get("user-agent", "").strip()
    return f"{ip} · {agent[:80]}" if agent else ip


def _same_origin(request: Request, origin: str) -> bool:
    try:
        origin_host = urlsplit(origin).netloc
    except ValueError:
        return False
    if not origin_host:
        return False
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    )
    host = host.split(",")[0].strip()
    return bool(host) and origin_host == host


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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = StreamerStore(settings.streamers_file)
    resolver = Resolver(settings)
    delay_buffer = DelayBuffer(settings)
    supervisor = Supervisor(
        settings, store, resolver, delay_buffer=delay_buffer
    )
    online_monitor = OnlineStatusMonitor(
        settings,
        store,
        is_active=lambda: supervisor.active_id,
    )
    supervisor.set_online_monitor(online_monitor)
    earnings_monitor = EarningsMonitor(
        settings,
        store,
        is_active=lambda: supervisor.active_id,
    )
    supervisor.set_earnings_monitor(earnings_monitor)
    auth_manager = AuthManager(
        telegram_enabled=bool(
            settings.telegram_bot_token.strip()
            and settings.telegram_allowed_user_ids
        )
    )
    telegram_bot = RestreamerBot(
        settings, supervisor, store, auth_manager=auth_manager
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logger.info("Restreamer %s startet", __version__)
        if store.error:
            logger.warning("Konfigurationsfehler: %s", store.error)

        try:
            ensure_chat_overlay_file(settings)
        except OSError:
            logger.warning(
                "Chat-Overlay-Datei konnte nicht angelegt werden", exc_info=True
            )
        try:
            ensure_earnings_overlay_file(settings)
        except OSError:
            logger.warning(
                "Einnahmen-Overlay-Datei konnte nicht angelegt werden",
                exc_info=True,
            )

        if write_tiktok_cookies(
            settings.cookies_file,
            settings.tiktok_session_id,
            settings.tiktok_target_idc,
        ):
            logger.info("cookies.txt aus den Einstellungen geschrieben")

        try:
            write_watchlist(
                settings.asr_watchlist_file, settings.asr_watchlist
            )
        except OSError:
            logger.warning(
                "ASR-Watchlist konnte nicht geschrieben werden", exc_info=True
            )

        await delay_buffer.start()

        async def _asr_active_loop() -> None:
            applied: tuple[str, str, str] | None = None
            pending: tuple[tuple[str, str, str], float] | None = None
            while True:
                active_id = supervisor.active_id
                streamer = store.get(active_id) if active_id else None
                if streamer is not None:
                    name = streamer.name
                    watchlist = (
                        streamer.asr_watchlist
                        if streamer.asr_watchlist.strip()
                        else settings.asr_watchlist
                    )
                else:
                    name = ""
                    watchlist = ""
                descriptor = (active_id or "", name, watchlist)
                if descriptor == applied:
                    pending = None
                elif pending is None or pending[0] != descriptor:
                    delay = 0.0
                    if (
                        applied is not None
                        and applied[0]
                        and settings.relay_delay_seconds > 0
                    ):
                        delay = float(settings.relay_delay_seconds)
                    pending = (descriptor, time.monotonic() + delay)
                if pending is not None and time.monotonic() >= pending[1]:
                    applied = pending[0]
                    try:
                        write_active_streamer(
                            settings.asr_active_file,
                            applied[0],
                            applied[1],
                            applied[2],
                        )
                    except OSError:
                        logger.warning(
                            "ASR-Active-Datei konnte nicht geschrieben "
                            "werden",
                            exc_info=True,
                        )
                    pending = None
                await asyncio.sleep(0.5)

        asr_state_task = asyncio.create_task(
            _asr_active_loop(), name="asr-active-state"
        )

        if settings.auto_start:
            for candidate in store.streamers:
                if candidate.enabled and candidate.auto_start:
                    try:
                        await supervisor.start(candidate.id, watch=True)
                        logger.info("Auto-Start: %s", candidate.id)
                    except Exception:
                        logger.exception("Auto-Start für %s fehlgeschlagen", candidate.id)
                    break

        online_monitor.start()
        earnings_monitor.start()
        await telegram_bot.start()

        yield

        asr_state_task.cancel()
        with suppress(asyncio.CancelledError):
            await asr_state_task
        await telegram_bot.stop()
        await online_monitor.stop()
        await earnings_monitor.stop()
        await supervisor.shutdown()
        await delay_buffer.stop()
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
    app.state.delay_buffer = delay_buffer
    app.state.online_monitor = online_monitor
    app.state.earnings_monitor = earnings_monitor
    app.state.telegram_bot = telegram_bot
    app.state.auth_manager = auth_manager

    context = AppContext(
        settings=settings,
        store=store,
        supervisor=supervisor,
        online_monitor=online_monitor,
        earnings_monitor=earnings_monitor,
    )
    app.include_router(build_system_router(context))
    app.include_router(build_settings_router(context))
    app.include_router(build_playlists_router(context))
    app.include_router(build_streamers_router(context))

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        password = settings.dashboard_password
        if password and _basic_auth_ok(
            request.headers.get("authorization"), password
        ):
            return await call_next(request)

        session = auth_manager.get_session(
            request.cookies.get(AuthManager.COOKIE_NAME)
        )
        if session is not None:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = (
                    request.headers.get("origin")
                    or request.headers.get("referer")
                )
                if origin and not _same_origin(request, origin):
                    return JSONResponse(
                        {"detail": "CSRF-Schutz: ungültige Origin"},
                        status_code=403,
                    )
            return await call_next(request)

        if not password and not auth_manager.telegram_enabled:
            return await call_next(request)

        if _wants_html(request):
            target = path
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(
                f"/auth?next={quote(target, safe='')}",
                status_code=303,
            )

        if password and not auth_manager.telegram_enabled:
            return _unauthorized()
        return JSONResponse(
            {"detail": "Authentifizierung erforderlich"}, status_code=401
        )

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "version": __version__,
                "password_required": bool(settings.dashboard_password),
                "telegram_auth_enabled": auth_manager.telegram_enabled,
            },
        )

    # ------------------------------------------------------- Telegram login

    @app.get("/auth", include_in_schema=False)
    async def auth_page(request: Request, next: str = "/"):
        target = _safe_next(next)
        session = auth_manager.get_session(
            request.cookies.get(AuthManager.COOKIE_NAME)
        )
        if session is not None:
            return RedirectResponse(target, status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": target,
                "telegram_enabled": auth_manager.telegram_enabled,
            },
        )

    @app.post("/auth/request")
    async def auth_request(request: Request):
        if not auth_manager.telegram_enabled:
            raise HTTPException(
                status_code=503,
                detail="Telegram-Login ist nicht konfiguriert",
            )
        bot = request.app.state.telegram_bot
        bot_username = getattr(bot, "bot_username", "")
        if not bot_username:
            raise HTTPException(
                status_code=503,
                detail="Telegram-Bot ist noch nicht verbunden",
            )
        token = auth_manager.create_token(_client_info(request))
        return {
            "token": token,
            "bot_username": bot_username,
            "deep_link": f"https://t.me/{bot_username}?start=login_{token}",
        }

    @app.post("/auth/check")
    async def auth_check(request: Request):
        payload = await _json_object(request)
        token = str(payload.get("token") or "")
        info = auth_manager.get_token(token)
        if info is None:
            return {"status": "expired"}
        if info.status == "approved":
            session_id = auth_manager.create_session(info.user)
            auth_manager.delete_token(token)
            response = JSONResponse(
                {"status": "approved", "user": info.user}
            )
            response.set_cookie(
                AuthManager.COOKIE_NAME,
                session_id,
                max_age=AuthManager.SESSION_TTL_SECONDS,
                httponly=True,
                secure=_is_secure_request(request),
                samesite="lax",
                path="/",
            )
            return response
        return {"status": info.status}

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        auth_manager.delete_session(
            request.cookies.get(AuthManager.COOKIE_NAME)
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(AuthManager.COOKIE_NAME, path="/")
        return response

    return app


app = create_app()

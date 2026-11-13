from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class PendingAuth:
    """One browser login request waiting for a Telegram approval."""

    token: str
    created_at: float
    status: str = "pending"  # pending | approved | denied
    user: str = ""
    client_info: str = ""


@dataclass
class AuthSession:
    """Authenticated dashboard session backed by an HttpOnly cookie."""

    session_id: str
    user: str
    expires_at: float


class AuthManager:
    """In-memory Telegram login tokens and dashboard sessions.

    The app runs with a single uvicorn worker, so a plain dict is sufficient.
    Tokens are one-time and short-lived; sessions survive until restart or
    expiry. The browser never receives the bot token.
    """

    COOKIE_NAME = "restreamer_session"
    TOKEN_TTL_SECONDS = 600
    SESSION_TTL_SECONDS = 86400 * 30
    MAX_PENDING_TOKENS = 100

    def __init__(self, telegram_enabled: bool = False) -> None:
        self.telegram_enabled = telegram_enabled
        self._tokens: dict[str, PendingAuth] = {}
        self._sessions: dict[str, AuthSession] = {}

    # ------------------------------------------------------------- tokens

    def _cleanup(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        for token, info in list(self._tokens.items()):
            if current - info.created_at > self.TOKEN_TTL_SECONDS:
                self._tokens.pop(token, None)
        for session_id, session in list(self._sessions.items()):
            if session.expires_at <= current:
                self._sessions.pop(session_id, None)

    def create_token(self, client_info: str = "") -> str:
        self._cleanup()
        if len(self._tokens) >= self.MAX_PENDING_TOKENS:
            oldest = min(
                self._tokens.values(), key=lambda item: item.created_at
            )
            self._tokens.pop(oldest.token, None)
        token = secrets.token_hex(16)
        self._tokens[token] = PendingAuth(
            token=token,
            created_at=time.time(),
            client_info=client_info,
        )
        return token

    def get_token(self, token: str) -> PendingAuth | None:
        self._cleanup()
        return self._tokens.get(token)

    def approve_token(self, token: str, user: str) -> bool:
        self._cleanup()
        info = self._tokens.get(token)
        if info is None or info.status != "pending":
            return False
        info.status = "approved"
        info.user = user or "Telegram"
        return True

    def deny_token(self, token: str) -> bool:
        self._cleanup()
        info = self._tokens.get(token)
        if info is None or info.status != "pending":
            return False
        info.status = "denied"
        return True

    def delete_token(self, token: str) -> None:
        self._tokens.pop(token, None)

    # ----------------------------------------------------------- sessions

    def create_session(self, user: str) -> str:
        self._cleanup()
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = AuthSession(
            session_id=session_id,
            user=user or "Telegram",
            expires_at=time.time() + self.SESSION_TTL_SECONDS,
        )
        return session_id

    def get_session(self, session_id: str | None) -> AuthSession | None:
        if not session_id:
            return None
        self._cleanup()
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

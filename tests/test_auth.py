from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.auth import AuthManager
from app.main import _safe_next, create_app


def auth_settings(settings, streamers_file):
    return replace(
        settings,
        streamers_file=streamers_file,
        dashboard_password="",
        telegram_bot_token="123:test-token",
        telegram_allowed_user_ids=(123456789,),
    )


def test_auth_manager_token_and_session_lifecycle() -> None:
    manager = AuthManager(telegram_enabled=True)

    token = manager.create_token()
    info = manager.get_token(token)
    assert info is not None
    assert info.status == "pending"
    assert manager.approve_token(token, "@tester") is True
    assert manager.get_token(token).status == "approved"
    assert manager.get_token(token).user == "@tester"

    session_id = manager.create_session("@tester")
    session = manager.get_session(session_id)
    assert session is not None
    assert session.user == "@tester"
    manager.delete_session(session_id)
    assert manager.get_session(session_id) is None

    denied_token = manager.create_token()
    assert manager.deny_token(denied_token) is True
    assert manager.get_token(denied_token).status == "denied"

    expired_token = manager.create_token()
    manager._tokens[expired_token].created_at -= manager.TOKEN_TTL_SECONDS + 1
    assert manager.get_token(expired_token) is None


def test_safe_next_rejects_open_redirects() -> None:
    assert _safe_next("/api/status") == "/api/status"
    assert _safe_next("//evil.example") == "/"
    assert _safe_next("https://evil.example") == "/"
    assert _safe_next("") == "/"


def test_html_requests_redirect_to_login(settings, streamers_file) -> None:
    app = create_app(auth_settings(settings, streamers_file))
    client = TestClient(app)

    response = client.get(
        "/api/status",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth?next=")

    page = client.get("/auth")
    assert page.status_code == 200
    assert "Mit Telegram anmelden" in page.text


def test_auth_request_approve_and_session_cookie(settings, streamers_file) -> None:
    app = create_app(auth_settings(settings, streamers_file))
    app.state.telegram_bot._bot_username = "testbot"
    client = TestClient(app)

    request = client.post("/auth/request")
    assert request.status_code == 200
    data = request.json()
    token = data["token"]
    assert data["bot_username"] == "testbot"
    assert data["deep_link"] == f"https://t.me/testbot?start=login_{token}"

    assert client.post("/auth/check", json={"token": token}).json()[
        "status"
    ] == "pending"

    assert app.state.auth_manager.approve_token(token, "@tester") is True

    check = client.post("/auth/check", json={"token": token})
    assert check.status_code == 200
    assert check.json()["status"] == "approved"
    assert check.json()["user"] == "@tester"

    assert client.get("/api/status").status_code == 200
    assert client.post("/auth/check", json={"token": token}).json()[
        "status"
    ] == "expired"


def test_auth_denied_and_expired(settings, streamers_file) -> None:
    app = create_app(auth_settings(settings, streamers_file))
    client = TestClient(app)

    denied = app.state.auth_manager.create_token()
    app.state.auth_manager.deny_token(denied)
    assert client.post("/auth/check", json={"token": denied}).json()[
        "status"
    ] == "denied"
    assert client.post("/auth/check", json={"token": "nope"}).json()[
        "status"
    ] == "expired"


def test_session_cookie_csrf_and_logout(settings, streamers_file) -> None:
    app = create_app(auth_settings(settings, streamers_file))
    client = TestClient(app)
    session_id = app.state.auth_manager.create_session("@tester")
    client.cookies.set(AuthManager.COOKIE_NAME, session_id)

    evil = client.post(
        "/api/reload", headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403

    allowed = client.post(
        "/api/reload", headers={"Origin": "http://testserver"}
    )
    assert allowed.status_code == 200

    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    assert logout.json()["ok"] is True

    after = client.get(
        "/api/status",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert after.status_code == 303


def test_login_page_redirects_when_session_exists(settings, streamers_file) -> None:
    app = create_app(auth_settings(settings, streamers_file))
    client = TestClient(app)
    session_id = app.state.auth_manager.create_session("@tester")
    client.cookies.set(AuthManager.COOKIE_NAME, session_id)

    response = client.get("/auth", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_basic_auth_still_works_with_telegram_enabled(
    settings, streamers_file
) -> None:
    app = create_app(
        replace(
            auth_settings(settings, streamers_file),
            dashboard_password="pw",
        )
    )
    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    assert (
        client.get("/api/status", auth=("admin", "pw")).status_code == 200
    )
    assert (
        client.get("/api/status", auth=("admin", "wrong")).status_code == 401
    )

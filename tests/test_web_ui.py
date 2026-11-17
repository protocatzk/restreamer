from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app.main import create_app
from app.settings import EDITABLE_FIELDS

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "app" / "static"

# Diese Werte sind bewusst nur über Umgebungsvariablen konfigurierbar.
ENV_ONLY_SETTINGS = {"asr_tap_enabled", "chat_delay_seconds"}


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.dialogs: list[dict[str, str]] = []
        self.aria_refs: list[tuple[str, str]] = []
        self.static_refs: list[str] = []
        self.settings_tabs: list[str] = []
        self.settings_panels: list[str] = []
        self.field_names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}

        if data.get("id"):
            self.ids.append(data["id"])
        if tag == "dialog":
            self.dialogs.append(data)
        if data.get("aria-labelledby"):
            self.aria_refs.append((tag, data["aria-labelledby"]))
        if tag in {"link", "script"}:
            ref = data.get("href") or data.get("src")
            if ref and ref.startswith("/static/"):
                self.static_refs.append(ref)
        if data.get("data-settings-tab"):
            self.settings_tabs.append(data["data-settings-tab"])
        if data.get("data-settings-panel"):
            self.settings_panels.append(data["data-settings-panel"])
        if tag in {"input", "select", "textarea"} and data.get("name"):
            self.field_names.add(data["name"])


def _index_html(settings, streamers_file) -> str:
    app = create_app(replace(settings, streamers_file=streamers_file))
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    return response.text


def _parse(html: str) -> DocumentParser:
    parser = DocumentParser()
    parser.feed(html)
    return parser


def test_index_has_unique_ids_and_valid_aria_references(
    settings, streamers_file
) -> None:
    html = _index_html(settings, streamers_file)
    parser = _parse(html)

    duplicates = {
        element_id: count
        for element_id, count in Counter(parser.ids).items()
        if count > 1
    }
    assert duplicates == {}

    ids = set(parser.ids)
    assert parser.dialogs
    for dialog in parser.dialogs:
        labelled_by = dialog.get("aria-labelledby", "")
        assert labelled_by
        assert labelled_by in ids
    for _, target in parser.aria_refs:
        assert target in ids


def test_index_has_settings_tabs_with_matching_panels(
    settings, streamers_file
) -> None:
    parser = _parse(_index_html(settings, streamers_file))

    assert parser.settings_tabs == [
        "stream",
        "quality",
        "overlays",
        "sources",
        "system",
    ]
    assert parser.settings_panels == parser.settings_tabs


def test_index_settings_cover_editable_fields(settings, streamers_file) -> None:
    html = _index_html(settings, streamers_file)
    match = re.search(
        r'<dialog id="settings-dialog".*?</dialog>', html, re.DOTALL
    )
    assert match is not None
    names = set(re.findall(r'name="([a-z0-9_]+)"', match.group(0)))

    missing = EDITABLE_FIELDS - ENV_ONLY_SETTINGS - names
    assert missing == set()
    assert "video_mode" not in html


def test_js_dom_ids_exist_in_index(settings, streamers_file) -> None:
    html = _index_html(settings, streamers_file)
    ids = set(re.findall(r'id="([^"]+)"', html))

    used: set[str] = set()
    for path in (STATIC_DIR / "js").glob("*.js"):
        if path.name == "login.js":
            continue
        source = path.read_text(encoding="utf-8")
        used.update(re.findall(r'\$\("([^"]+)"\)', source))
        used.update(re.findall(r'getElementById\("([^"]+)"\)', source))

    theme_source = (STATIC_DIR / "theme.js").read_text(encoding="utf-8")
    used.update(re.findall(r'getElementById\("([^"]+)"\)', theme_source))

    assert used - ids == set()


def test_referenced_static_assets_exist(settings, streamers_file) -> None:
    parser = _parse(_index_html(settings, streamers_file))
    assert parser.static_refs

    for reference in parser.static_refs:
        relative = reference.split("?", 1)[0].removeprefix("/static/")
        path = STATIC_DIR / relative
        assert path.is_file(), reference


def test_login_page_uses_static_assets(settings, streamers_file) -> None:
    app = create_app(replace(settings, streamers_file=streamers_file))
    with TestClient(app) as client:
        response = client.get("/auth")

    assert response.status_code == 200
    assert "/static/brand.css" in response.text
    assert "/static/login.css" in response.text
    assert "<style>" not in response.text
    assert 'data-next="/"' in response.text


def test_login_template_includes_telegram_module() -> None:
    env = Environment(
        loader=FileSystemLoader(str(BASE_DIR / "app" / "templates")),
        autoescape=True,
    )
    html = env.get_template("login.html").render(
        version="test", next="/dashboard", telegram_enabled=True
    )
    parser = _parse(html)

    assert "/static/js/login.js" in html
    assert 'data-next="/dashboard"' in html
    assert "login-shell" in parser.ids

    source = (STATIC_DIR / "js" / "login.js").read_text(encoding="utf-8")
    used = set(re.findall(r'getElementById\("([^"]+)"\)', source))
    assert used <= set(parser.ids)

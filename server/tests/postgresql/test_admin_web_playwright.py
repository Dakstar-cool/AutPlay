"""Loopback-only Playwright qualification for the optional M6 admin SSR UI.

Run from ``server`` with the disposable PostgreSQL root supplied by the canonical
environment::

    uv run --frozen pytest tests/postgresql/test_admin_web_playwright.py -q

The PostgreSQL fixture creates a randomly named migrated database and removes it
after the test.  The only durable output is a bounded, non-secret screenshot
set and manifest under ``docs/implementation/evidence/m6-admin-web``.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import uvicorn
from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.application.admin_commands import AdminCommandService
from autplay.application.web_admin import WebAdminService
from autplay.entrypoints.admin_web_http import create_admin_web_router
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from playwright.sync_api import Browser, Page, sync_playwright
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE = _ROOT / "docs" / "implementation" / "evidence" / "m6-admin-web"
_SURFACES = (
    "devices",
    "sessions",
    "invitations",
    "vault",
    "jobs",
    "imports",
    "review",
    "diagnostics",
    "recovery",
    "audit",
)
_SECURITY_HEADERS = (
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
)


def _seed_owner(connection: Connection[Any], now: datetime) -> UUID:
    row = connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('M6 browser qualification owner', 'OWNER') RETURNING user_id"
    ).fetchone()
    assert row is not None
    user_id = UUID(str(row[0]))
    connection.execute(
        """
        INSERT INTO account.server_instance (
          server_instance_id, identity_epoch, identity_public_key_spki,
          identity_thumbprint_sha256, label_hint, api_origin, stream_origin,
          capability_revision, created_at, updated_at
        ) VALUES (%s, 1, %s, %s, 'M6 browser qualification',
                  'https://api.invalid', 'https://stream.invalid', 1, %s, %s)
        """,
        (uuid4(), b"s" * 65, b"t" * 32, now, now),
    )
    connection.commit()
    return user_id


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        threading.Event().wait(0.05)
    raise RuntimeError("loopback admin browser server did not start")


def _assert_semantics(page: Page, *, require_table: bool) -> None:
    assert page.locator("main").count() == 1
    assert page.locator("h1").count() == 1
    assert page.locator("nav[aria-label]").count() >= 1
    assert page.locator("a.skip-link").count() == 1
    if require_table:
        assert page.locator("table, section.empty").count() >= 1


def _screenshot(page: Page, name: str) -> str:
    _EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = _EVIDENCE / name
    page.screenshot(path=str(path), full_page=True)
    return path.name


def test_admin_ssr_browser_qualification_real_postgresql(
    database_connection: Connection[Any], database_url: str
) -> None:
    """Exercise real login and each read surface across accessibility display modes."""

    now = datetime.now(UTC)
    user_id = _seed_owner(database_connection, now)
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    web = WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(sessions),
        csrf_secret=b"m6-browser-qualification-csrf-secret-long",
    )
    invitation = web.issue_invitation(user_id, now=now)
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    app = FastAPI()
    app.include_router(
        create_admin_web_router(
            web=web,
            views=SqlAlchemyAdminViewService(sessions),
            commands=AdminCommandService(SqlAlchemyAdminCommandRepository(sessions)),
            renderer=AdminTemplateRenderer(),
            origin=base_url,
            source_secret=b"m6-browser-qualification-source-secret-long",
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    screenshots: list[str] = []
    evidence: dict[str, object] = {
        "schema": "autplay.m6_admin_browser_qualification.v1",
        "loopback_only": True,
        "database": "isolated_disposable_postgresql_18_4_pgvector",
        "screenshots": screenshots,
        "surfaces": list(_SURFACES),
        "locales": ["en", "ru"],
        "viewports": ["desktop", "tablet", "mobile"],
        "checks": [],
    }
    try:
        with sync_playwright() as playwright:
            browser: Browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, extra_http_headers={"Origin": base_url}
            )
            page = context.new_page()
            login = page.goto(f"{base_url}/admin/login", wait_until="networkidle")
            assert login is not None and login.status == 200
            for header in _SECURITY_HEADERS:
                assert header in login.headers
            assert page.locator("form[action='/admin/login']").count() == 1
            page.locator("#browser-invitation").fill(invitation.bearer.decode())
            page.locator("button[type='submit']").click()
            page.wait_for_timeout(250)
            assert page.url == f"{base_url}/admin/", page.locator("body").inner_text()
            _assert_semantics(page, require_table=False)
            assert page.locator("html").get_attribute("lang") == "en"
            assert page.content().find(invitation.bearer.decode()) == -1
            screenshots.append(_screenshot(page, "desktop-dashboard-en.png"))

            for surface in _SURFACES:
                response = page.goto(
                    f"{base_url}/admin/{surface}?lang=en", wait_until="networkidle"
                )
                assert response is not None and response.status == 200
                for header in _SECURITY_HEADERS:
                    assert header in response.headers
                _assert_semantics(page, require_table=surface not in {"vault", "recovery"})
                assert page.content().find(invitation.bearer.decode()) == -1
                if surface in {"vault", "jobs", "review", "diagnostics", "recovery"}:
                    screenshots.append(_screenshot(page, f"desktop-{surface}-en.png"))

            page.set_viewport_size({"width": 820, "height": 1180})
            response = page.goto(f"{base_url}/admin/devices?lang=ru", wait_until="networkidle")
            assert response is not None and response.status == 200
            assert page.locator("html").get_attribute("lang") == "ru"
            _assert_semantics(page, require_table=True)
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement !== document.body")
            page.evaluate("document.body.style.zoom = '2'")
            screenshots.append(_screenshot(page, "tablet-devices-ru-200pct.png"))

            page.set_viewport_size({"width": 390, "height": 844})
            page.emulate_media(color_scheme="dark", reduced_motion="reduce")
            response = page.goto(f"{base_url}/admin/sessions?lang=ru", wait_until="networkidle")
            assert response is not None and response.status == 200
            _assert_semantics(page, require_table=True)
            assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
            assert page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
            screenshots.append(_screenshot(page, "mobile-sessions-ru-dark-reduced-motion.png"))
            browser.close()
        evidence["checks"] = [
            "real_postgresql_login",
            "all_representative_read_surfaces",
            "semantic_landmarks_headings_forms_tables",
            "en_ru_desktop_tablet_mobile",
            "keyboard_focus_200_percent_zoom_reduced_motion_light_dark",
            "security_headers_and_secret_non_leakage",
        ]
        (_EVIDENCE / "qualification.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()

"""Browser checks for accessible session filters and their GET/pagination behavior."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from autplay.domain.admin_views import AdminPage, AdminSessionFilters, AdminSessionItem
from autplay.domain.web_admin import WebActor
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import expect, sync_playwright

from .test_admin_web_http import _client, _Views
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


def test_session_filters_submit_reset_and_fit_mobile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cursor = "019a1000-0000-7000-8000-000000000001"
    seen: list[tuple[str | None, AdminSessionFilters]] = []

    def sessions_page(
        self: _Views,
        actor: WebActor,
        surface: str,
        *,
        limit: int = 100,
        after: str | None = None,
        session_filters: AdminSessionFilters | None = None,
    ) -> AdminPage:
        assert surface == "sessions"
        filters = session_filters or AdminSessionFilters()
        seen.append((after, filters))
        now = datetime.now(UTC)
        return AdminPage(
            (
                AdminSessionItem(
                    actor.web_session_id,
                    "BROWSER",
                    "ACTIVE",
                    now,
                    now + timedelta(minutes=30),
                    True,
                ),
            ),
            cursor if after is None else None,
        )

    monkeypatch.setattr(_Views, "page", sessions_page)
    port = _free_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    client, _web = _client(AdminTemplateRenderer(), origin=origin)
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width, locale in ((1440, "ru"), (390, "ru"), (390, "en")):
                page = browser.new_page(viewport={"width": width, "height": 1000})
                page.context.add_cookies(
                    [{"name": "autplay_admin_dev", "value": "session", "url": origin}]
                )
                page.goto(origin + f"/admin/sessions?lang={locale}&after={cursor}")
                form = page.locator("form.session-filters")
                expect(form.locator("select")).to_have_count(4)
                for name, value in (
                    ("state", "ACTIVE"),
                    ("expires", "24h"),
                    ("current", "yes"),
                    ("action", "none"),
                ):
                    form.locator(f"select[name={name}]").select_option(value)
                with page.expect_navigation():
                    form.locator("button[type=submit]").click()
                filters = AdminSessionFilters("ACTIVE", "24h", "yes", "none")
                assert seen.pop() == (None, filters)
                for name, value in (
                    ("state", "ACTIVE"),
                    ("expires", "24h"),
                    ("current", "yes"),
                    ("action", "none"),
                ):
                    expect(page.locator(f"select[name={name}]")).to_have_value(value)
                assert page.evaluate(
                    "document.documentElement.scrollWidth === document.documentElement.clientWidth"
                )
                assert page.locator(".session-filters select").evaluate_all(
                    "elements => elements.every(element => {"
                    "const r = element.getBoundingClientRect();"
                    "return r.left >= 0 && r.right <= document.documentElement.clientWidth;})"
                )
                page.screenshot(
                    path=str(tmp_path / f"sessions-{width}-{locale}.png"), full_page=True
                )
                with page.expect_navigation():
                    page.locator(".pagination a").click()
                assert seen.pop() == (cursor, filters)
                with page.expect_navigation():
                    page.locator(".session-filter-actions a").click()
                assert seen.pop() == (None, AdminSessionFilters())
                assert f"lang={locale}" in page.url and "after=" not in page.url
                page.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(5)
        assert not thread.is_alive()

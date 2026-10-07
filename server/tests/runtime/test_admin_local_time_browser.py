from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
import uvicorn
from autplay.domain.admin_views import AdminDashboard, AdminWorkerHealth
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import expect, sync_playwright

from .test_admin_vault import _vault
from .test_admin_web_http import _client, _Views
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


@pytest.mark.parametrize("timezone,hour", (("Europe/Moscow", "14"), ("America/New_York", "07")))
def test_admin_uses_browser_timezone_after_fragment_refresh(
    monkeypatch: pytest.MonkeyPatch, timezone: str, hour: str
) -> None:
    minute = [15]

    def dashboard(self: _Views, actor: object) -> AdminDashboard:
        return AdminDashboard(
            "Test server",
            True,
            3,
            False,
            worker_status="HEALTHY",
            vault_status="DEGRADED",
            worker=AdminWorkerHealth(
                datetime(2026, 10, 6, 11, minute[0], tzinfo=UTC), True, False, 1.0, 1024, 2048, 0, 0
            ),
            vault=_vault(),
        )

    monkeypatch.setattr(_Views, "dashboard", dashboard)
    port = _free_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    client, _ = _client(AdminTemplateRenderer(), origin=origin)
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(timezone_id=timezone, viewport={"width": 390, "height": 1000})
            page.context.add_cookies(
                [{"name": "autplay_admin_dev", "value": "session", "url": origin}]
            )
            page.clock.install()
            page.goto(origin + "/admin/?lang=ru")
            signal = page.locator(".health-updated time[data-local-time]")
            expect(signal).to_contain_text(hour + ":15")
            expect(page.locator("[data-vault-disk]")).to_contain_text("60,0%")
            assert page.evaluate(
                "document.documentElement.scrollWidth === document.documentElement.clientWidth"
            )
            minute[0] = 16
            with page.expect_response(lambda response: "fragment=health" in response.url):
                page.clock.run_for(10000)
            expect(signal).to_contain_text(hour + ":16")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(5)
        assert not thread.is_alive()

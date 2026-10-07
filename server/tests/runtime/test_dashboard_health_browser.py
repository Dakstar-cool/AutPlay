from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from autplay.domain.admin_views import AdminDashboard, AdminVaultStatus, AdminWorkerHealth
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import expect, sync_playwright

from .test_admin_web_http import _client, _Views
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


def test_dashboard_updates_load_reasons_and_expires_on_lost_contact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    busy = [True]
    cpu = [65.5]
    calls = [0]

    def dashboard(self: _Views, actor: object) -> AdminDashboard:
        calls[0] += 1
        return AdminDashboard(
            "Test server",
            True,
            3,
            False,
            worker_status="HEALTHY",
            vault_status="DEGRADED",
            worker=AdminWorkerHealth(
                datetime.now(UTC), True, busy[0], cpu[0], 104857600, 536870912, 1, 3
            ),
            vault=AdminVaultStatus(3, 2048, 1, 2, 2, 0, 1, None, False, 1, 1, 0),
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
    evidence = Path(os.environ.get("AUTPLAY_HEALTH_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.context.add_cookies(
                [{"name": "autplay_admin_dev", "value": "session", "url": origin}]
            )
            page.clock.install()
            page.goto(origin + "/admin/?lang=ru")
            worker = page.locator('[data-component="component_worker"]')
            vault = page.locator('[data-component="component_vault"]')
            assert "65,5%" in worker.inner_text() and "100,0 MiB / 512,0 MiB" in worker.inner_text()
            assert "Отсутствующие копии" in vault.inner_text()
            assert "Повреждённые копии" in vault.inner_text()
            assert vault.locator("a").get_attribute("href") == "/admin/vault?lang=ru"
            assert "private/" not in page.content()
            for width in (1920, 1440, 1024, 768, 390, 320):
                page.set_viewport_size({"width": width, "height": 1050})
                assert page.evaluate(
                    "document.documentElement.scrollWidth === document.documentElement.clientWidth"
                )
                if width in (1440, 390):
                    page.screenshot(path=str(evidence / f"health-{width}.png"), full_page=True)
                    page.locator(".status-grid").screenshot(
                        path=str(evidence / f"cards-{width}.png")
                    )
            page.set_viewport_size({"width": 1440, "height": 1050})
            cpu[0] = 12.0
            busy[0] = False
            with page.expect_response(lambda response: "fragment=health" in response.url):
                page.clock.run_for(10000)
            expect(worker).to_contain_text("12,0%")
            assert "Простаивает" in worker.inner_text() and calls[0] >= 2
            page.route("**/*fragment=health*", lambda route: route.abort())
            page.clock.run_for(40000)
            expect(worker.locator(".pill")).to_have_text("Нет связи")
            assert worker.locator(".health-metrics").count() == 0
            assert "устарели" in worker.inner_text()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()

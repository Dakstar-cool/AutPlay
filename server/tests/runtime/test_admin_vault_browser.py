from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
import uvicorn
from autplay.domain.admin_views import AdminUnavailable, AdminVaultStatus
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import Route, expect, sync_playwright

from .test_admin_vault import _vault
from .test_admin_web_http import _client, _Views
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


def test_vault_refresh_recovers_from_network_and_backend_failure_and_expired_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unavailable = [False]

    def status(self: _Views, actor: object, surface: str) -> object:
        return AdminUnavailable("private/secret") if unavailable[0] else _vault()

    monkeypatch.setattr(_Views, "status", status)
    port = _free_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    client, _ = _client(AdminTemplateRenderer(), origin=origin)
    server = uvicorn.Server(
        uvicorn.Config(
            client.app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    evidence = Path(os.environ.get("AUTPLAY_VAULT_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 1100}, timezone_id="Europe/Moscow"
            )
            page.context.add_cookies(
                [
                    {"name": "autplay_admin_dev", "value": "session", "url": origin},
                ]
            )
            page.goto(origin + "/admin/vault?lang=ru")
            failure_message = page.locator("[data-vault-page]").get_attribute("data-failed")
            assert failure_message is not None
            refresh = page.locator("[data-vault-refresh]")
            message = page.locator("[data-refresh-status]")
            expect(page.locator("#vault-errors-title")).to_have_text("Причины ошибок")
            expect(page.locator("[data-vault-disk]")).to_contain_text("60,0%")
            expect(page.locator("[data-vault-disk] progress")).to_have_attribute("value", "60.0")
            expect(page.locator(".vault-error-meta time").first).to_contain_text("13:00")
            assert "не означает повреждение" in page.locator(".vault-overview").inner_text()
            assert "private/" not in page.content() and "token=secret" not in page.content()
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({"width": width, "height": 1100})
                assert page.evaluate(
                    "document.documentElement.scrollWidth === document.documentElement.clientWidth"
                )
                if width in (1440, 390):
                    page.screenshot(path=str(evidence / f"vault-{width}.png"), full_page=True)
            page.route("**/admin/vault?*", lambda route: route.abort())
            refresh.click()
            expect(message).to_have_text(failure_message)
            assert page.locator(".vault-stale").count() == 1
            expect(page.locator(".vault-overview .pill")).to_have_text("Данные устарели")
            expect(refresh).not_to_have_attribute("aria-disabled", "true")
            assert page.locator("[data-vault-login]").is_hidden()
            assert page.locator(".vault-error-group").count() == 2
            page.unroute("**/admin/vault?*")
            page.clock.install()
            pending: list[Route] = []
            page.route("**/admin/vault?*", lambda route: pending.append(route))
            refresh.click()
            refresh.dispatch_event("click")
            expect(refresh).to_have_attribute("aria-disabled", "true")
            page.clock.run_for(11000)
            expect(message).to_have_text(failure_message)
            expect(refresh).not_to_have_attribute("aria-disabled", "true")
            assert len(pending) == 1
            pending[0].abort()
            page.unroute("**/admin/vault?*")
            unavailable[0] = True
            refresh.click()
            expect(message).to_have_text(failure_message)
            expect(refresh).not_to_have_attribute("aria-disabled", "true")
            assert page.locator(".vault-error-group").count() == 2
            unavailable[0] = False
            refresh.click()
            expect(message).to_have_text("Состояние обновлено.")
            expect(page.locator(".vault-error-meta time").first).to_contain_text("13:00")
            assert page.locator(".vault-stale").count() == 0
            page.context.clear_cookies()
            refresh.click()
            expect(message).to_contain_text("Сессия завершилась")
            expect(page.locator("[data-vault-login]")).to_be_visible()
            expect(refresh).not_to_have_attribute("aria-disabled", "true")
            page.screenshot(path=str(evidence / "vault-expired-session.png"), full_page=True)
            page.context.add_cookies(
                [
                    {"name": "autplay_admin_dev", "value": "session", "url": origin},
                ]
            )
            unavailable[0] = True
            result = page.goto(origin + "/admin/vault?lang=ru")
            assert result is not None and result.status == 503
            assert page.locator(".vault-summary").count() == 0
            assert page.locator(".vault-unavailable").count() == 1
            page.screenshot(path=str(evidence / "vault-unavailable.png"), full_page=True)
            unavailable[0] = False
            page.locator("[data-vault-refresh]").click()
            expect(page.locator("[data-refresh-status]")).to_have_text("Состояние обновлено.")
            assert page.locator(".vault-summary").count() == 1
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_vault_page_remains_usable_without_javascript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _Views,
        "status",
        lambda self, actor, surface: AdminVaultStatus(
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            None,
            False,
        ),
    )
    client, _ = _client(AdminTemplateRenderer())
    client.cookies.set("__Host-autplay_admin", "session")
    result = client.get("/admin/vault?lang=en")
    assert result.status_code == 200 and "No errors are recorded" in result.text
    assert 'href="/admin/vault?lang=en&amp;scope=all" data-vault-refresh' in result.text

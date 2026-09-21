"""Real HTTPS quota forms and responsive bundled UI against disposable PostgreSQL."""

from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Page, expect, sync_playwright

from .test_resource_policy import PolicyHarness
from .test_resource_policy import policy as policy
from .test_resource_policy_web import _client
from .test_web_passkey_browser import EVIDENCE, _tls_files


def _save(page: Page, form_selector: str) -> None:
    with page.expect_navigation(wait_until="load", timeout=10_000):
        page.locator(form_selector).get_by_role("button", name="Save limits", exact=True).click()


def test_browser_quota_forms_and_two_tab_conflict(policy: PolicyHarness, tmp_path: Path) -> None:
    target = policy.account(linked=True)
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    origin = f"https://localhost:{port}"
    with _client(policy, origin) as client:
        app, bearer = client.app, client.cookies.get("__Host-autplay_admin")
    assert isinstance(app, FastAPI) and bearer is not None
    key, cert = _tls_files(tmp_path)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ssl_keyfile=str(key),
            ssl_certfile=str(cert),
            log_level="error",
            access_log=False,
        )
    )

    def serve() -> None:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            threading.Event().wait(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 1000}
            )
            context.add_cookies(
                [
                    {
                        "name": "__Host-autplay_admin",
                        "value": bearer,
                        "url": origin,
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "Strict",
                    }
                ]
            )
            page, stale = context.new_page(), context.new_page()
            for tab in (page, stale):
                tab.goto(origin + "/admin/quotas?lang=en")
                expect(
                    tab.get_by_role("heading", name="Resource limits", exact=True)
                ).to_be_visible()
            expect(page.locator("fieldset").get_by_role("button")).to_be_disabled()
            defaults = 'form[action*="/defaults"]'
            page.locator("#limit-devices").fill("7")
            with page.expect_request(lambda request: request.method == "POST") as sent:
                _save(page, defaults)
            assert sent.value.header_value("origin") == origin
            assert policy.service.view(policy.actor).defaults.devices == 7
            stale.locator("#limit-devices").fill("8")
            stale.locator(defaults).get_by_role("button", name="Save limits", exact=True).click()
            expect(stale.get_by_role("alert")).to_contain_text("settings changed")
            assert policy.service.view(policy.actor).defaults.devices == 7
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(EVIDENCE / "quota-server-desktop.png"), full_page=True)
            page.goto(origin + f"/admin/quotas/accounts/{target}?lang=en")
            page.locator("#limit-devices").fill("9")
            _save(page, 'form[method="post"]')
            assert policy.service.view(policy.actor, target).effective.devices == 9
            page.locator("#limit-devices").fill("")
            _save(page, 'form[method="post"]')
            assert policy.service.view(policy.actor, target).effective.devices == 7
            page.set_viewport_size({"width": 320, "height": 900})
            page.emulate_media(color_scheme="dark")
            page.goto(origin + f"/admin/quotas/accounts/{target}?lang=ru")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(page.get_by_role("button", name="Сохранить лимиты", exact=True)).to_be_visible()
            page.screenshot(path=str(EVIDENCE / "quota-account-ru-mobile.png"), full_page=True)
            page.goto(origin + "/admin/quotas?lang=ru")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(EVIDENCE / "quota-server-ru-mobile.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()

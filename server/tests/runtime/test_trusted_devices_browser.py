from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from autplay.domain.web_admin import WebActor
from autplay.entrypoints.admin_web_http import TrustedDeviceWebItem
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import Page, sync_playwright

from .test_admin_web_http import _Admission, _client
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


class _TrustedAdmission(_Admission):
    blocked = False
    deleted = False
    empty = False

    def trusted_devices(self, actor: WebActor) -> tuple[TrustedDeviceWebItem, ...]:
        assert actor
        if self.empty:
            return ()
        items = (
            TrustedDeviceWebItem(
                self.key_reference,
                "A55",
                "ANDROID",
                "ACTIVE",
                1,
                self.device_id,
                blocked=self.blocked,
                connected_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
            ),
            TrustedDeviceWebItem(
                uuid4(),
                "Living room tablet with a longer device name",
                "ANDROID",
                "REMOVED",
                0,
                connected_at=datetime(2026, 9, 30, 15, 0, tzinfo=UTC),
            ),
            TrustedDeviceWebItem(uuid4(), "X" * 120, "FUTURE_PLATFORM", "FUTURE_STATE", 12),
        )
        return items[1:] if self.deleted else items

    def manage_trusted_device(
        self,
        actor: WebActor,
        key_reference: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
    ) -> None:
        super().manage_trusted_device(actor, key_reference, action, operation_id, request_sha256)
        if action == "BLOCK_FUTURE_ADMISSION":
            self.blocked = True
        elif action == "UNBLOCK_FUTURE_ADMISSION":
            self.blocked = False
        elif action == "REVOKE_AND_REMOVE":
            self.deleted = True


def _assert_contained(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
    assert page.locator(".trusted-device-card button:visible").evaluate_all(
        "elements => elements.every(element => {"
        "const rect = element.getBoundingClientRect();"
        "return rect.left >= 0 && rect.right <= document.documentElement.clientWidth;"
        "})"
    )


def test_trusted_devices_are_compact_localized_and_keyboard_accessible(tmp_path: Path) -> None:
    admission = _TrustedAdmission()
    port = _free_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    client, _ = _client(AdminTemplateRenderer(), admission, origin=origin)
    evidence = Path(os.environ.get("AUTPLAY_TRUSTED_DEVICE_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.context.add_cookies(
                [{"name": "autplay_admin_dev", "value": "session", "url": origin}]
            )
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for locale in ("ru", "en"):
                admission.blocked = False
                admission.deleted = False
                response = page.goto(f"{origin}/admin/trusted-devices?lang={locale}")
                assert response is not None and response.status == 200
                assert "script-src 'self'" in response.headers["content-security-policy"]
                assert page.locator("html").get_attribute("lang") == locale
                assert page.locator(".trusted-device-card").count() == 3
                assert "FUTURE_STATE" in page.locator(".trusted-device-card").last.inner_text()
                first = page.locator(".trusted-device-card").first
                buttons = first.locator("button")
                assert buttons.count() == 2
                assert page.locator(".trusted-device-developer").count() == 0
                for width in (1920, 1440, 1024, 768, 390, 320):
                    page.set_viewport_size({"width": width, "height": 1000})
                    _assert_contained(page)
                    assert first.evaluate("element => element.getBoundingClientRect().height") < 330
                    assert buttons.first.is_visible() and buttons.last.is_visible()
                    if width in (1440, 390):
                        page.screenshot(
                            path=evidence / f"trusted-devices-{locale}-{width}.png", full_page=True
                        )
                    for button in buttons.all():
                        button.focus()
                        assert button.evaluate("element => element === document.activeElement")
                        description_id = button.get_attribute("aria-describedby")
                        assert description_id and page.locator(f"#{description_id}").text_content()

                # The same sort controls are available on desktop and mobile.
                names = page.locator(".trusted-device-card h2")
                for width, container in ((1440, "thead"), (390, ".trusted-device-sort")):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.goto(f"{origin}/admin/trusted-devices?lang={locale}")
                    for key, direction, expected_first in (
                        ("name", "asc", "A55"),
                        ("name", "desc", "X" * 120),
                        ("connected_at", "desc", "A55"),
                        ("connected_at", "asc", "Living room tablet with a longer device name"),
                    ):
                        link = page.locator(f'{container} a[data-sort="{key}"]')
                        link.focus()
                        page.keyboard.press("Enter")
                        page.wait_for_url(
                            f"**/admin/trusted-devices?sort={key}&direction={direction}*"
                        )
                        assert names.first.inner_text() == expected_first
                        active = "ascending" if direction == "asc" else "descending"
                        assert page.locator(f'th[aria-sort="{active}"]').count() == 1
                        assert page.locator('th[aria-sort="none"]').count() == 1
                        _assert_contained(page)
                    assert page.locator(".trusted-device-date").last.inner_text().endswith("—")
                    page.locator(".language").click()
                    assert "sort=connected_at&direction=asc" in page.url

                # Real form submission exercises the shared script under the page CSP.
                page.set_viewport_size({"width": 390, "height": 1000})
                for action, expected in (
                    ("block", "BLOCK_FUTURE_ADMISSION"),
                    ("unblock", "UNBLOCK_FUTURE_ADMISSION"),
                    ("revoke-and-remove", "REVOKE_AND_REMOVE"),
                ):
                    page.goto(f"{origin}/admin/trusted-devices?lang={locale}")
                    button = page.locator(
                        f'form[action="/admin/trusted-devices/{admission.key_reference}/{action}"] '
                        'button[type="submit"]'
                    )
                    with page.expect_response(
                        lambda response: response.request.method == "POST"
                    ) as posted:
                        button.click()
                    assert posted.value.status == 303
                    page.wait_for_url(f"{origin}/admin/trusted-devices")
                    assert admission.calls[-1][0] == expected
                    if action == "revoke-and-remove":
                        assert page.locator(".trusted-device-card").count() == 2
                        assert page.locator(".trusted-device-card h2", has_text="A55").count() == 0
                    else:
                        next_action = "unblock" if action == "block" else "block"
                        assert first.locator(f'form[action$="/{next_action}"]').count() == 1
                        assert first.locator(f'form[action$="/{action}"]').count() == 0
                        assert first.locator("button").count() == 2

                admission.empty = True
                page.goto(f"{origin}/admin/trusted-devices?lang={locale}")
                assert page.locator(".empty").is_visible()
                assert page.locator(".trusted-device-card").count() == 0
                admission.empty = False
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()

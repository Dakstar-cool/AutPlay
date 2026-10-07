from __future__ import annotations

import os
import threading
from pathlib import Path
from uuid import UUID

import uvicorn
from autplay.domain.web_admin import WebActor
from autplay.entrypoints.admin_web_http import PendingDeviceAdmission
from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import sync_playwright

from .test_admin_web_http import _Admission, _client
from .test_backup_control_browser import _free_loopback_port, _wait_until_started


class _PendingAdmission(_Admission):
    def __init__(self) -> None:
        super().__init__()
        self.visible = False
        self.reads = 0

    def pending_reviews(self, actor: WebActor) -> tuple[PendingDeviceAdmission, ...]:
        self.reads += 1
        return super().pending_reviews(actor) if self.visible else ()

    def decide_pending_review(
        self,
        actor: WebActor,
        request_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
        device_name: str | None,
    ) -> None:
        super().decide_pending_review(
            actor, request_id, action, operation_id, request_sha256, device_name
        )
        self.visible = False


def test_pending_requests_refresh_preserve_names_and_submit_under_csp(tmp_path: Path) -> None:
    admission = _PendingAdmission()
    port = _free_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    client, _ = _client(
        AdminTemplateRenderer(),
        admission,
        mobile_api_origin="https://mobile.test:8443",
        origin=origin,
    )
    evidence = Path(os.environ.get("AUTPLAY_DEVICE_EVIDENCE_DIR", str(tmp_path)))
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
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.context.add_cookies(
                [{"name": "autplay_admin_dev", "value": "session", "url": origin}]
            )
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{origin}/admin/connection-requests?lang=ru")
            assert page.locator(".pending-device").count() == 0
            admission.visible = True
            name = page.locator('input[name="device_name"]')
            name.wait_for(state="visible", timeout=12000)
            name.fill("Kitchen phone")
            page.evaluate("document.activeElement.blur()")
            before_edit = admission.reads
            page.wait_for_timeout(5500)
            assert admission.reads == before_edit
            assert name.input_value() == "Kitchen phone"
            assert page.locator('input[name="review_locator"]').count() == 0
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1100})
                assert page.evaluate(
                    "document.documentElement.scrollWidth === document.documentElement.clientWidth"
                )
                page.screenshot(path=evidence / f"pending-devices-ru-{width}.png", full_page=True)
            with page.expect_response(lambda response: response.request.method == "POST") as posted:
                page.locator('.pending-device button[type="submit"]').first.click()
            assert posted.value.status == 303, (page.url, posted.value.status, admission.calls)
            page.wait_for_url(f"{origin}/admin/connection-requests")
            assert admission.calls[-1][0] == "TRUST_DEVICE"
            assert admission.calls[-1][-1] == "Kitchen phone"
            assert page.locator(".pending-device").count() == 0
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()

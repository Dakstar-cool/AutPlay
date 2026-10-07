from __future__ import annotations

import os
import re
from pathlib import Path

from autplay.web.renderer import AdminTemplateRenderer
from playwright.sync_api import Route, sync_playwright

from .test_admin_web_http import _Admission, _client


def test_server_qr_and_copy_work_under_admin_csp_on_desktop_and_phone(tmp_path: Path) -> None:
    client, _web = _client(
        AdminTemplateRenderer(), _Admission(), mobile_api_origin="https://mobile.test:8443"
    )
    client.cookies.set("__Host-autplay_admin", "session")
    evidence_path = Path(os.environ.get("AUTPLAY_QR_EVIDENCE_DIR", str(tmp_path)))
    evidence_path.mkdir(parents=True, exist_ok=True)
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "apps/android/src/test/resources/server-connection-qr.txt"
    )
    expected_modules = [
        line for line in fixture_path.read_text().splitlines() if not line.startswith("#")
    ]

    def serve(route: Route) -> None:
        response = client.get(route.request.url)
        route.fulfill(
            status=response.status_code, headers=dict(response.headers), body=response.content
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("https://admin.test/**", serve)
        for locale, width in (("en", 1440), ("ru", 390)):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto(f"https://admin.test/admin/connection-requests?lang={locale}")
            svg = page.locator("[data-server-qr] svg")
            svg.wait_for(state="visible")
            assert svg.get_attribute("role") == "img"
            path = svg.locator("path").get_attribute("d")
            assert path is not None
            dark_modules = {(int(x), int(y)) for x, y in re.findall(r"M(\d+),(\d+)h1v1h-1z", path)}
            actual_modules = [
                "".join(
                    "1" if (x, y) in dark_modules else "0" for x in range(len(expected_modules))
                )
                for y in range(len(expected_modules))
            ]
            assert actual_modules == expected_modules
            assert page.locator("[data-server-address]").input_value() == "https://mobile.test:8443"
            assert page.locator("[data-qr-status]").inner_text() == ""
            assert page.evaluate(
                "document.documentElement.scrollWidth === document.documentElement.clientWidth"
            )
            assert page.locator("input[name='device_name']").is_visible()
            assert page.locator("input[name='review_locator']").count() == 0
            page.locator("[data-copy-server-address]").click()
            assert page.evaluate("navigator.clipboard.readText()") == "https://mobile.test:8443"
            assert page.locator("[data-copy-status]").inner_text()
            page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
            page.screenshot(
                path=evidence_path / f"server-connection-{locale}-{width}.png", full_page=True
            )
        assert not errors
        context.close()
        browser.close()

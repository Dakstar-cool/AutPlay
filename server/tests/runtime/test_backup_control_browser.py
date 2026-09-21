from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import uvicorn
from autplay.application.backup_control import BackupControlService, parse_backup_targets
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
from autplay.entrypoints.admin_web_http import WebAdminHttp
from autplay.entrypoints.backup_control_http import create_backup_control_router
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import Page, sync_playwright


class _Web:
    def __init__(self, actor: WebActor) -> None:
        self.actor = actor

    def authenticate(self, bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession:
        del bearer, mutation
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession:
        del bearer, head
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        if actor != self.actor or csrf != b"c" * 32 or not operation_id:
            raise WebAdminError("csrf_invalid")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        threading.Event().wait(0.05)
    raise RuntimeError("loopback backup-control browser server did not start")


def _assert_no_horizontal_overflow(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
    assert page.evaluate("document.body.scrollWidth === document.documentElement.clientWidth")
    assert page.locator("select").evaluate_all(
        "elements => elements.every(element => {"
        "const rect = element.getBoundingClientRect();"
        "return rect.left >= 0 && rect.right <= document.documentElement.clientWidth;"
        "})"
    )


def test_backup_control_is_responsive_localized_and_keyboard_accessible(
    tmp_path: Path,
) -> None:
    actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 1)
    backups = BackupControlService(
        tmp_path / "control",
        parse_backup_targets(
            '[{"id":"workstation-usb-e","label":"External USB E","kind":"external-agent"}]'
        ),
    )
    backups.configure(
        actor,
        target_id="workstation-usb-e",
        max_backup_bytes=140 * 1024**3,
        warning_percent=90,
        expected_revision=0,
        schedule_mode="automatic",
        schedule_weekday=7,
        schedule_hour=3,
    )
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    app = FastAPI()
    app.mount(
        "/admin/static",
        StaticFiles(
            directory=Path(__file__).resolve().parents[2] / "src" / "autplay" / "web" / "static"
        ),
        name="admin-static",
    )
    app.include_router(
        create_backup_control_router(
            web=cast(WebAdminHttp, _Web(actor)),
            backups=backups,
            renderer=AdminTemplateRenderer(),
            origin=base_url,
            passkeys_enabled=True,
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_started(server)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(f"{base_url}/admin/recovery?lang=en", wait_until="networkidle")

            assert page.locator("html").get_attribute("lang") == "en"
            assert page.locator("link[rel='stylesheet']").get_attribute("href") == (
                "/admin/static/admin-v2.css?v=3"
            )
            _assert_no_horizontal_overflow(page)
            assert page.locator("select").evaluate_all(
                "elements => elements.every(element => "
                "parseFloat(getComputedStyle(element).minHeight) >= 44)"
            )

            page.set_viewport_size({"width": 412, "height": 915})
            page.goto(f"{base_url}/admin/recovery?lang=ru", wait_until="networkidle")
            assert page.locator("html").get_attribute("lang") == "ru"
            assert page.get_by_label("День автоматического резервного копирования").count() == 1
            _assert_no_horizontal_overflow(page)

            for _ in range(12):
                page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement?.id") == "backup-schedule-mode"
            assert page.locator("#backup-schedule-mode").evaluate(
                "element => getComputedStyle(element).outlineStyle === 'solid' && "
                "parseFloat(getComputedStyle(element).outlineWidth) > 0"
            )

            page.locator("#backup-schedule-mode").select_option("manual")
            assert not page.locator("[data-backup-automatic-schedule]").is_visible()
            assert page.locator("form[action^='/admin/recovery/request'] button").is_enabled()
            page.locator("#backup-schedule-mode").select_option("automatic")
            assert page.locator("[data-backup-automatic-schedule]").is_visible()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)

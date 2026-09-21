from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autplay.application.backup_control import BackupControlService, parse_backup_targets
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import AuthenticatedWebSession, WebActor, WebAdminError
from autplay.entrypoints.admin_web_http import WebAdminHttp
from autplay.entrypoints.backup_control_http import create_backup_control_router
from autplay.web.renderer import AdminTemplateRenderer


class _Web:
    def __init__(self) -> None:
        self.actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 1)

    def authenticate(
        self, bearer: bytes, *, mutation: bool
    ) -> AuthenticatedWebSession:
        del mutation
        if bearer != b"session":
            raise WebAdminError("authentication_required")
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession:
        del head
        return self.authenticate(bearer, mutation=False)

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        if actor != self.actor or csrf != b"c" * 32 or not operation_id:
            raise WebAdminError("csrf_invalid")


def _client(tmp_path: Path) -> tuple[TestClient, BackupControlService]:
    service = BackupControlService(
        tmp_path / "control",
        parse_backup_targets(
            '[{"id":"workstation-usb-e","label":"External USB E",'
            '"kind":"external-agent"}]'
        ),
    )
    app = FastAPI()
    app.include_router(
        create_backup_control_router(
            web=cast(WebAdminHttp, _Web()),
            backups=service,
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            passkeys_enabled=True,
        )
    )
    client = TestClient(app, base_url="https://admin.test")
    client.cookies.set("__Host-autplay_admin", "session")
    return client, service


def _form(text: str, action: str) -> dict[str, str]:
    match = re.search(
        rf'<form method="post" action="{re.escape(action)}\?lang=[^"]+">(.*?)</form>',
        text,
        re.DOTALL,
    )
    assert match is not None
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', match.group(1)))


def test_owner_selects_target_and_requests_bounded_external_agent(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    page = client.get("/admin/recovery?lang=ru")

    assert page.status_code == 200
    assert "External USB E" in page.text
    assert "Максимальный размер" in page.text
    assert "E:\\" not in page.text

    policy = _form(page.text, "/admin/recovery/policy") | {
        "target_id": "workstation-usb-e",
        "max_backup_gib": "140",
        "warning_percent": "90",
        "schedule_mode": "automatic",
        "schedule_weekday": "7",
        "schedule_hour": "3",
    }
    saved = client.post(
        "/admin/recovery/policy?lang=ru",
        data=policy,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert saved.status_code == 303

    configured = client.get("/admin/recovery?lang=en")
    request = _form(configured.text, "/admin/recovery/request")
    accepted = client.post(
        "/admin/recovery/request?lang=en",
        data=request,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    document = json.loads((service.root / "request.json").read_text(encoding="utf-8"))
    assert document["max_backup_bytes"] == 140 * 1024**3
    assert document["warning_percent"] == 90
    assert "path" not in document
    stored_policy = json.loads((service.root / "policy.json").read_text(encoding="utf-8"))
    assert stored_policy["schedule_mode"] == "automatic"
    assert stored_policy["schedule_weekday"] == 7
    assert stored_policy["schedule_hour"] == 3


def test_origin_and_csrf_fail_without_mutating_policy(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    page = client.get("/admin/recovery?lang=en")
    form = _form(page.text, "/admin/recovery/policy") | {
        "target_id": "workstation-usb-e",
        "max_backup_gib": "140",
        "warning_percent": "90",
        "schedule_mode": "manual",
        "schedule_weekday": "7",
        "schedule_hour": "3",
    }

    response = client.post("/admin/recovery/policy", data=form)

    assert response.status_code == 409
    assert not (service.root / "policy.json").exists()

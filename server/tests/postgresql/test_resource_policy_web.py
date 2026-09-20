"""Quota forms against real M6 sessions, CSRF, PostgreSQL policy and receipts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from threading import Event
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import UserAccountRow
from autplay.adapters.postgresql.models.resource_admission import QuotaOperationReceiptRow
from autplay.domain.resource_policy import QuotaChange
from autplay.domain.web_admin import WebActor
from autplay.entrypoints.resource_policy_web_http import create_resource_policy_web_router
from autplay.runtime.http import RequestRuntimeMiddleware, install_error_handlers
from autplay.runtime.metrics import RuntimeMetrics
from autplay.runtime.web_security import apply_admin_security_headers
from autplay.web.renderer import AdminTemplateRenderer, read_static_asset
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from starlette.responses import Response
from starlette.testclient import TestClient

from .test_resource_admission_runtime import present
from .test_resource_policy import PolicyHarness
from .test_resource_policy import policy as policy

ORIGIN = "https://admin.test"


class Forms(HTMLParser):
    def __init__(self, document: str) -> None:
        super().__init__()
        self.forms: dict[str, dict[str, str]] = {}
        self.current: dict[str, str] | None = None
        self.feed(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            action = attributes.get("action")
            if action:
                self.current = {}
                self.forms[action] = self.current
        elif tag == "input" and self.current is not None:
            name = attributes.get("name")
            if name:
                self.current[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None


def _client(policy: PolicyHarness, origin: str = ORIGIN) -> TestClient:
    invitation = policy.web.issue_invitation(policy.actor.user_id)
    credentials = policy.web.login(policy.web.begin_login(), invitation.bearer, b"w" * 32)
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestRuntimeMiddleware, metrics=RuntimeMetrics())
    app.include_router(
        create_resource_policy_web_router(
            web=policy.web,
            policy=policy.service,
            renderer=AdminTemplateRenderer(),
            origin=origin,
        )
    )

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"responding": True}

    @app.get("/admin/static/{asset}")
    def static(asset: str) -> Response:
        if asset not in {"admin-v2.css", "admin-forms-v1.js"}:
            return Response(status_code=404)
        payload, _ = read_static_asset(asset)
        return apply_admin_security_headers(
            Response(
                payload,
                media_type="text/css" if asset.endswith(".css") else "text/javascript",
            )
        )

    client = TestClient(app, base_url=origin)
    client.cookies.set("__Host-autplay_admin", credentials.bearer.decode())
    return client


def test_defaults_replay_cas_and_measured_budget_gate(policy: PolicyHarness) -> None:
    with _client(policy) as client:
        page = client.get("/admin/quotas?lang=ru")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "Лимиты ресурсов" in page.text and "<fieldset disabled>" in page.text
        path = "/admin/quotas/defaults?lang=ru"
        before = Forms(page.text).forms[path]
        change = {**before, "devices": "7", "playbacks": "3", "transfers": "4"}
        for _ in range(2):
            saved = client.post(
                path, data=change, headers={"Origin": ORIGIN}, follow_redirects=False
            )
            assert saved.status_code == 303
            assert saved.headers["location"] == "/admin/quotas?lang=ru"
        assert policy.service.view(policy.actor).defaults.devices == 7
        conflict = client.post(path, data={**change, "devices": "8"}, headers={"Origin": ORIGIN})
        assert conflict.status_code == 409
        stale = client.post(
            path, data={**before, "operation_id": str(uuid4())}, headers={"Origin": ORIGIN}
        )
        assert stale.status_code == 409
        current = Forms(client.get("/admin/quotas").text).forms["/admin/quotas/budget?lang=en"]
        blocked = client.post(
            "/admin/quotas/budget",
            data={**current, "playbacks": "2", "transfers": "2"},
            headers={"Origin": ORIGIN},
        )
        assert blocked.status_code == 503
    with policy.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperationReceiptRow)) == 1


def test_account_override_reset_is_scoped_and_autoescaped(policy: PolicyHarness) -> None:
    target, stranger = policy.account(linked=True), policy.account(linked=False)
    with policy.sessions.begin() as session:
        present(session.get(UserAccountRow, target)).display_name = '<img src=x onerror="alert(1)">'
        present(session.get(UserAccountRow, stranger)).display_name = "Hidden private account"
    path = f"/admin/quotas/accounts/{target}"
    with _client(policy) as client:
        page = client.get(path)
        assert page.status_code == 200
        assert "&lt;img" in page.text and "<img src=x" not in page.text
        form = Forms(page.text).forms[path + "?lang=en"]
        assert form["devices"] == form["playbacks"] == form["transfers"] == ""
        assert (
            client.post(
                path, data={**form, "devices": "9", "transfers": "1"}, headers={"Origin": ORIGIN}
            ).status_code
            == 200
        )
        assert policy.service.view(policy.actor, target).effective.devices == 9
        form = Forms(client.get(path).text).forms[path + "?lang=en"]
        assert (
            client.post(
                path,
                data={**form, "devices": "", "playbacks": "", "transfers": ""},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        reset = policy.service.view(policy.actor, target)
        assert reset.account_revision == 2 and reset.effective == reset.defaults
        denied = client.get(f"/admin/quotas/accounts/{stranger}")
        assert denied.status_code == 403 and "Hidden private account" not in denied.text
        assert "Hidden private account" not in client.get("/admin/quotas").text


def test_origin_csrf_duplicate_and_noncanonical_form_values(policy: PolicyHarness) -> None:
    with _client(policy) as client:
        form = Forms(client.get("/admin/quotas").text).forms["/admin/quotas/defaults?lang=en"]
        assert client.post("/admin/quotas/defaults", data=form).status_code == 403
        assert (
            client.post(
                "/admin/quotas/defaults", data=form, headers={"Origin": "https://other.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/admin/quotas/defaults",
                data={**form, "csrf_token": "wrong"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 403
        )
        for value in ("+1", "01", "1.0", "-1", "0", "1000001"):
            invalid = client.post(
                "/admin/quotas/defaults",
                data={**form, "devices": value},
                headers={"Origin": ORIGIN},
            )
            assert invalid.status_code == 400
        duplicate = client.post(
            "/admin/quotas/defaults",
            content=urlencode(form) + "&devices=8",
            headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert duplicate.status_code == 403
    assert policy.service.view(policy.actor).global_revision == 1


@pytest.mark.parametrize("source", ["view", "authentication"])
def test_database_failure_is_sanitized(
    policy: PolicyHarness, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise OperationalError("private SQL", {}, RuntimeError("private database path"))

    if source == "view":
        monkeypatch.setattr(policy.service, "editor", unavailable)
    else:
        monkeypatch.setattr(policy.web, "authenticate_safe_get", unavailable)
    with _client(policy) as client:
        failed = client.get("/admin/quotas")
        assert failed.status_code == 503
        assert failed.headers["retry-after"] == "5"
        assert "private SQL" not in failed.text and "private database path" not in failed.text


def test_mutation_database_work_does_not_block_async_requests(
    policy: PolicyHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = policy.service.apply

    def delayed(actor: WebActor, change: QuotaChange) -> dict[str, object]:
        entered.set()
        assert release.wait(3)
        return original(actor, change)

    monkeypatch.setattr(policy.service, "apply", delayed)
    with _client(policy) as client, ThreadPoolExecutor(max_workers=1) as pool:
        form = Forms(client.get("/admin/quotas").text).forms["/admin/quotas/defaults?lang=en"]
        pending = pool.submit(
            client.post,
            "/admin/quotas/defaults",
            data=form,
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        try:
            assert entered.wait(2)
            assert client.get("/probe").json() == {"responding": True}
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 303

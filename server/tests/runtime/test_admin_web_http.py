from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.application.web_admin import LoginChallenge
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.admin_views import (
    AdminConfirmationTarget,
    AdminDashboard,
    AdminDeviceItem,
    AdminPage,
    AdminUnavailable,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import (
    AuthenticatedWebSession,
    WebActor,
    WebAdminError,
    WebSessionCredentials,
)
from autplay.entrypoints.admin_web_http import (
    DeviceAdmissionReview,
    Renderer,
    TrustedDeviceWebItem,
    create_admin_web_router,
)
from autplay.web.renderer import AdminTemplateRenderer
from fastapi import FastAPI
from starlette.testclient import TestClient


class _Renderer:
    def render(
        self, template: str, *, locale: str, context: Mapping[str, object] | None = None
    ) -> str:
        del locale
        return f"{template}:{context!r}"


class _Web:
    commands: _Commands

    def __init__(self) -> None:
        self.challenge = LoginChallenge(
            uuid4(), uuid4(), b"preauth", b"nonce", datetime.now(UTC) + timedelta(minutes=5)
        )
        self.rate_sources: list[bytes] = []
        self.challenge_sources: list[bytes] = []
        self.login_fails = False
        self.login_receipt = False
        self.rate_limited = False
        self.challenge_rate_limited = False
        self.rotate = False
        self.head_calls: list[bool] = []
        self.actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0)
        self.mutation_calls = 0
        self.retry_calls = 0
        self.mutation_due = False
        self.revoked = False

    def begin_login(self) -> LoginChallenge:
        return self.challenge

    def login_challenge_rate_gate(self, source: bytes) -> None:
        self.challenge_sources.append(source)
        if self.challenge_rate_limited:
            raise WebAdminError("rate_limited")

    def login_rate_gate(self, source: bytes, invitation: bytes, request_hash: bytes) -> None:
        assert source != b"testclient"
        assert invitation == b"invite" and len(request_hash) == 32
        self.rate_sources.append(source)
        if self.rate_limited:
            raise WebAdminError("rate_limited")

    def login_retry_outcome(self, operation_id: UUID, request_hash: bytes) -> None:
        del operation_id, request_hash
        raise WebAdminError(
            "browser_login_outcome_unknown"
            if self.login_receipt
            else "browser_invitation_unavailable"
        )

    def login(
        self, challenge: LoginChallenge, invitation: bytes, request_hash: bytes
    ) -> WebSessionCredentials:
        assert (
            challenge.challenge_id == self.challenge.challenge_id and challenge.cookie == b"preauth"
        )
        assert invitation == b"invite" and len(request_hash) == 32
        if self.login_fails:
            self.login_receipt = True
            raise WebAdminError("browser_invitation_unavailable")
        return WebSessionCredentials(
            WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0),
            datetime.now(UTC),
            b"session",
            b"csrf",
        )

    def authenticate(self, bearer: bytes, *, mutation: bool) -> AuthenticatedWebSession:
        if mutation:
            self.mutation_calls += 1
            if self.mutation_due:
                raise WebAdminError("browser_session_rotation_required")
        if self.revoked:
            raise WebAdminError("authentication_required")
        if bearer != b"session":
            raise WebAdminError("authentication_required")
        return AuthenticatedWebSession(self.actor, b"c" * 32)

    def authenticate_safe_get(
        self, bearer: bytes, *, head: bool = False
    ) -> AuthenticatedWebSession:
        self.head_calls.append(head)
        actor = self.authenticate(bearer, mutation=False).actor
        return AuthenticatedWebSession(
            actor, b"c" * 32, b"rotated" if self.rotate and not head else None
        )

    def validate_csrf(self, actor: WebActor, csrf: bytes, operation_id: UUID) -> None:
        if actor != self.actor or csrf != b"c" * 32 or operation_id is None:
            raise WebAdminError("csrf_invalid")

    def revoked_lifecycle_retry(
        self, bearer: bytes, operation_id: UUID, action: str, request_hash: bytes
    ) -> str:
        assert bearer == b"session" and operation_id and action and len(request_hash) == 32
        self.retry_calls += 1
        return "LOGGED_OUT"

    def logout_current(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_hash: bytes,
        reason_code: str | None = None,
    ) -> None:
        assert actor == self.actor and operation_id and len(request_hash) == 32 and reason_code

    def logout_all_browser(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_hash: bytes,
        reason_code: str | None = None,
    ) -> None:
        assert actor == self.actor and operation_id and len(request_hash) == 32 and reason_code

    def revoke_browser_session(
        self,
        actor: WebActor,
        target_id: UUID,
        operation_id: UUID,
        request_hash: bytes,
        reason_code: str | None = None,
    ) -> None:
        assert actor == self.actor and target_id and operation_id and len(request_hash) == 32
        assert reason_code


class _Views:
    def dashboard(self, actor: WebActor) -> AdminDashboard:
        del actor
        return AdminDashboard("Test server", True, 3, True)

    def confirmation(
        self, actor: WebActor, action: str, target_id: UUID
    ) -> AdminConfirmationTarget:
        kinds = {
            "invitation": "ENROLLMENT_INVITATION",
            "device": "ANDROID_DEVICE",
            "session": "ANDROID_SESSION",
            "browser-session": "BROWSER_SESSION",
            "logout-current": "CURRENT_BROWSER_SESSION",
            "logout-all": "ALL_BROWSER_SESSIONS",
        }
        if action == "logout-current" and target_id != actor.web_session_id:
            raise WebAdminError("forbidden")
        if action == "logout-all" and target_id != actor.user_id:
            raise WebAdminError("forbidden")
        return AdminConfirmationTarget(
            target_id, kinds[action], "Phone" if action == "device" else None
        )

    def page(
        self,
        actor: WebActor,
        surface: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> AdminPage:
        del actor, limit, after
        if surface != "devices":
            raise WebAdminError("admin_surface_unavailable")
        return AdminPage((AdminDeviceItem(uuid4(), "Phone", "ANDROID", datetime.now(UTC)),), None)

    def status(self, actor: WebActor, surface: str) -> object:
        del actor, surface
        return AdminUnavailable("unavailable")


class _Commands:
    def __init__(self) -> None:
        self.calls: list[AdminCommand] = []

    def cancel_enrollment_invitation(self, command: AdminCommand) -> dict[str, object]:
        self.calls.append(command)
        return {}

    def revoke_android_device(self, command: AdminCommand) -> dict[str, object]:
        self.calls.append(command)
        return {}

    def revoke_android_session(self, command: AdminCommand) -> dict[str, object]:
        self.calls.append(command)
        return {}


class _Admission:
    def __init__(self) -> None:
        self.request_id = uuid4()
        self.key_reference = uuid4()
        self.calls: list[tuple[object, ...]] = []

    def resolve_review_locator(
        self, actor: WebActor, locator: str, operation_id: UUID, request_sha256: bytes
    ) -> None:
        assert actor and locator == "locator-secret" and operation_id and len(request_sha256) == 32
        self.calls.append(("resolve", actor.user_id, operation_id))

    def review(self, actor: WebActor) -> DeviceAdmissionReview:
        assert actor
        return DeviceAdmissionReview(
            request_id=self.request_id,
            device_label="Phone <script>",
            platform="ANDROID",
            app_version="1.0",
            device_model_hint="Test Model",
            api_major=1,
            requested_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            sas_3x4=("0123", "4567", "8901"),
        )

    def decide_review(
        self,
        actor: WebActor,
        request_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
    ) -> None:
        assert actor and request_id == self.request_id and len(request_sha256) == 32
        self.calls.append((action, actor.user_id, operation_id))

    def trusted_devices(self, actor: WebActor) -> tuple[TrustedDeviceWebItem, ...]:
        assert actor
        return (TrustedDeviceWebItem(self.key_reference, "Phone", "ANDROID", "TRUSTED", 1),)

    def manage_trusted_device(
        self,
        actor: WebActor,
        key_reference: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
    ) -> None:
        assert actor and key_reference == self.key_reference and len(request_sha256) == 32
        self.calls.append((action, actor.user_id, operation_id))


def _client(
    renderer: Renderer | None = None,
    admission: _Admission | None = None,
    *,
    discovery_enabled: bool = False,
    discovery_automation_enabled: bool = False,
) -> tuple[TestClient, _Web]:
    web = _Web()
    commands = _Commands()
    web.commands = commands
    app = FastAPI()
    app.include_router(
        create_admin_web_router(
            web=web,
            views=_Views(),
            commands=commands,
            renderer=renderer or _Renderer(),
            origin="https://admin.test",
            source_secret=b"s" * 32,
            discovery_enabled=discovery_enabled,
            discovery_automation_enabled=discovery_automation_enabled,
            device_admission=admission,
        )
    )
    return TestClient(app, base_url="https://admin.test"), web


def _form(text: str) -> dict[str, str]:
    return dict(re.findall(r'name="([^\"]+)" value="([^\"]*)"', text))


def test_connection_request_uses_body_only_locator_and_session_scoped_review() -> None:
    admission = _Admission()
    client, web = _client(AdminTemplateRenderer(), admission)
    client.cookies.set("__Host-autplay_admin", "session")
    start = client.get("/admin/connection-requests")
    assert start.status_code == 200 and "locator-secret" not in start.text
    form = _form(start.text) | {"review_locator": "locator-secret"}
    missing_origin = client.post("/admin/connection-requests/resolve", data=form)
    assert missing_origin.status_code == 403 and not admission.calls
    resolved = client.post(
        "/admin/connection-requests/resolve",
        data=form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert resolved.status_code == 303 and "locator-secret" not in resolved.headers["location"]
    reviewed = client.get("/admin/connection-requests/review")
    assert reviewed.status_code == 200 and "&lt;script&gt;" in reviewed.text
    assert "0123" in reviewed.text and "4567" in reviewed.text and "8901" in reviewed.text
    assert "locator-secret" not in reviewed.text and "review_binding" not in reviewed.text
    decision = _form(reviewed.text)
    applied = client.post(
        "/admin/connection-requests/decision/approve-once",
        data=decision,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert applied.status_code == 303 and admission.calls[-1][0] == "APPROVE_ONCE"
    assert admission.calls[-1][1] == web.actor.user_id


def test_trusted_device_actions_are_exact_and_consequence_specific() -> None:
    admission = _Admission()
    client, _ = _client(AdminTemplateRenderer(), admission)
    client.cookies.set("__Host-autplay_admin", "session")
    page = client.get("/admin/trusted-devices")
    assert page.status_code == 200
    assert "Remove trust" in page.text and "Revoke access and remove trust" in page.text
    form = _form(page.text)
    response = client.post(
        f"/admin/trusted-devices/{admission.key_reference}/revoke-and-remove",
        data=form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and admission.calls[-1][0] == "REVOKE_AND_REMOVE"


def test_login_and_security_headers() -> None:
    client, web = _client()
    get = client.get("/admin/login")
    assert (
        get.status_code == 200
        and "no-store" in get.headers["cache-control"]
        and "__Host-autplay_login=" in get.headers["set-cookie"]
    )
    form = {
        "preauth_nonce": "nonce",
        "challenge_id": str(web.challenge.challenge_id),
        "operation_id": str(web.challenge.login_operation_id),
        "browser_invitation": "invite",
    }
    post = client.post(
        "/admin/login", data=form, headers={"Origin": "https://admin.test"}, follow_redirects=False
    )
    assert post.status_code == 303 and "__Host-autplay_admin=" in post.headers["set-cookie"]


def test_bad_origin_clears_preauth_and_static_is_immutable() -> None:
    client, web = _client()
    client.get("/admin/login")
    form = {
        "preauth_nonce": "nonce",
        "challenge_id": str(web.challenge.challenge_id),
        "operation_id": str(web.challenge.login_operation_id),
        "browser_invitation": "invite",
    }
    failed = client.post("/admin/login", data=form, headers={"Origin": "https://wrong.test"})
    assert failed.status_code == 403 and "Max-Age=0" in failed.headers["set-cookie"]
    asset = client.get("/admin/static/admin-v1.css")
    assert (
        asset.status_code == 200
        and "immutable" in asset.headers["cache-control"]
        and asset.headers["etag"].startswith('"sha256-')
        and asset.headers["content-security-policy"]
    )


def test_protected_page_redirects_without_cookie() -> None:
    client, _ = _client()
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/login"


def test_login_outcome_unknown_is_stable_and_rate_key_is_not_source() -> None:
    client, web = _client()
    client.get("/admin/login")
    web.login_fails = True
    failed = client.post(
        "/admin/login",
        data={
            "preauth_nonce": "nonce",
            "challenge_id": str(web.challenge.challenge_id),
            "operation_id": str(web.challenge.login_operation_id),
            "browser_invitation": "invite",
        },
        headers={"Origin": "https://admin.test"},
    )
    assert failed.status_code == 403
    assert "browser_login_outcome_unknown" in failed.text
    assert web.rate_sources and web.rate_sources[-1] != b"testclient"


def test_committed_login_receipt_precedes_rate_limit_and_get_is_bounded() -> None:
    client, web = _client()
    login = client.get("/admin/login")
    assert login.status_code == 200 and web.challenge_sources
    web.login_receipt = True
    web.rate_limited = True
    failed = client.post(
        "/admin/login",
        data={
            "preauth_nonce": "nonce",
            "challenge_id": str(web.challenge.challenge_id),
            "operation_id": str(web.challenge.login_operation_id),
            "browser_invitation": "invite",
        },
        headers={"Origin": "https://admin.test"},
    )
    assert failed.status_code == 403
    assert "browser_login_outcome_unknown" in failed.text
    assert web.rate_sources == []

    web.challenge_rate_limited = True
    limited = client.get("/admin/login")
    assert limited.status_code == 429 and limited.headers["retry-after"] == "900"


def test_safe_get_rotates_only_when_due_and_head_never_rotates() -> None:
    client, web = _client()
    client.cookies.set("__Host-autplay_admin", "session")
    plain = client.get("/admin/devices")
    assert plain.status_code == 200 and "__Host-autplay_admin=" not in plain.headers.get(
        "set-cookie", ""
    )
    web.rotate = True
    rotated = client.get("/admin/")
    assert (
        rotated.status_code == 200
        and "__Host-autplay_admin=rotated" in rotated.headers["set-cookie"]
    )
    head = client.head("/admin/devices")
    assert head.status_code == 200 and "__Host-autplay_admin=" not in head.headers.get(
        "set-cookie", ""
    )
    assert web.head_calls[-1] is True


def test_actual_renderer_renders_dashboard_table_and_status() -> None:
    client, _ = _client(AdminTemplateRenderer())
    login = client.get("/admin/login")
    assert login.status_code == 200 and 'name="challenge_id"' in login.text
    client.cookies.set("__Host-autplay_admin", "session")
    for path, text in (
        ("/admin/", "Test server"),
        ("/admin/devices", "Phone"),
        ("/admin/vault", "This information is unavailable"),
    ):
        response = client.get(path)
        assert response.status_code == 200 and text in response.text


def test_dashboard_navigation_discovers_enabled_automation() -> None:
    client, _ = _client(
        AdminTemplateRenderer(),
        discovery_enabled=True,
        discovery_automation_enabled=True,
    )
    client.cookies.set("__Host-autplay_admin", "session")

    page = client.get("/admin/?lang=ru")

    assert page.status_code == 200
    assert "Автопоиск музыки" in page.text
    assert 'href="/admin/discovery/automation?lang=ru"' in page.text


def test_router_rejects_short_source_secret() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        create_admin_web_router(
            web=_Web(),
            views=_Views(),
            commands=_Commands(),
            renderer=_Renderer(),
            origin="https://admin.test",
            source_secret=b"short",
        )


def _confirmation_form(
    client: TestClient, web: _Web, action: str, target_id: UUID
) -> dict[str, str]:
    client.cookies.set("__Host-autplay_admin", "session")
    page = client.get(f"/admin/confirm/{action}/{target_id}")
    assert page.status_code == 200
    fields = dict(re.findall(r'name="([^\"]+)" value="([^\"]*)"', page.text))
    assert fields["csrf_token"] == "Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M"
    return fields


def test_confirmation_cancel_origin_csrf_and_exact_duplicate_are_guarded() -> None:
    client, web = _client(AdminTemplateRenderer())
    target = uuid4()
    form = _confirmation_form(client, web, "invitation", target)
    assert not web.commands.calls
    wrong_origin = client.post(f"/admin/confirm/invitation/{target}", data=form)
    assert wrong_origin.status_code == 400 and not web.commands.calls
    form["csrf_token"] = "wrong"
    rejected = client.post(
        f"/admin/confirm/invitation/{target}", data=form, headers={"Origin": "https://admin.test"}
    )
    assert rejected.status_code == 403 and not web.commands.calls
    form["csrf_token"] = "Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M"
    applied = client.post(
        f"/admin/confirm/invitation/{target}",
        data=form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    repeated = client.post(
        f"/admin/confirm/invitation/{target}",
        data=form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert applied.status_code == repeated.status_code == 303
    assert len(web.commands.calls) == 2
    assert web.commands.calls[0].operation_id == web.commands.calls[1].operation_id


def test_due_rotation_refuses_command_and_revoked_retry_clears_cookie() -> None:
    client, web = _client(AdminTemplateRenderer())
    target = web.actor.web_session_id
    form = _confirmation_form(client, web, "logout-current", target)
    web.mutation_due = True
    due = client.post(
        f"/admin/confirm/logout-current/{target}",
        data=form,
        headers={"Origin": "https://admin.test"},
    )
    assert due.status_code == 409 and not web.commands.calls
    web.mutation_due = False
    web.revoked = True
    retried = client.post(
        f"/admin/confirm/logout-current/{target}",
        data=form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert retried.status_code == 303 and web.retry_calls == 1
    assert "Max-Age=0" in retried.headers["set-cookie"]

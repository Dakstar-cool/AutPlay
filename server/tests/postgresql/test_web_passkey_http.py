"""Passkey HTTP ceremonies with real PostgreSQL and M6 cookies/CSRF."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from runtime.test_admin_web_http import _Commands, _Views
from starlette.testclient import TestClient
from test_webauthn import ORIGIN, _vector

from autplay.entrypoints.admin_web_http import create_admin_web_router
from autplay.entrypoints.web_passkey_http import create_web_passkey_router
from autplay.runtime.web_security import encode_request_integrity_token
from autplay.web.renderer import AdminTemplateRenderer

from .test_web_passkeys import Harness, _decode, _register
from .test_web_passkeys import harness as harness


def test_passkey_database_wait_does_not_block_unrelated_async_request(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()

    def blocked_gate(source: bytes) -> None:
        del source
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(harness.web, "login_challenge_rate_gate", blocked_gate)
    client = _client(harness)
    app = client.app
    assert isinstance(app, FastAPI)

    @app.get("/event-loop-probe")
    async def probe() -> dict[str, bool]:
        return {"responding": True}

    with client, ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(
            client.post,
            "/admin/login/passkey/options",
            headers={"Origin": ORIGIN},
            data={"preauth_nonce": "n" * 43},
        )
        try:
            assert entered.wait(2)
            ping = pool.submit(client.get, "/event-loop-probe")
            assert ping.result(timeout=2).json() == {"responding": True}
            assert not waiting.done()
        finally:
            release.set()
        assert waiting.result(timeout=3).status_code == 403


def _client(value: Harness, origin: str = ORIGIN) -> TestClient:
    app = FastAPI()
    renderer = AdminTemplateRenderer()
    app.include_router(
        create_web_passkey_router(
            web=value.web,
            passkeys=value.passkeys,
            renderer=renderer,
            origin=origin,
            source_secret=b"http-test-source-secret-at-least-32-bytes",
        )
    )
    app.include_router(
        create_admin_web_router(
            web=value.web,
            views=_Views(),
            commands=_Commands(),
            renderer=renderer,
            origin=origin,
            source_secret=b"http-test-source-secret-at-least-32-bytes",
            passkeys_enabled=True,
        )
    )
    return TestClient(app, base_url=origin)


def test_browser_registration_is_origin_csrf_and_operation_bound(harness: Harness) -> None:
    client = _client(harness)
    form = {
        "csrf_token": encode_request_integrity_token(harness.bootstrap.csrf),
        "operation_id": str(uuid4()),
    }
    headers = {"Origin": ORIGIN}
    assert client.post("/admin/passkeys/options", data=form, headers=headers).status_code == 403
    client.cookies.set("__Host-autplay_admin", harness.bootstrap.bearer.decode())
    assert client.post("/admin/passkeys/options", data=form).status_code == 403
    assert (
        client.post(
            "/admin/passkeys/options", data={**form, "csrf_token": "wrong"}, headers=headers
        ).status_code
        == 403
    )
    first = client.post("/admin/passkeys/options", data=form, headers=headers)
    second = client.post("/admin/passkeys/options", data=form, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    options = first.json()
    key = ec.generate_private_key(ec.SECP256R1())
    payload = _vector(key, registration=True, challenge=_decode(options["publicKey"]["challenge"]))
    registration = client.post(
        "/admin/passkeys/verify",
        headers=headers,
        data={
            **form,
            "ceremony_id": options["ceremony_id"],
            "credential": payload,
            "label": "Laptop",
        },
    )
    assert registration.status_code == 200
    page = client.get("/admin/passkeys?lang=ru")
    assert page.status_code == 200 and "Laptop" in page.text and "Ключи входа" in page.text
    assert first.headers["cache-control"] == "no-store"
    assert "credential_id" not in page.text


def test_login_cookie_and_exact_self_revocation_receipt(harness: Harness) -> None:
    passkey_id, key, handle = _register(harness)
    client = _client(harness)
    page = client.get("/admin/login?lang=ru")
    assert page.status_code == 200 and "Войти \u0441 ключом доступа" in page.text
    nonce = re.search(r'name="preauth_nonce" value="([^"]+)"', page.text)
    assert nonce is not None
    headers = {"Origin": ORIGIN}
    options_response = client.post(
        "/admin/login/passkey/options", headers=headers, data={"preauth_nonce": nonce[1]}
    )
    assert options_response.status_code == 200
    options = options_response.json()
    assert not options["publicKey"].get("allowCredentials")
    payload = _vector(
        key,
        registration=False,
        challenge=_decode(options["publicKey"]["challenge"]),
        handle=handle,
        counter=2,
    )
    signed_in = client.post(
        "/admin/login/passkey/verify",
        headers=headers,
        data={
            "preauth_nonce": nonce[1],
            "ceremony_id": options["ceremony_id"],
            "operation_id": options["operation_id"],
            "credential": payload,
        },
    )
    assert signed_in.status_code == 200 and signed_in.json() == {"signed_in": True}
    assert (
        "HttpOnly" in signed_in.headers["set-cookie"]
        and "Secure" in signed_in.headers["set-cookie"]
    )
    bearer = client.cookies.get("__Host-autplay_admin")
    assert bearer is not None
    actor = harness.web.authenticate(bearer.encode(), mutation=True)
    form = {"csrf_token": encode_request_integrity_token(actor.csrf), "operation_id": str(uuid4())}
    path = f"/admin/passkeys/{passkey_id}/revoke"
    response = client.post(path, data=form, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    repeated = client.post(path, data=form, headers=headers, follow_redirects=False)
    assert repeated.status_code == 303 and repeated.headers["location"] == "/admin/login"
    client.cookies.set("__Host-autplay_admin", bearer)
    assert (
        client.post(path, data={**form, "operation_id": str(uuid4())}, headers=headers).status_code
        == 403
    )
    assert (
        client.post(path, data={**form, "csrf_token": "changed"}, headers=headers).status_code
        == 403
    )
    assert (
        client.post(f"/admin/passkeys/{uuid4()}/revoke", data=form, headers=headers).status_code
        == 403
    )


def test_passkey_payload_bounds_and_static_integrity(harness: Harness) -> None:
    client = _client(harness)
    script = client.get("/admin/static/passkeys-v1.js")
    assert script.status_code == 200 and "navigator.credentials" in script.text
    assert "immutable" in script.headers["cache-control"]
    assert "localStorage" not in script.text and "sessionStorage" not in script.text
    response = client.post(
        "/admin/login/passkey/verify",
        content="x" * 20_000,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert response.status_code == 403 and response.json() == {"error": "passkey_invalid"}

"""Two virtual authenticators over local HTTPS with real PostgreSQL and bundled UI."""

from __future__ import annotations

import asyncio
import socket
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile
from starlette.responses import HTMLResponse, Response

from autplay.adapters.postgresql.web_passkeys import SqlAlchemyWebPasskeyUnitOfWorkFactory
from autplay.adapters.webauthn import DuoWebPasskeyVerifier
from autplay.application.web_passkeys import WebPasskeyService
from autplay.runtime.web_security import apply_admin_security_headers, require_exact_origin

from .test_web_passkey_http import _client
from .test_web_passkeys import SECRET, Harness
from .test_web_passkeys import harness as harness

EVIDENCE = Path(__file__).resolve().parents[3] / ".codex-state" / "admin-implementation-20260916"


def _tls_files(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = directory / "fixture-key.pem", directory / "fixture-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def _virtual_page(context: BrowserContext, origin: str, harness: Harness) -> Page:
    context.add_cookies(
        [
            {
                "name": "__Host-autplay_admin",
                "value": harness.bootstrap.bearer.decode(),
                "url": origin,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
            }
        ]
    )
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    return page


def _register(page: Page, origin: str, label: str) -> None:
    page.goto(origin + "/admin/passkeys?lang=en")
    page.get_by_label("Key name, such as Laptop or M55").fill(label)
    page.get_by_role("button", name="Add a key on this device").click()
    expect(page.get_by_role("heading", name=label, exact=True)).to_be_visible(timeout=15_000)


def _login(page: Page, context: BrowserContext, origin: str) -> None:
    context.clear_cookies()
    page.goto(origin + "/admin/login?lang=en")
    page.get_by_role("button", name="Sign in with a passkey", exact=True).click()
    expect(page.get_by_role("heading", name="Server overview", exact=True)).to_be_visible(
        timeout=15_000
    )


def test_two_passkeys_sign_in_independently_and_revocation_closes_sessions(
    harness: Harness,
    tmp_path: Path,
) -> None:
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    origin = f"https://localhost:{port}"
    harness.passkeys = WebPasskeyService(
        SqlAlchemyWebPasskeyUnitOfWorkFactory(
            sessionmaker(harness.engine, class_=Session, expire_on_commit=False),
        ),
        DuoWebPasskeyVerifier(origin),
        SECRET,
    )
    app = _client(harness, origin).app
    assert isinstance(app, FastAPI)
    uploads: list[bytes] = []

    def transport_page() -> Response:
        # A small fixture exercises the shared transport's non-redirect HTML and multipart path.
        return apply_admin_security_headers(
            HTMLResponse(
                '<!doctype html><html><head><script defer src="/admin/static/admin-forms-v1.js">'
                '</script></head><body data-action-error="Request failed">'
                '<main id="main" tabindex="-1">'
                f'<p>Uploads: {len(uploads)}</p><form method="post" enctype="multipart/form-data" '
                'action="/admin/transport-fixture/page"><input type="file" name="collection">'
                '<button type="submit">Upload fixture</button></form></main></body></html>'
            )
        )

    @app.get("/admin/transport-fixture/page")
    def transport_get() -> Response:
        return transport_page()

    @app.post("/admin/transport-fixture/page")
    async def transport_post(request: Request) -> Response:
        require_exact_origin(request.scope, origin)
        async with request.form(max_files=1, max_fields=0) as fields:
            upload = fields["collection"]
            assert isinstance(upload, UploadFile)
            uploads.append(await upload.read(100))
        return transport_page()

    key_path, cert_path = _tls_files(tmp_path)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ssl_keyfile=str(key_path),
            ssl_certfile=str(cert_path),
            log_level="error",
            access_log=False,
        )
    )

    def run_server() -> None:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            threading.Event().wait(0.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            laptop = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 1000}
            )
            phone = browser.new_context(
                ignore_https_errors=True, viewport={"width": 390, "height": 844}
            )
            first = _virtual_page(laptop, origin, harness)
            second = _virtual_page(phone, origin, harness)
            _register(first, origin, "Laptop fixture")
            _register(second, origin, "M55 fixture")
            _login(first, laptop, origin)
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            first.screenshot(path=str(EVIDENCE / "desktop-dashboard.png"), full_page=True)
            second.goto(origin + "/admin/passkeys?lang=ru")
            assert second.evaluate("document.documentElement.scrollWidth <= innerWidth")
            second.screenshot(path=str(EVIDENCE / "mobile-passkeys.png"), full_page=True)
            second.goto(origin + "/admin/passkeys?lang=en")
            card = second.locator("article").filter(
                has=second.get_by_role("heading", name="Laptop fixture", exact=True)
            )
            card.locator("summary").click()
            with second.expect_request(lambda request: request.url.endswith("/revoke")) as sent:
                card.get_by_role("button", name="Revoke key and end its sessions").click()
            assert sent.value.header_value("origin") == origin
            expect(card.get_by_text("Key revoked", exact=True)).to_be_visible()
            first.goto(origin + "/admin/")
            expect(
                first.get_by_role("button", name="Sign in with a passkey", exact=True)
            ).to_be_visible()
            _login(second, phone, origin)
            second.goto(origin + "/admin/transport-fixture/page")
            for count in (1, 2):
                second.locator('input[type="file"]').set_input_files(
                    {"name": "fixture.txt", "mimeType": "text/plain", "buffer": b"fixture data"}
                )
                second.get_by_role("button", name="Upload fixture").click()
                expect(second.get_by_text(f"Uploads: {count}", exact=True)).to_be_visible()
            assert uploads == [b"fixture data", b"fixture data"]
            second.goto(origin + "/admin/?lang=en")
            second.get_by_role("link", name="Sign out", exact=True).click()
            with second.expect_request(lambda request: request.method == "POST") as logout:
                second.get_by_role("button", name="Sign out", exact=True).click()
            assert logout.value.header_value("origin") == origin
            expect(
                second.get_by_role("button", name="Sign in with a passkey", exact=True)
            ).to_be_visible()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()

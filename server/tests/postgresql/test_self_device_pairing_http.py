"""Self-service HTTP bounds and real authenticated USER enrollment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from pydantic import SecretStr
from starlette.testclient import TestClient

from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.composition import build_auth_service
from autplay.entrypoints.self_device_pairing_http import create_self_device_pairing_router
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings

from .test_self_device_pairing import PairingHarness
from .test_self_device_pairing import pair as pair


def _client(pair: PairingHarness, *, enabled: bool = True) -> tuple[TestClient, str]:
    secret = b"self-pairing-test-secret-at-least-32-bytes"
    settings = ApiSettings(
        database_url=pair.engine.url.render_as_string(hide_password=False),
        auth_signing_secret=SecretStr(secret.decode()),
        auth_issuer="test",
        auth_audience="test",
        public_access_source_hmac_secret=SecretStr(
            "http-test-public-source-secret-at-least-32bytes"
        ),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_self_device_pairing_router(
            pair.service if enabled else None,
            authenticated=bearer_authentication(build_auth_service(settings, pair.engine)),
        ),
        prefix="/api/v1",
    )
    now = datetime.now(UTC)
    token = Hs256AccessTokenCodec(secret, issuer="test", audience="test").issue(
        pair.actor,
        token_id=uuid4(),
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    return TestClient(app), token


def test_http_user_must_authenticate_and_new_phone_must_prove_possession(
    pair: PairingHarness,
) -> None:
    client, token = _client(pair)
    start = pair.start()
    path = "/api/v1/account/device-pairings"
    assert client.post(path, json=start).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}
    opened = client.post(path, json=start, headers=auth)
    assert opened.status_code == 200 and opened.json()["state"] == "OPEN"
    claim = pair.claim()
    claim_path = f"/api/v1/pairing/self-service/{pair.ceremony_id}/claim"
    assert client.post(claim_path, json=claim).status_code == 403
    submitted = client.post(
        claim_path, json=claim, headers={"X-AutPlay-Pairing-Secret": pair.rendezvous}
    )
    assert submitted.status_code == 200 and "account_id" not in submitted.json()
    decision = pair.approve(submitted.json())
    assert (
        client.post(f"{path}/{pair.ceremony_id}/decision", json=decision, headers=auth).status_code
        == 200
    )
    poll = pair.request(
        "poll",
        {
            "claim_id": claim["claim_id"],
            "claim_request_sha256": claim["request_sha256"],
        },
    )
    status = client.post(
        f"/api/v1/pairing/self-service/{pair.ceremony_id}/poll",
        json=poll,
        headers={"X-AutPlay-Pairing-Secret": pair.poll_secret},
    )
    assert status.status_code == 200 and status.json()["account_id"] == str(pair.actor.user_id)
    assert status.headers["cache-control"] == "no-store"


def test_http_rejects_duplicate_json_oversized_bodies_and_ambiguous_secret(
    pair: PairingHarness,
) -> None:
    client, _ = _client(pair)
    path = "/api/v1/account/device-pairings"
    for body in ('{"schema_version":1,"schema_version":1}', "x" * 9000, "NaN", "[]"):
        response = client.post(path, content=body, headers={"Content-Type": "application/json"})
        assert response.status_code == 400
    pair.service.start(pair.actor, pair.start())
    response = client.post(
        f"/api/v1/pairing/self-service/{pair.ceremony_id}/claim",
        json=pair.claim(),
        headers=[
            ("X-AutPlay-Pairing-Secret", pair.rendezvous),
            ("X-AutPlay-Pairing-Secret", pair.rendezvous),
        ],
    )
    assert response.status_code == 403
    disabled, _ = _client(pair, enabled=False)
    assert disabled.post(path, json=pair.start()).status_code == 503

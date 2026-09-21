"""Deletion HTTP bounds, revoked bearer and explicitly separated cancellation."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.domain.profile_pairing import iso8601
from autplay.entrypoints.account_deletion_http import create_account_deletion_router
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.testclient import TestClient

from .test_account_deletion import PairingHarness, cancel_request, prepared, request
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair


def test_request_loss_is_queryable_but_old_bearer_stays_revoked(pair: PairingHarness) -> None:
    service, code, _, body = prepared(pair)
    secret = "recovery-test-access-secret-distinct-32bytes"
    settings = ApiSettings(
        database_url=SecretStr(pair.engine.url.render_as_string(hide_password=False)),
        auth_signing_secret=SecretStr(secret),
        auth_issuer="test",
        auth_audience="test",
        public_access_source_hmac_secret=SecretStr("deletion-http-source-secret-at-least-32bytes"),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_account_deletion_router(
            service,
            authenticated=bearer_authentication(build_auth_service(settings, pair.engine)),
        )
    )
    now = datetime.now(UTC)
    token = Hs256AccessTokenCodec(secret, issuer="test", audience="test").issue(
        pair.actor,
        token_id=uuid4(),
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    auth = {"Authorization": f"Bearer {token}"}
    proof = {"X-AutPlay-Recovery-Code": code}
    with TestClient(app) as client:
        assert client.get("/account/deletion").status_code == 401
        status = client.get("/account/deletion", headers=auth)
        assert status.json()["can_request"]
        for raw in ('{"schema_version":1,"schema_version":1}', "x" * 8193, "NaN", "[]"):
            response = client.post(
                "/deletion/cancel/preview",
                content=raw,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400 and response.headers["cache-control"] == "no-store"
        assert client.post("/account/deletion", json=body, headers=proof).status_code == 401
        result = client.post("/account/deletion", json=body, headers={**auth, **proof})
        assert result.status_code == 200 and result.json()["state"] == "PENDING"
        assert code not in result.text and result.headers["cache-control"] == "no-store"
        assert client.get("/account/deletion", headers=auth).status_code == 401
        receipt = client.post("/deletion/request-receipt", json=body, headers=proof)
        assert receipt.status_code == 200 and receipt.json()["replayed"]
        preview = client.post(
            "/deletion/cancel/preview", json=request(pair, "preview", pair.key), headers=proof
        )
        assert preview.status_code == 200 and preview.json()["confirmation_required"]
        refresh = secrets.token_urlsafe(32)
        cancel = cancel_request(pair, result.json())
        cancel = request(
            pair,
            "cancel",
            pair.key,
            **{
                **cancel,
                "next_refresh_token_sha256": hashlib.sha256(refresh.encode("ascii")).hexdigest(),
            },
        )
        cancelled = client.post("/deletion/cancel/commit", json=cancel, headers=proof)
        assert cancelled.status_code == 200 and cancelled.json()["access_token"]
        assert client.post("/deletion/cancel/outcome", json=cancel).status_code == 403
        outcome = client.post(
            "/deletion/cancel/outcome",
            json=cancel,
            headers={"X-AutPlay-Recovery-Refresh": refresh},
        )
        assert outcome.status_code == 200
        assert outcome.json()["outcome_recovered"] is True
        assert outcome.headers["cache-control"] == "no-store"
        assert client.get("/account/deletion", headers=auth).status_code == 401
        fresh = client.get(
            "/account/deletion",
            headers={"Authorization": "Bearer " + cancelled.json()["access_token"]},
        )
        assert fresh.status_code == 200 and fresh.json()["authority_generation"] == 3


def test_exact_negative_resolution_needs_no_bearer_and_cannot_be_inferred_from_missing_receipt(
    pair: PairingHarness,
) -> None:
    service, code, key, _ = prepared(pair)
    body = request(
        pair,
        "request",
        key,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        expected_authority_generation=1,
        requested_at=iso8601(datetime.now(UTC) - timedelta(minutes=3)),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_account_deletion_router(
            service,
            authenticated=lambda _: (_ for _ in ()).throw(AssertionError("unexpected bearer")),
        )
    )
    proof = {"X-AutPlay-Recovery-Code": code}
    with TestClient(app) as client:
        missing = client.post("/deletion/request-receipt", json=body, headers=proof)
        assert missing.status_code == 403
        negative = client.post("/deletion/request-resolve", json=body, headers=proof)
        assert negative.status_code == 200 and negative.json()["state"] == "NOT_ACCEPTED"
        assert negative.headers["cache-control"] == "no-store" and code not in negative.text
        again = client.post("/deletion/request-resolve", json=body, headers=proof)
        assert again.status_code == 200 and again.json()["replayed"]
        ledger_path = pair.deletion_ledger.path
        ledger_path.rename(ledger_path.with_suffix(".saved"))
        try:
            unavailable = client.post("/deletion/request-resolve", json=body, headers=proof)
            assert unavailable.status_code == 503
            assert unavailable.headers["cache-control"] == "no-store"
        finally:
            ledger_path.with_suffix(".saved").rename(ledger_path)

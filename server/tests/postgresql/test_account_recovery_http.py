"""Recovery HTTP validation, authentication replacement and durable attempt limits."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.domain.account_recovery import AccountRecoveryError
from autplay.entrypoints.account_recovery_http import create_account_recovery_router
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from starlette.testclient import TestClient

from .test_account_recovery import configured, request
from .test_self_device_pairing import PairingHarness
from .test_self_device_pairing import pair as pair


def test_http_recovery_replaces_bearer_and_rejects_bad_bodies(pair: PairingHarness) -> None:
    recovery, code, commit = configured(pair)
    refresh = secrets.token_urlsafe(32)
    commit = request(
        pair,
        "recover",
        **{
            **commit,
            "next_refresh_token_sha256": hashlib.sha256(refresh.encode("ascii")).hexdigest(),
        },
    )
    secret = "recovery-test-access-secret-distinct-32bytes"
    settings = ApiSettings(
        database_url=SecretStr(pair.engine.url.render_as_string(hide_password=False)),
        auth_signing_secret=SecretStr(secret),
        auth_issuer="test",
        auth_audience="test",
        public_access_source_hmac_secret=SecretStr("http-recovery-source-secret-at-least-32bytes"),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_account_recovery_router(
            recovery, authenticated=bearer_authentication(build_auth_service(settings, pair.engine))
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
    with TestClient(app) as client:
        assert client.get("/account/recovery").status_code == 401
        status = client.get("/account/recovery", headers=auth)
        assert status.json()["configured"]
        assert status.json()["account_label"]
        assert status.headers["cache-control"] == "no-store"
        for raw in ('{"schema_version":1,"schema_version":1}', "x" * 8193, "NaN", "[]"):
            reply = client.post(
                "/recovery/preview", content=raw, headers={"Content-Type": "application/json"}
            )
            assert reply.status_code == 400 and reply.headers["cache-control"] == "no-store"
        reply = client.post(
            "/recovery/preview",
            json=request(pair, "preview", app_version="\0"),
            headers={"X-AutPlay-Recovery-Code": code},
        )
        assert reply.status_code == 400 and "\0" not in reply.text
        assert client.post("/recovery/commit", json=commit).status_code == 403
        assert (
            client.post(
                "/recovery/commit",
                json=commit,
                headers=[("X-AutPlay-Recovery-Code", code), ("X-AutPlay-Recovery-Code", code)],
            ).status_code
            == 403
        )
        reply = client.post(
            "/recovery/commit", json=commit, headers={"X-AutPlay-Recovery-Code": code}
        )
        assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
        assert code not in reply.text
        assert client.post("/recovery/outcome", json=commit).status_code == 403
        outcome = client.post(
            "/recovery/outcome",
            json=commit,
            headers={"X-AutPlay-Recovery-Refresh": refresh},
        )
        assert outcome.status_code == 200
        assert outcome.json()["outcome_recovered"] is True
        assert outcome.headers["cache-control"] == "no-store"
        assert client.get("/account/recovery", headers=auth).status_code == 401
        assert (
            client.get(
                "/account/recovery",
                headers={"Authorization": "Bearer " + reply.json()["access_token"]},
            ).status_code
            == 200
        )


def test_account_attempts_are_durable_and_feature_requires_persistent_identity(
    pair: PairingHarness,
) -> None:
    recovery, _, _ = configured(pair)
    for _ in range(30):
        recovery.rate_gate("", pair.actor.user_id)
    with pytest.raises(AccountRecoveryError, match="account_recovery_rate_limited"):
        recovery.rate_gate("", pair.actor.user_id)
    with pytest.raises(ValidationError, match="persistent profile identity"):
        ApiSettings(
            account_recovery_enabled=True,
            database_url=SecretStr(pair.engine.url.render_as_string(hide_password=False)),
            auth_signing_secret=SecretStr("recovery-test-access-secret-distinct-32bytes"),
            public_access_source_hmac_secret=SecretStr(
                "http-recovery-source-secret-at-least-32bytes"
            ),
        )

"""Actual JWT and PostgreSQL admission through strict bounded HTTP documents."""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.auth import AuthService
from autplay.domain.auth import Principal
from autplay.domain.resource_admission import ActivationFence, AdmissionStatus, ResourceRequest
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.composition import build_auth_service
from autplay.entrypoints.resource_admission_http import create_resource_admission_router
from autplay.runtime.http import install_error_handlers
from autplay.runtime.settings import ApiSettings

from .test_resource_admission_runtime import AdmissionHarness
from .test_resource_admission_runtime import admission as admission

_BASE = "/api/v1/account/resource-admissions"
_VERSION = {"contract_version": "v1", "schema_version": 1}
_SECRET = b"admission-http-synthetic-auth-secret-at-least32bytes"


@dataclass
class HttpHarness:
    admission: AdmissionHarness
    authentication: AuthService
    client: TestClient
    actor: Principal

    def headers(self, actor: Principal | None = None) -> dict[str, str]:
        now = datetime.now(UTC)
        token = Hs256AccessTokenCodec(_SECRET, issuer="quota", audience="quota").issue(
            actor or self.actor,
            token_id=uuid4(),
            issued_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def http(admission: AdmissionHarness) -> Iterator[HttpHarness]:
    settings = ApiSettings(
        database_url=SecretStr(admission.engine.url.render_as_string(hide_password=False)),
        auth_signing_secret=SecretStr(_SECRET.decode()),
        auth_issuer="quota",
        auth_audience="quota",
        public_access_source_hmac_secret=SecretStr("quota-http-source-secret-at-least32bytes"),
    )
    authentication = build_auth_service(settings, admission.engine)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_resource_admission_router(
            admission.service, authenticated=bearer_authentication(authentication)
        ),
        prefix="/api/v1",
    )
    with TestClient(app) as client:
        yield HttpHarness(admission, authentication, client, admission.actor())


def _play() -> dict[str, object]:
    return {
        **_VERSION,
        "operation_id": str(uuid4()),
        "kind": "PLAYBACK",
        "resource_type": "PLAY_INSTANCE",
        "resource_id": str(uuid4()),
    }


def _fence(status: dict[str, object]) -> dict[str, object]:
    return {
        **_VERSION,
        "activation_id": status["activation_id"],
        "generation": status["generation"],
    }


def test_http_playback_waiting_attach_release_and_exact_replay(http: HttpHarness) -> None:
    request, headers = _play(), http.headers()
    assert http.client.post(_BASE, json=request).status_code == 401
    unavailable = http.client.post(_BASE, json=request, headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "resource_budget_unconfigured"
    http.admission.budget(playbacks=1)
    response = http.client.post(_BASE, json=request, headers=headers)
    active = response.json()
    assert response.status_code == 200 and active["state"] == "ACTIVE"
    assert response.headers["cache-control"] == "no-store"
    assert all(not isinstance(value, dict | list) for value in active.values())
    assert "user_id" not in active and "session_family_id" not in active
    replay = http.client.post(_BASE, json=request, headers=headers).json()
    assert replay["lease_until"] == active["lease_until"]
    other = http.admission.actor()
    other_headers = http.headers(other)
    queued = http.client.post(_BASE, json=_play(), headers=other_headers).json()
    assert queued["state"] == "WAITING" and queued["waiting_reason"] == "SERVER_CAPACITY"
    assert queued["activation_id"] is None and queued["generation"] is None
    assert queued["claim_until"] is None and queued["lease_until"] is None
    assert queued["retry_after_seconds"] == 5
    path = f"{_BASE}/{active['operation_id']}"
    assert http.client.get(path, headers=other_headers).status_code == 403
    recording = http.admission.recording(http.actor)
    attached = http.client.post(
        f"{path}/attachments",
        headers=headers,
        json={
            **_fence(active),
            "expected_attachment_revision": 0,
            "current_recording_id": str(recording),
            "next_recording_id": None,
        },
    )
    assert attached.status_code == 200 and attached.json()["attachment_revision"] == 1
    assert (
        http.client.post(f"{path}/renew", json=_fence(active), headers=headers).status_code == 200
    )
    assert (
        http.client.post(
            f"{path}/release",
            headers=headers,
            json={**_fence(active), "activation_id": str(uuid4()), "reason": "COMPLETE"},
        ).status_code
        == 409
    )
    for _ in range(2):
        released = http.client.post(
            f"{path}/release", json={**_fence(active), "reason": "COMPLETE"}, headers=headers
        )
        assert released.status_code == 200 and released.json()["state"] == "RELEASED"
    assert (
        http.client.get(f"{_BASE}/{queued['operation_id']}", headers=other_headers).json()["state"]
        == "ACTIVE"
    )


def test_http_transfer_targets_share_capacity_and_waiting_cancel_binds_digest(
    http: HttpHarness,
) -> None:
    http.admission.budget(transfers=1)
    headers = http.headers()
    upload = {
        **_VERSION,
        "operation_id": str(uuid4()),
        "kind": "TRANSFER",
        "resource_type": "UPLOAD_INTENT",
        "resource_id": str(uuid4()),
        "target_id": str(http.admission.upload(http.actor)),
    }
    first = http.client.post(_BASE, json=upload, headers=headers)
    assert first.status_code == 200 and first.json()["state"] == "ACTIVE"
    download = {
        **upload,
        "operation_id": str(uuid4()),
        "resource_id": str(uuid4()),
        "resource_type": "DOWNLOAD_INTENT",
        "target_id": str(http.admission.variant(http.actor)),
    }
    response = http.client.post(_BASE, json=download, headers=headers)
    queued = response.json()
    assert response.status_code == 200 and queued["state"] == "WAITING"
    path = f"{_BASE}/{queued['operation_id']}/release"
    cancel = {**_VERSION, "reason": "CANCEL", "operation_sha256": queued["operation_sha256"]}
    assert (
        http.client.post(
            path, headers=headers, json={**cancel, "operation_sha256": "0" * 64}
        ).status_code
        == 409
    )
    for _ in range(2):
        assert http.client.post(path, headers=headers, json=cancel).json()["state"] == "RELEASED"
    assert (
        http.client.post(
            _BASE, headers=headers, json={**upload, "target_id": download["target_id"]}
        ).status_code
        == 409
    )
    other = http.admission.actor()
    assert (
        http.client.post(
            _BASE, headers=http.headers(other), json={**upload, "operation_id": str(uuid4())}
        ).status_code
        == 403
    )


def test_http_rejects_ambiguous_unbounded_or_noncanonical_documents(http: HttpHarness) -> None:
    request = _play()
    headers = {**http.headers(), "Content-Type": "application/json"}
    documents: list[str | bytes] = [
        "[]",
        "NaN",
        "{}",
        "x" * 4097,
        b"\xff",
        '{"schema_version":1,"schema_version":1}',
        json.dumps({**request, "schema_version": True}),
        json.dumps({**request, "schema_version": "1"}),
        json.dumps({**request, "schema_version": 1.0}),
        json.dumps({**request, "resource_id": str(uuid4()).upper()}),
        json.dumps({**request, "resource_id": uuid4().hex}),
        json.dumps({**request, "resource_type": "INTERNET_ACQUISITION"}),
        json.dumps({**request, "user_id": str(http.actor.user_id)}),
        json.dumps({**request, "resource_id": {"id": str(uuid4())}}),
        json.dumps({**request, "kind": ["PLAYBACK"]}),
    ]
    for document in documents:
        response = http.client.post(_BASE, content=document, headers=headers)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "resource_request_invalid"
    assert (
        http.client.post(
            _BASE, content=json.dumps(request), headers={**headers, "Content-Type": "text/plain"}
        ).status_code
        == 400
    )
    assert http.client.get(f"{_BASE}/{uuid4().hex}", headers=headers).status_code == 400
    for generation in (True, "1", 1.0, -1, 2**53):
        response = http.client.post(
            f"{_BASE}/{uuid4()}/renew",
            headers=headers,
            json={**_VERSION, "activation_id": str(uuid4()), "generation": generation},
        )
        assert response.status_code == 400


def test_requeued_operation_hides_deadlines_of_draining_activation(http: HttpHarness) -> None:
    http.admission.budget(playbacks=1)
    request, headers = _play(), http.headers()
    active = http.client.post(_BASE, json=request, headers=headers).json()
    fence = ActivationFence(
        UUID(active["operation_id"]), UUID(active["activation_id"]), active["generation"]
    )
    recording = http.admission.recording(http.actor)
    http.admission.service.attach(http.actor, fence, 0, recording, None)
    permit = http.admission.service.open_io(http.actor, fence, recording)
    with http.admission.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.resource_admission SET created_at=created_at-interval '1 minute',"
                "lease_until=clock_timestamp()-interval '1 second'"
            )
        )
    response = http.client.post(_BASE, json=request, headers=headers)
    queued = response.json()
    assert response.status_code == 200 and queued["state"] == "WAITING"
    assert queued["account_usage"] == 1
    for field in ("activation_id", "generation", "claim_until", "lease_until"):
        assert queued[field] is None
    http.admission.service.close_io(permit)


def test_http_queue_pressure_is_bounded_and_no_store(http: HttpHarness) -> None:
    http.admission.budget(playbacks=1)
    headers = http.headers()
    for index in range(22):
        response = http.client.post(_BASE, json=_play(), headers=headers)
        assert response.status_code == (429 if index == 21 else 200)
    assert response.json()["error"]["code"] == "resource_queue_full"
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"


def test_http_stale_authenticated_actor_cannot_acquire(
    http: HttpHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    http.admission.budget()
    authenticate = AuthService.authenticate_access

    def authenticate_then_revoke(service: AuthService, token: str) -> Principal:
        actor = authenticate(service, token)
        service.logout(actor)
        return actor

    monkeypatch.setattr(AuthService, "authenticate_access", authenticate_then_revoke)
    response = http.client.post(_BASE, json=_play(), headers=http.headers())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "resource_admission_unavailable"


def test_http_database_failure_is_sanitized(
    http: HttpHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(actor: Principal, request: ResourceRequest) -> AdmissionStatus:
        del actor, request
        raise OperationalError("private query", {}, Exception("private database origin"))

    monkeypatch.setattr(http.admission.service, "acquire", unavailable)
    response = http.client.post(_BASE, json=_play(), headers=http.headers())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "resource_service_unavailable"
    assert "private" not in response.text


def test_http_blocking_transaction_does_not_block_async_requests(
    http: HttpHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    acquire = http.admission.service.acquire
    http.admission.budget()

    def blocked(actor: Principal, request: ResourceRequest) -> AdmissionStatus:
        entered.set()
        assert release.wait(5)
        return acquire(actor, request)

    monkeypatch.setattr(http.admission.service, "acquire", blocked)
    app = http.client.app
    assert isinstance(app, FastAPI)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"responding": True}

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(http.client.post, _BASE, json=_play(), headers=http.headers())
        try:
            assert entered.wait(2)
            assert pool.submit(http.client.get, "/probe").result(timeout=2).json() == {
                "responding": True
            }
            assert not waiting.done()
        finally:
            release.set()
        assert waiting.result(timeout=3).status_code == 200

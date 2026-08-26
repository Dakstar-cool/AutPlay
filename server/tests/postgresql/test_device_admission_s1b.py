"""Real-PostgreSQL security and lifecycle gates for S1B device admission."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.profile_pairing import (
    ProfilePairingService,
    cleanup_expired_device_admissions,
    cleanup_expired_pairing_receipts,
)
from autplay.application.web_admin import WebAdminService
from autplay.domain.auth import AccountRole
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
)
from autplay.domain.web_admin import WebActor
from autplay.entrypoints.admin_web_http import create_admin_web_router
from autplay.entrypoints.device_admission_http import (
    AdmissionRecovery,
    create_device_admission_router,
)
from autplay.entrypoints.device_admission_web import DeviceAdmissionWebAdapter
from autplay.runtime.http import install_error_handlers
from autplay.web.renderer import AdminTemplateRenderer
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REQUEST_DOMAIN = "autplay:s1b:admission-request:v1\n"
POLL_DOMAIN = "autplay:s1b:admission-poll:v1\n"
RECOVERY_DOMAIN = "autplay:s1b:admission-recovery:v1\n"
EXCHANGE_DOMAIN = "autplay:s1b:admission-exchange:v1\n"
TRUSTED_REENROLLMENT_DOMAIN = "autplay:s1b:trusted-reenrollment:v1\n"


@dataclass(slots=True)
class _Clock:
    value: datetime

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@dataclass(frozen=True, slots=True)
class _Runtime:
    service: ProfilePairingService
    sessions: sessionmaker[Session]
    clock: _Clock


@pytest.fixture
def admission_runtime(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Runtime]:
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    clock = _Clock(datetime(2026, 8, 25, 12, 0, tzinfo=UTC))
    monkeypatch.setattr("autplay.application.profile_pairing._now", lambda: clock.value)
    service = ProfilePairingService(
        sessions,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="S1B test server",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=Hs256AccessTokenCodec(
            b"s1b-test-access-secret-at-least-thirty-two-bytes",
            issuer="s1b",
            audience="s1b",
        ),
        access_ttl=timedelta(minutes=10),
    )
    yield _Runtime(service, sessions, clock)
    engine.dispose()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _nonce(marker: int) -> str:
    return _b64url(bytes([marker % 256]) * 32)


def _jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
    }


def _signed(
    key: ec.EllipticCurvePrivateKey, domain: str, body: dict[str, object]
) -> dict[str, object]:
    result = dict(body)
    result["proof_b64url"] = sign_p1363(key, domain, canonical_sha256(result))
    return result


def _identity(service: ProfilePairingService) -> dict[str, object]:
    envelope = service.discovery()
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    return payload


def _admission_request(
    runtime: _Runtime,
    *,
    key: ec.EllipticCurvePrivateKey | None = None,
    source: str | None = None,
    nonce_marker: int = 1,
) -> tuple[dict[str, object], ec.EllipticCurvePrivateKey, str]:
    device_key = key or ec.generate_private_key(ec.SECP256R1())
    identity = _identity(runtime.service)
    spki = public_spki(device_key)
    request = _signed(
        device_key,
        REQUEST_DOMAIN,
        {
            "request_id": str(uuid4()),
            "expected_server_instance_id": identity["server_instance_id"],
            "expected_identity_epoch": identity["identity_epoch"],
            "expected_identity_thumbprint_sha256": identity["identity_thumbprint_sha256"],
            "client_nonce_b64url": _nonce(nonce_marker),
            "api_major": 1,
            "device_key_thumbprint_sha256": public_key_thumbprint(spki).hex(),
            "device_public_key_jwk": _jwk(device_key),
            "nickname": f"Android {nonce_marker}",
            "device_model_hint": "Pixel test model",
            "platform": "ANDROID",
            "app_version": "s1b-test",
            "requested_at": runtime.clock.value.isoformat().replace("+00:00", "Z"),
        },
    )
    return (
        request,
        device_key,
        canonical_sha256(request, omit=frozenset({"proof_b64url"})).hex(),
    )


def _admission(
    runtime: _Runtime,
    *,
    key: ec.EllipticCurvePrivateKey | None = None,
    source: str | None = None,
    nonce_marker: int = 1,
) -> tuple[dict[str, object], dict[str, object], ec.EllipticCurvePrivateKey, str]:
    request, device_key, request_hash = _admission_request(
        runtime, key=key, source=source, nonce_marker=nonce_marker
    )
    created, replayed = runtime.service.submit_device_admission(request, source=source)
    assert not replayed
    return request, created, device_key, request_hash


def _web_actor(
    runtime: _Runtime,
    connection: Connection[Any],
    role: AccountRole = AccountRole.OWNER,
    *,
    user_id: UUID | None = None,
) -> WebActor:
    identity = _identity(runtime.service)
    if user_id is None:
        row = connection.execute(
            "INSERT INTO account.user_account (display_name, role) "
            "VALUES (%s, %s) RETURNING user_id",
            (f"S1B {role.value}", role.value),
        ).fetchone()
        assert row is not None
        user_id = UUID(str(row[0]))
    session_id = uuid4()
    token_hash = hashlib.sha256(session_id.bytes + b"token").digest()
    csrf_hash = hashlib.sha256(session_id.bytes + b"csrf").digest()
    connection.execute(
        """
        INSERT INTO account.web_session (
          web_session_id, family_id, server_instance_id, user_id, token_generation,
          token_sha256, csrf_sha256, issued_at, token_issued_at, last_activity_at,
          idle_expires_at, absolute_expires_at
        ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            session_id,
            UUID(str(identity["server_instance_id"])),
            user_id,
            token_hash,
            csrf_hash,
            runtime.clock.value,
            runtime.clock.value,
            runtime.clock.value,
            runtime.clock.value + timedelta(minutes=30),
            runtime.clock.value + timedelta(hours=8),
        ),
    )
    connection.commit()
    return WebActor(UUID(str(identity["server_instance_id"])), user_id, session_id, role, 1)


def _bind_and_decide(
    runtime: _Runtime,
    actor: WebActor,
    created: dict[str, object],
    action: str,
) -> None:
    runtime.service.bind_device_admission_review(
        actor=actor,
        web_session_id=actor.web_session_id,
        locator=str(created["review_locator"]),
        operation_id=uuid4(),
        request_sha256=b"r" * 32,
    )
    runtime.service.decide_device_admission(
        actor,
        UUID(str(created["request_id"])),
        action,
        uuid4(),
        b"d" * 32,
        web_session_id=actor.web_session_id,
    )


def _form_values(document: str) -> dict[str, str]:
    return dict(re.findall(r'name="([^\"]+)" value="([^\"]*)"', document))


def test_actual_http_and_web_routers_complete_recovery_review_and_exchange(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    request, key, request_hash = _admission_request(
        admission_runtime, source="127.0.0.1", nonce_marker=70
    )
    request.pop("proof_b64url")
    request["nickname"] = "N" * 120
    request["device_model_hint"] = "M" * 96
    request["app_version"] = "V" * 32
    request = _signed(key, REQUEST_DOMAIN, request)
    request_hash = canonical_sha256(request, omit=frozenset({"proof_b64url"})).hex()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_device_admission_router(admission_runtime.service), prefix="/api/v1")

    owner_row = database_connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('S1B HTTP owner', 'OWNER') RETURNING user_id"
    ).fetchone()
    assert owner_row is not None
    owner_id = UUID(str(owner_row[0]))
    database_connection.commit()
    web_service = WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(admission_runtime.sessions),
        csrf_secret=b"s1b-http-web-csrf-secret-is-long",
    )
    web_now = datetime.now(UTC)
    invitation = web_service.issue_invitation(owner_id, now=web_now)
    login_challenge = web_service.begin_login(now=web_now)
    credentials = web_service.login(login_challenge, invitation.bearer, b"l" * 32, now=web_now)
    app.include_router(
        create_admin_web_router(
            web=web_service,
            views=cast(Any, object()),
            commands=cast(Any, object()),
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            source_secret=b"s1b-http-source-secret-is-long-enough",
            device_admission=DeviceAdmissionWebAdapter(admission_runtime.service),
        )
    )
    client = TestClient(app, base_url="https://admin.test")
    invalid = dict(request)
    invalid["app_version"] = "V" * 33
    invalid_response = client.post("/api/v1/social/admission-requests", json=invalid)
    assert invalid_response.status_code == 422
    submitted = client.post("/api/v1/social/admission-requests", json=request)
    assert submitted.status_code == 202
    assert submitted.headers["cache-control"] == "no-store"
    created = submitted.json()
    assert str(created["review_locator"]) not in submitted.request.url.path
    assert str(created["poll_bearer"]) not in submitted.request.url.path

    recovery = _signed(
        key,
        RECOVERY_DOMAIN,
        {
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "expected_server_instance_id": request["expected_server_instance_id"],
            "expected_identity_epoch": request["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": request["expected_identity_thumbprint_sha256"],
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "recovery_nonce_b64url": _nonce(71),
        },
    )
    recovered = client.post(
        f"/api/v1/social/admission-requests/{request['request_id']}/recover",
        json=recovery,
    )
    assert recovered.status_code == 200 and recovered.headers["cache-control"] == "no-store"
    rotated = recovered.json()
    assert rotated["review_locator"] != created["review_locator"]

    client.cookies.set("__Host-autplay_admin", credentials.bearer.decode("ascii"))
    review_start = client.get("/admin/connection-requests")
    resolve_form = _form_values(review_start.text) | {
        "review_locator": str(rotated["review_locator"])
    }
    resolved = client.post(
        "/admin/connection-requests/resolve",
        data=resolve_form,
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert resolved.status_code == 303
    review = client.get("/admin/connection-requests/review")
    assert review.status_code == 200
    assert "M" * 96 in review.text
    assert "API version" in review.text and ">1<" in review.text
    assert "Requested" in review.text
    assert str(rotated["review_locator"]) not in review.text
    assert str(rotated["poll_bearer"]) not in review.text
    decision = client.post(
        "/admin/connection-requests/decision/approve-once",
        data=_form_values(review.text),
        headers={"Origin": "https://admin.test"},
        follow_redirects=False,
    )
    assert decision.status_code == 303

    poll = _signed(
        key,
        POLL_DOMAIN,
        {
            "request_id": request["request_id"],
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "client_nonce_b64url": _nonce(72),
        },
    )
    polled = client.post(
        f"/api/v1/social/admission-requests/{request['request_id']}/poll",
        json=poll,
        headers={"X-AutPlay-Admission-Poll": str(rotated["poll_bearer"])},
    )
    assert polled.status_code == 200 and polled.json()["state"] == "APPROVED"
    assert polled.json()["approved_account_id"] == str(owner_id)

    exchange = _signed(
        key,
        EXCHANGE_DOMAIN,
        {
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "poll_bearer_sha256": hashlib.sha256(
                str(rotated["poll_bearer"]).encode("ascii")
            ).hexdigest(),
            "expected_server_instance_id": request["expected_server_instance_id"],
            "expected_identity_epoch": request["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": request["expected_identity_thumbprint_sha256"],
            "expected_api_origin": "https://api.test.invalid",
            "expected_stream_origin": "https://stream.test.invalid",
            "approved_account_id": str(owner_id),
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "device_public_key_jwk": _jwk(key),
            "next_refresh_token_sha256": hashlib.sha256(b"http-refresh").hexdigest(),
            "client_nonce_b64url": _nonce(73),
        },
    )
    exchanged = client.post(
        f"/api/v1/social/admission-requests/{request['request_id']}/exchange",
        json=exchange,
        headers={"X-AutPlay-Admission-Poll": str(rotated["poll_bearer"])},
    )
    assert exchanged.status_code == 201
    assert exchanged.headers["cache-control"] == "no-store"
    assert exchanged.json()["binding_commit_id"] == exchange["binding_commit_id"]


def test_concurrent_duplicate_submit_and_exact_approval_replay_are_serialized(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    request, _, _ = _admission_request(admission_runtime, source="198.51.100.1", nonce_marker=1)
    submit_gate = Barrier(2)

    def submit() -> dict[str, object] | str:
        submit_gate.wait(timeout=5)
        try:
            created, _ = admission_runtime.service.submit_device_admission(
                request, source="198.51.100.1"
            )
            return created
        except ProfilePairingError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="s1b-submit") as executor:
        outcomes = [executor.submit(submit), executor.submit(submit)]
        results = [future.result(timeout=10) for future in outcomes]
    created_rows = [result for result in results if isinstance(result, dict)]
    assert len(created_rows) == 1
    assert results.count("admission_recovery_required") == 1

    created = created_rows[0]
    owner = _web_actor(admission_runtime, database_connection)
    admission_runtime.service.bind_device_admission_review(
        actor=owner,
        web_session_id=owner.web_session_id,
        locator=str(created["review_locator"]),
        operation_id=uuid4(),
        request_sha256=b"b" * 32,
    )
    operation_id = uuid4()
    decision_gate = Barrier(2)

    def decide() -> dict[str, object]:
        decision_gate.wait(timeout=5)
        return admission_runtime.service.decide_device_admission(
            owner,
            UUID(str(created["request_id"])),
            "APPROVE_ONCE",
            operation_id,
            b"a" * 32,
            web_session_id=owner.web_session_id,
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="s1b-decision") as executor:
        decision_futures = [executor.submit(decide), executor.submit(decide)]
        decisions = [future.result(timeout=10) for future in decision_futures]
    assert sum(result.get("replayed") is True for result in decisions) == 1
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission_web_operation_receipt WHERE operation_id=%s",
        (operation_id,),
    ).fetchone() == (1,)


def test_recovery_invalidates_web_binding_and_poll_is_nonce_and_time_bounded(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    request, created, key, request_hash = _admission(admission_runtime, source="10.0.0.1")
    actor = _web_actor(admission_runtime, database_connection)
    admission_runtime.service.bind_device_admission_review(
        actor=actor,
        web_session_id=actor.web_session_id,
        locator=str(created["review_locator"]),
        operation_id=uuid4(),
        request_sha256=b"r" * 32,
    )
    recovery = _signed(
        key,
        RECOVERY_DOMAIN,
        {
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "expected_server_instance_id": request["expected_server_instance_id"],
            "expected_identity_epoch": request["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": request["expected_identity_thumbprint_sha256"],
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "recovery_nonce_b64url": _nonce(2),
        },
    )
    AdmissionRecovery.model_validate(recovery)
    rotated = admission_runtime.service.recover_device_admission(recovery)
    assert rotated["review_locator"] != created["review_locator"]
    assert rotated["poll_bearer"] != created["poll_bearer"]
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        admission_runtime.service.reviewed_device_admission(
            actor=actor, web_session_id=actor.web_session_id
        )

    def poll(marker: int) -> dict[str, object]:
        body = _signed(
            key,
            POLL_DOMAIN,
            {
                "request_id": request["request_id"],
                "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
                "client_nonce_b64url": _nonce(marker),
            },
        )
        return admission_runtime.service.poll_device_admission(body, str(rotated["poll_bearer"]))

    assert poll(3)["state"] == "PENDING"
    with pytest.raises(ProfilePairingError, match="admission_poll_rate_limited"):
        poll(4)
    admission_runtime.clock.advance(timedelta(seconds=3))
    assert poll(4)["state"] == "PENDING"
    admission_runtime.clock.advance(timedelta(seconds=3))
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        poll(3)
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        admission_runtime.service.recover_device_admission(recovery)


def test_user_cannot_consume_locator_and_account_binding_limit_is_five(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    requests = [
        _admission(admission_runtime, source=f"10.0.1.{index}", nonce_marker=index)
        for index in range(1, 7)
    ]
    user = _web_actor(admission_runtime, database_connection, AccountRole.USER)
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        admission_runtime.service.bind_device_admission_review(
            actor=user,
            web_session_id=user.web_session_id,
            locator=str(requests[0][1]["review_locator"]),
            operation_id=uuid4(),
            request_sha256=b"u" * 32,
        )
    owner = _web_actor(admission_runtime, database_connection)
    for _, created, _, _ in requests[:5]:
        admission_runtime.service.bind_device_admission_review(
            actor=owner,
            web_session_id=owner.web_session_id,
            locator=str(created["review_locator"]),
            operation_id=uuid4(),
            request_sha256=b"o" * 32,
        )
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        admission_runtime.service.bind_device_admission_review(
            actor=owner,
            web_session_id=owner.web_session_id,
            locator=str(requests[5][1]["review_locator"]),
            operation_id=uuid4(),
            request_sha256=b"o" * 32,
        )
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission WHERE review_web_session_id=%s",
        (owner.web_session_id,),
    ).fetchone() == (5,)


def test_submit_rate_limits_are_exact_and_transactional(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    for index in range(10):
        _admission(admission_runtime, source="198.51.100.7", nonce_marker=index + 1)
    with pytest.raises(ProfilePairingError, match="admission_rate_limited"):
        _admission(admission_runtime, source="198.51.100.7", nonce_marker=20)

    key = ec.generate_private_key(ec.SECP256R1())
    for index in range(30):
        request, _, _, _ = _admission(
            admission_runtime,
            key=key,
            source=f"203.0.113.{index + 1}",
            nonce_marker=index + 30,
        )
        database_connection.execute(
            "UPDATE account.device_admission SET state='REJECTED', decided_at=%s "
            "WHERE request_id=%s",
            (admission_runtime.clock.value, UUID(str(request["request_id"]))),
        )
        database_connection.commit()
    with pytest.raises(ProfilePairingError, match="admission_rate_limited"):
        _admission(
            admission_runtime,
            key=key,
            source="203.0.113.200",
            nonce_marker=90,
        )


def test_exchange_replay_trust_removal_and_revocation_remain_distinct(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    request, created, key, request_hash = _admission(admission_runtime, source="192.0.2.10")
    owner = _web_actor(admission_runtime, database_connection)
    _bind_and_decide(admission_runtime, owner, created, "TRUST_DEVICE")
    refresh = b"r" * 32
    exchange = _signed(
        key,
        EXCHANGE_DOMAIN,
        {
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "poll_bearer_sha256": hashlib.sha256(
                str(created["poll_bearer"]).encode("ascii")
            ).hexdigest(),
            "expected_server_instance_id": request["expected_server_instance_id"],
            "expected_identity_epoch": request["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": request["expected_identity_thumbprint_sha256"],
            "expected_api_origin": "https://api.test.invalid",
            "expected_stream_origin": "https://stream.test.invalid",
            "approved_account_id": str(owner.user_id),
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "device_public_key_jwk": _jwk(key),
            "next_refresh_token_sha256": hashlib.sha256(refresh).hexdigest(),
            "client_nonce_b64url": _nonce(40),
        },
    )
    enrolled, replayed = admission_runtime.service.exchange_device_admission(
        exchange, str(created["poll_bearer"])
    )
    assert not replayed
    exact, replayed = admission_runtime.service.exchange_device_admission(
        exchange, str(created["poll_bearer"])
    )
    assert replayed and exact["session_id"] == enrolled["session_id"]
    changed = dict(exchange)
    changed["exchange_id"] = str(uuid4())
    changed["binding_commit_id"] = str(uuid4())
    changed["next_refresh_token_sha256"] = hashlib.sha256(b"changed-refresh").hexdigest()
    changed.pop("proof_b64url")
    changed = _signed(key, EXCHANGE_DOMAIN, changed)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        admission_runtime.service.exchange_device_admission(changed, str(created["poll_bearer"]))
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission_exchange_receipt "
        "WHERE request_or_challenge_id=%s",
        (UUID(str(request["request_id"])),),
    ).fetchone() == (1,)

    thumb = bytes.fromhex(str(request["device_key_thumbprint_sha256"]))
    admission_runtime.service.manage_trusted_key(
        principal=owner,
        thumbprint=thumb,
        action="REMOVE_TRUST",
        operation_id=uuid4(),
        request_sha256=b"m" * 32,
    )
    assert database_connection.execute(
        "SELECT revoked_at IS NULL FROM account.user_session WHERE session_id=%s",
        (UUID(str(enrolled["session_id"])),),
    ).fetchone() == (True,)
    admission_runtime.service.manage_trusted_key(
        principal=owner,
        thumbprint=thumb,
        action="REVOKE_ACCESS",
        operation_id=uuid4(),
        request_sha256=b"v" * 32,
    )
    assert database_connection.execute(
        "SELECT revoked_at IS NOT NULL FROM account.user_session WHERE session_id=%s",
        (UUID(str(enrolled["session_id"])),),
    ).fetchone() == (True,)


def test_cleanup_preserves_replay_and_trust_references_then_removes_orphans(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    request, created, key, request_hash = _admission(admission_runtime, source="192.0.2.20")
    owner = _web_actor(admission_runtime, database_connection)
    _bind_and_decide(admission_runtime, owner, created, "TRUST_DEVICE")
    exchange = _signed(
        key,
        EXCHANGE_DOMAIN,
        {
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "poll_bearer_sha256": hashlib.sha256(
                str(created["poll_bearer"]).encode("ascii")
            ).hexdigest(),
            "expected_server_instance_id": request["expected_server_instance_id"],
            "expected_identity_epoch": request["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": request["expected_identity_thumbprint_sha256"],
            "expected_api_origin": "https://api.test.invalid",
            "expected_stream_origin": "https://stream.test.invalid",
            "approved_account_id": str(owner.user_id),
            "device_key_thumbprint_sha256": request["device_key_thumbprint_sha256"],
            "device_public_key_jwk": _jwk(key),
            "next_refresh_token_sha256": hashlib.sha256(b"cleanup-refresh").hexdigest(),
            "client_nonce_b64url": _nonce(50),
        },
    )
    admission_runtime.service.exchange_device_admission(exchange, str(created["poll_bearer"]))
    database_connection.execute(
        "UPDATE account.device_admission SET decided_at=%s WHERE request_id=%s",
        (
            admission_runtime.clock.value - timedelta(days=2),
            UUID(str(request["request_id"])),
        ),
    )
    database_connection.commit()
    cleanup_expired_device_admissions(
        admission_runtime.sessions, now=admission_runtime.clock.value, limit=100
    )
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission WHERE request_id=%s",
        (UUID(str(request["request_id"])),),
    ).fetchone() == (1,)
    replay, replayed = admission_runtime.service.exchange_device_admission(
        exchange, str(created["poll_bearer"])
    )
    assert replayed and replay["exchange_id"] == exchange["exchange_id"]

    admission_runtime.clock.advance(timedelta(days=31))
    cleanup_expired_pairing_receipts(
        admission_runtime.sessions, now=admission_runtime.clock.value, limit=100
    )
    cleanup_expired_device_admissions(
        admission_runtime.sessions, now=admission_runtime.clock.value, limit=100
    )
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission WHERE request_id=%s",
        (UUID(str(request["request_id"])),),
    ).fetchone() == (0,)
    assert admission_runtime.service.list_trusted_device_keys(owner)["trusted_keys"]

    challenge_id = uuid4()
    database_connection.execute(
        """
        INSERT INTO account.trusted_device_reenrollment_challenge (
          challenge_id, user_id, device_key_thumbprint_sha256, request_sha256,
          client_nonce_sha256, challenge_hash,
          expires_at, consumed_at, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s)
        """,
        (
            challenge_id,
            owner.user_id,
            bytes.fromhex(str(request["device_key_thumbprint_sha256"])),
            b"r" * 32,
            b"n" * 32,
            b"h" * 32,
            admission_runtime.clock.value - timedelta(minutes=1),
            admission_runtime.clock.value - timedelta(minutes=10),
        ),
    )
    database_connection.commit()
    cleanup_expired_pairing_receipts(
        admission_runtime.sessions, now=admission_runtime.clock.value, limit=100
    )
    assert database_connection.execute(
        "SELECT count(*) FROM account.trusted_device_reenrollment_challenge WHERE challenge_id=%s",
        (challenge_id,),
    ).fetchone() == (0,)


def test_trusted_reenrollment_challenge_replay_and_growth_are_bounded(
    admission_runtime: _Runtime, database_connection: Connection[Any]
) -> None:
    owner = _web_actor(admission_runtime, database_connection)
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    thumbprint = public_key_thumbprint(spki)
    database_connection.execute(
        """
        INSERT INTO account.trusted_device_key (
          user_id, device_key_thumbprint_sha256, device_public_key_spki,
          approved_request_id, key_reference, revision, created_at
        ) VALUES (%s, %s, %s, %s, %s, 1, %s)
        """,
        (
            owner.user_id,
            thumbprint,
            spki,
            uuid4(),
            uuid4(),
            admission_runtime.clock.value,
        ),
    )
    database_connection.commit()
    identity = _identity(admission_runtime.service)

    def challenge_request(marker: int) -> dict[str, object]:
        return _signed(
            key,
            TRUSTED_REENROLLMENT_DOMAIN,
            {
                "challenge_request_id": str(uuid4()),
                "account_id": str(owner.user_id),
                "expected_server_instance_id": identity["server_instance_id"],
                "expected_identity_epoch": identity["identity_epoch"],
                "expected_identity_thumbprint_sha256": identity["identity_thumbprint_sha256"],
                "device_key_thumbprint_sha256": thumbprint.hex(),
                "device_public_key_jwk": _jwk(key),
                "client_nonce_b64url": _nonce(marker),
            },
        )

    request = challenge_request(80)
    barrier = Barrier(2)

    def race() -> tuple[str, str]:
        barrier.wait(timeout=5)
        try:
            result = admission_runtime.service.request_trusted_reenrollment_challenge(request)
            return "created", str(result["challenge_id"])
        except ProfilePairingError as error:
            return "failed", error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(race), pool.submit(race)]]
    assert sorted(result[0] for result in results) == ["created", "failed"]
    assert ("failed", "trusted_key_unavailable") in results
    assert database_connection.execute(
        "SELECT count(*) FROM account.trusted_device_reenrollment_challenge WHERE challenge_id=%s",
        (UUID(str(request["challenge_request_id"])),),
    ).fetchone() == (1,)

    admission_runtime.service.request_trusted_reenrollment_challenge(challenge_request(81))
    admission_runtime.service.request_trusted_reenrollment_challenge(challenge_request(82))
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        admission_runtime.service.request_trusted_reenrollment_challenge(challenge_request(83))

    # Consumed challenges do not occupy the active-three bound, but still count toward the
    # five-per-key/account durable 15-minute rate window.
    database_connection.execute(
        "UPDATE account.trusted_device_reenrollment_challenge SET consumed_at=%s WHERE user_id=%s",
        (admission_runtime.clock.value, owner.user_id),
    )
    database_connection.commit()
    for marker in range(83, 85):
        issued = admission_runtime.service.request_trusted_reenrollment_challenge(
            challenge_request(marker)
        )
        database_connection.execute(
            "UPDATE account.trusted_device_reenrollment_challenge SET consumed_at=%s "
            "WHERE challenge_id=%s",
            (admission_runtime.clock.value, UUID(str(issued["challenge_id"]))),
        )
        database_connection.commit()
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        admission_runtime.service.request_trusted_reenrollment_challenge(challenge_request(85))

"""UNEXECUTED real PostgreSQL + HTTP + M6 gates for the six D1 operations.

Synthetic secrets/keys only. Proof/digest statements below are future test code;
no cryptography, imports, database, HTTP or other checks ran during authoring.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.application.profile_pairing import cleanup_expired_device_admissions
from autplay.application.web_admin import WebAdminService
from autplay.domain.canonical_json import JsonValue
from autplay.domain.device_compatibility import (
    IMPLEMENTED_AND_QUALIFIED_FAMILIES,
    DeviceCompatibilityError,
)
from autplay.domain.device_profile_v2 import DeviceProfileV2Error
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.web_admin import WebActor
from autplay.entrypoints.admin_web_http import create_admin_web_router
from autplay.entrypoints.device_admission_v2_http import create_device_admission_v2_router
from autplay.entrypoints.device_admission_web import DeviceAdmissionWebAdapter
from autplay.runtime.http import install_error_handlers
from autplay.web.renderer import AdminTemplateRenderer
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from typed_assertions import present, require_instance

from .test_device_admission_s1b import (
    _admission,
    _bind_and_decide,
    _form_values,
    _identity,
    _jwk,
    _nonce,
    _Runtime,
    _signed,
    _web_actor,
)
from .test_device_admission_s1b import admission_runtime as admission_runtime
from .test_device_profile_v2 import (
    ProfileHarness,
    _legacy_admission_binding,
    _legacy_pair,
    _legacy_reservation_writer,
)
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service

PREFIX = "/api/v1/device-compatibility/v2"
FAILURES = (ProfilePairingError, DeviceCompatibilityError, DeviceProfileV2Error)


def proof(body: dict[str, Any], key: ec.EllipticCurvePrivateKey, operation: str) -> bytes:
    # request_sha256 here is the ORIGINAL submit reference. It is never omitted.
    unsigned = {name: value for name, value in body.items() if name != "proof_b64url"}
    return rfc8785.dumps(
        cast(
            JsonValue,
            _signed(
                key,
                "autplay:device-compatibility:" + operation.replace(".", ":") + ":v2\n",
                unsigned,
            ),
        )
    )


def trust(body: dict[str, Any]) -> dict[str, Any]:
    return {
        name: body[name]
        for name in (
            "contract_version",
            "schema_version",
            "platform",
            "expected_server_instance_id",
            "expected_identity_epoch",
            "expected_identity_thumbprint_sha256",
            "expected_api_origin",
            "expected_stream_origin",
        )
    }


@dataclass
class Admission:
    body: dict[str, Any]
    key: ec.EllipticCurvePrivateKey
    raw: bytes
    created: dict[str, Any]

    @property
    def reference(self) -> str:
        return canonical_sha256(self.body).hex()


@dataclass
class AdmissionHarness:
    runtime: _Runtime
    service: DeviceAdmissionServiceV2
    gate: dict[str, bool]
    platform: str
    actor: WebActor

    def submit_body(
        self, key: ec.EllipticCurvePrivateKey | None = None
    ) -> tuple[dict[str, Any], ec.EllipticCurvePrivateKey]:
        key = key or ec.generate_private_key(ec.SECP256R1())
        identity = _identity(self.runtime.service)
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": self.platform,
            "request_id": str(uuid4()),
            "requested_at": self.runtime.clock.value.isoformat().replace("+00:00", "Z"),
            "client_nonce_b64url": secrets.token_urlsafe(32),
            "api_major": 1,
            "nickname": "D1 synthetic device",
            "device_model_hint": "D1 test model",
            "app_version": "d1-fixture",
            "device_public_key_jwk": _jwk(key),
            "device_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
            "expected_server_instance_id": identity["server_instance_id"],
            "expected_identity_epoch": identity["identity_epoch"],
            "expected_identity_thumbprint_sha256": identity["identity_thumbprint_sha256"],
            "expected_api_origin": "https://api.test.invalid",
            "expected_stream_origin": "https://stream.test.invalid",
        }, key

    def submit(self, *, source: str = "d1-synthetic") -> Admission:
        body, key = self.submit_body()
        raw = proof(body, key, "admission.submit")
        return Admission(body, key, raw, self.service.admission_submit(raw, source=source))

    def approve(
        self,
        admission: Admission,
        action: str = "TRUST_DEVICE",
        *,
        operation_id: UUID | None = None,
    ) -> None:
        self.runtime.service.decide_device_admission(
            self.actor,
            UUID(admission.body["request_id"]),
            action,
            operation_id or uuid4(),
            b"w" * 32,
            web_session_id=self.actor.web_session_id,
            from_pending_list=True,
            device_name="D1 reviewed device"
            if action in {"TRUST_DEVICE", "APPROVE_ONCE"}
            else None,
        )

    def poll(self, admission: Admission) -> dict[str, Any]:
        return {
            **trust(admission.body),
            "request_id": admission.body["request_id"],
            "device_key_thumbprint_sha256": admission.body["device_key_thumbprint_sha256"],
            "client_nonce_b64url": secrets.token_urlsafe(32),
        }

    def recover(self, admission: Admission) -> dict[str, Any]:
        return {
            **trust(admission.body),
            "request_id": admission.body["request_id"],
            "request_sha256": admission.reference,
            "device_key_thumbprint_sha256": admission.body["device_key_thumbprint_sha256"],
            "recovery_nonce_b64url": secrets.token_urlsafe(32),
        }

    def exchange(self, admission: Admission) -> dict[str, Any]:
        return {
            **trust(admission.body),
            "request_id": admission.body["request_id"],
            "request_sha256": admission.reference,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "approved_account_id": str(self.actor.user_id),
            "device_public_key_jwk": admission.body["device_public_key_jwk"],
            "device_key_thumbprint_sha256": admission.body["device_key_thumbprint_sha256"],
            "poll_bearer_sha256": hashlib.sha256(
                admission.created["poll_bearer"].encode("ascii")
            ).hexdigest(),
            "client_nonce_b64url": secrets.token_urlsafe(32),
            "next_refresh_token_sha256": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        }

    def bind(self, admission: Admission) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
        self.approve(admission)
        body = self.exchange(admission)
        raw = proof(body, admission.key, "admission.exchange")
        result, replayed = self.service.admission_exchange(
            raw, admission.body["request_id"], admission.created["poll_bearer"]
        )
        assert replayed is False
        return body, raw, result

    def challenge_body(self, admission: Admission) -> dict[str, Any]:
        return {
            **trust(admission.body),
            "challenge_request_id": str(uuid4()),
            "account_id": str(self.actor.user_id),
            "device_public_key_jwk": admission.body["device_public_key_jwk"],
            "device_key_thumbprint_sha256": admission.body["device_key_thumbprint_sha256"],
            "client_nonce_b64url": secrets.token_urlsafe(32),
        }

    def trusted_body(self, admission: Admission, challenge: dict[str, Any]) -> dict[str, Any]:
        return {
            **trust(admission.body),
            "challenge_id": challenge["challenge_id"],
            "challenge": challenge["challenge"],
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "account_id": str(self.actor.user_id),
            "device_public_key_jwk": admission.body["device_public_key_jwk"],
            "device_key_thumbprint_sha256": admission.body["device_key_thumbprint_sha256"],
            "client_nonce_b64url": secrets.token_urlsafe(32),
            "next_refresh_token_sha256": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        }


@pytest.fixture(params=("ANDROID", "MACOS"))
def admission_v2(
    admission_runtime: _Runtime,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> AdmissionHarness:
    gate = {"enabled": True}
    monkeypatch.setattr(
        "autplay.application.device_admission_service_v2._now",
        lambda: admission_runtime.clock.value,
    )
    admission_runtime.service._device_v2_enabled = lambda: gate["enabled"]
    actor = _web_actor(admission_runtime, database_connection)
    return AdmissionHarness(
        admission_runtime,
        DeviceAdmissionServiceV2(
            admission_runtime.sessions,
            identity=admission_runtime.service,
            enabled=lambda: gate["enabled"],
        ),
        gate,
        request.param,
        actor,
    )


def client_for(h: AdmissionHarness) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_device_admission_v2_router(h.service), prefix="/api/v1")
    return TestClient(app, base_url="https://admin.test")


def post(client: TestClient, path: str, raw: bytes, bearer: str | None = None) -> Response:
    return client.post(
        PREFIX + path,
        content=raw,
        headers={
            "Content-Type": "application/json",
            **({"X-AutPlay-Admission-Poll": bearer} if bearer else {}),
        },
    )


def snapshot(connection: Connection[Any]) -> object:
    tables = (
        "device_admission",
        "device_admission_nonce",
        "device_admission_rate_window",
        "device_admission_exchange_receipt",
        "device_admission_web_operation_receipt",
        "trusted_device_key",
        "trusted_device_reenrollment_challenge",
        "device",
        "user_session",
    )
    result = tuple(
        connection.execute(f"SELECT * FROM account.{table} ORDER BY 1").fetchall()
        for table in tables
    )
    connection.commit()
    return result


@pytest.mark.parametrize("pending_flow", [False, True])
def test_actual_http_m6_review_sas_approval_and_both_issuers(
    admission_v2: AdmissionHarness, database_connection: Connection[Any], pending_flow: bool
) -> None:
    h = admission_v2
    client = client_for(h)
    web = WebAdminService(
        SqlAlchemyWebAdminUnitOfWorkFactory(h.runtime.sessions),
        csrf_secret=b"d1-web-csrf-synthetic-32-byte-secret",
    )
    now = datetime.now(UTC)
    invite = web.issue_invitation(h.actor.user_id, now=now)
    login = web.login(web.begin_login(now=now), invite.bearer, b"l" * 32, now=now)
    require_instance(client.app, FastAPI).include_router(
        create_admin_web_router(
            web=web,
            views=cast(Any, object()),
            commands=cast(Any, object()),
            renderer=AdminTemplateRenderer(),
            origin="https://admin.test",
            source_secret=b"d1-web-source-synthetic-32-byte-secret",
            device_admission=DeviceAdmissionWebAdapter(h.runtime.service),
        )
    )
    body, key = h.submit_body()
    raw = proof(body, key, "admission.submit")
    response = post(client, "/admission/requests", raw)
    assert response.status_code == 202
    a = Admission(body, key, raw, response.json())
    assert (
        post(client, "/admission/requests", raw).json()["error"]["code"]
        == "admission_recovery_required"
    )
    recovered = post(
        client,
        f"/admission/requests/{body['request_id']}/recover",
        proof(h.recover(a), key, "admission.recover"),
    )
    assert (
        recovered.status_code == 200 and recovered.json()["expires_at"] == a.created["expires_at"]
    )
    a.created = recovered.json()
    client.cookies.set("__Host-autplay_admin", login.bearer.decode("ascii"))
    listing = client.get("/admin/connection-requests")
    assert listing.status_code == 200 and h.platform in listing.text and "v2" in listing.text
    csrf = _form_values(listing.text)["csrf_token"]
    if pending_flow:
        url = f"/admin/connection-requests/pending/{body['request_id']}/trust"
        form = {
            "csrf_token": csrf,
            "operation_id": str(uuid4()),
            "device_name": "D1 reviewed device",
        }
    else:
        resolve = client.post(
            "/admin/connection-requests/resolve",
            data={
                "csrf_token": csrf,
                "operation_id": str(uuid4()),
                "review_locator": a.created["review_locator"],
            },
            headers={"Origin": "https://admin.test"},
            follow_redirects=False,
        )
        assert resolve.status_code == 303
        review = client.get("/admin/connection-requests/review")
        assert (
            review.status_code == 200 and a.reference in review.text and h.platform in review.text
        )
        with h.runtime.sessions() as s:
            from autplay.adapters.postgresql.models.profile_pairing import DeviceAdmissionRow
            from autplay.application.profile_pairing import _admission_sas

            sas = _admission_sas(present(s.get(DeviceAdmissionRow, UUID(body["request_id"]))))
        assert len(sas) == 12 and all(
            group in review.text for group in (sas[:4], sas[4:8], sas[8:])
        )
        url = "/admin/connection-requests/decision/trust"
        form = {"csrf_token": csrf, "operation_id": str(uuid4()), "request_id": body["request_id"]}
    assert (
        client.post(
            url, data=form, headers={"Origin": "https://foreign.test"}, follow_redirects=False
        ).status_code
        == 403
    )
    assert (
        client.post(
            url, data=form, headers={"Origin": "https://admin.test"}, follow_redirects=False
        ).status_code
        == 303
    )
    poll = post(
        client,
        f"/admission/requests/{body['request_id']}/poll",
        proof(h.poll(a), key, "admission.poll"),
        a.created["poll_bearer"],
    )
    assert poll.json()["approved_account_id"] == str(h.actor.user_id)
    exchange = h.exchange(a)
    exchange_raw = proof(exchange, key, "admission.exchange")
    bound = post(
        client,
        f"/admission/requests/{body['request_id']}/exchange",
        exchange_raw,
        a.created["poll_bearer"],
    )
    assert bound.status_code == 201 and bound.json()["platform"] == h.platform
    challenge_raw = proof(h.challenge_body(a), key, "trusted.challenge")
    challenge = post(client, "/trusted/challenge", challenge_raw)
    assert challenge.status_code == 201 and len(challenge.json()["challenge"]) == 22
    assert (
        post(client, "/trusted/challenge", challenge_raw).json()["error"]["code"]
        == "trusted_key_unavailable"
    )
    trusted = h.trusted_body(a, challenge.json())
    trusted_raw = proof(trusted, key, "trusted.exchange")
    bound_trusted = post(client, "/trusted/exchanges", trusted_raw)
    assert bound_trusted.status_code == 201 and bound_trusted.json()["platform"] == h.platform
    h.gate["enabled"] = False
    for path, original, bearer, binding in (
        (
            f"/admission/requests/{body['request_id']}/exchange",
            exchange_raw,
            a.created["poll_bearer"],
            bound.json(),
        ),
        ("/trusted/exchanges", trusted_raw, None, bound_trusted.json()),
    ):
        replay = post(client, path, original, bearer)
        assert replay.status_code == 200 and replay.json()["device_id"] == binding["device_id"]
        assert (
            replay.headers["Cache-Control"] == "no-store" and replay.headers["Pragma"] == "no-cache"
        )
    assert not IMPLEMENTED_AND_QUALIFIED_FAMILIES
    stored = database_connection.execute(
        "SELECT original_request_evidence FROM account.device_admission_exchange_receipt"
    ).fetchall()
    text = repr(stored)
    assert (
        trusted["challenge"] not in text
        and a.created["poll_bearer"] not in text
        and a.created["review_locator"] not in text
    )
    assert (
        bound.json()["access_token"] not in text
        and bound_trusted.json()["access_token"] not in text
    )
    assert all(row[0]["signature_hex"] and row[0]["original_document_sha256"] for row in stored)


@pytest.mark.parametrize(
    "operation",
    [
        "admission.submit",
        "admission.poll",
        "admission.recover",
        "admission.exchange",
        "trusted.challenge",
        "trusted.exchange",
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_server_instance_id", str(UUID(int=71))),
        ("expected_identity_epoch", 77),
        ("expected_identity_thumbprint_sha256", "ab" * 32),
        ("expected_api_origin", "https://wrong.test"),
        ("expected_stream_origin", "https://wrong-stream.test"),
        ("contract_version", "v1"),
        ("schema_version", 1),
        ("platform", "OTHER"),
    ],
)
def test_each_operation_rejects_resigned_trust_version_platform_tamper(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    operation: str,
    field: str,
    value: Any,
) -> None:
    h = admission_v2
    a = h.submit()
    if operation in {"admission.exchange", "trusted.challenge", "trusted.exchange"}:
        h.approve(a)
    if operation.startswith("trusted"):
        h.service.admission_exchange(
            proof(h.exchange(a), a.key, "admission.exchange"),
            a.body["request_id"],
            a.created["poll_bearer"],
        )
    invoke: Callable[[bytes], object]
    if operation == "trusted.exchange":
        challenge = h.service.trusted_challenge(
            proof(h.challenge_body(a), a.key, "trusted.challenge")
        )
        body, invoke = h.trusted_body(a, challenge), h.service.trusted_exchange
    elif operation == "trusted.challenge":
        body, invoke = h.challenge_body(a), h.service.trusted_challenge
    elif operation == "admission.exchange":
        body, invoke = (
            h.exchange(a),
            lambda raw: h.service.admission_exchange(
                raw, a.body["request_id"], a.created["poll_bearer"]
            ),
        )
    elif operation == "admission.recover":
        body, invoke = (
            h.recover(a),
            lambda raw: h.service.admission_recover(raw, a.body["request_id"]),
        )
    elif operation == "admission.poll":
        body, invoke = (
            h.poll(a),
            lambda raw: h.service.admission_poll(
                raw, a.body["request_id"], a.created["poll_bearer"]
            ),
        )
    else:
        body, key = h.submit_body()
        a.key = key
        invoke = h.service.admission_submit
    body[field] = value
    before = snapshot(database_connection)
    with pytest.raises(FAILURES):
        invoke(proof(body, a.key, operation))
    assert snapshot(database_connection) == before


def reservation_candidate(
    profile: ProfileHarness,
    connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    identifier: str | None = None,
) -> tuple[str, Callable[[], dict[str, Any]]]:
    """Keep the v2 account independent of every v1 lifecycle writer under test."""
    from .test_device_admission_s1b import _Clock

    gate = {"enabled": True}
    profile.identity._device_v2_enabled = lambda: gate["enabled"]
    runtime = _Runtime(profile.identity, profile.identity._sessions, _Clock(profile.clock["now"]))
    monkeypatch.setattr(
        "autplay.application.device_admission_service_v2._now", lambda: profile.clock["now"]
    )
    h = AdmissionHarness(
        runtime,
        DeviceAdmissionServiceV2(
            runtime.sessions, identity=profile.identity, enabled=lambda: gate["enabled"]
        ),
        gate,
        profile.platform,
        _web_actor(runtime, connection),
    )
    identifier = identifier or str(uuid4())
    body, key = h.submit_body()
    if kind == "admission_submit":
        body["request_id"] = identifier
    original_submit = proof(body, key, "admission.submit")
    a = Admission(body, key, original_submit, {})
    if kind != "admission_submit":
        a.created = h.service.admission_submit(original_submit)
    if kind not in {"admission_submit", "admission_web"}:
        h.approve(a)
    exchange = h.exchange(a) if a.created else None
    challenge_request = None
    if kind.startswith("trusted"):
        assert exchange is not None
        h.service.admission_exchange(
            proof(exchange, key, "admission.exchange"), body["request_id"], a.created["poll_bearer"]
        )
        challenge_request = h.challenge_body(a)
        if kind == "trusted_challenge":
            challenge_request["challenge_request_id"] = identifier
            exchange = None
        else:
            challenge = h.service.trusted_challenge(
                proof(challenge_request, key, "trusted.challenge")
            )
            exchange = h.trusted_body(a, challenge)
    if kind.endswith("operation"):
        assert exchange is not None
        exchange["exchange_id"] = identifier
    if kind.endswith("binding"):
        assert exchange is not None
        exchange["binding_commit_id"] = identifier
    originals: list[bytes] = []
    operation = "trusted.exchange" if kind.startswith("trusted") else "admission.exchange"
    if exchange is not None and kind != "admission_web":
        originals.append(proof(exchange, key, operation))

    def attempt() -> dict[str, Any]:
        if not originals:
            if kind == "trusted_challenge":
                assert challenge_request is not None
                challenge = h.service.trusted_challenge(
                    proof(challenge_request, key, "trusted.challenge")
                )
                command = h.trusted_body(a, challenge)
            else:
                if kind == "admission_submit":
                    a.created = h.service.admission_submit(original_submit)
                h.approve(a, operation_id=UUID(identifier) if kind == "admission_web" else None)
                command = h.exchange(a)
            originals.append(proof(command, key, operation))
        if kind.startswith("trusted"):
            return h.service.trusted_exchange(originals[0])[0]
        return h.service.admission_exchange(
            originals[0], body["request_id"], a.created["poll_bearer"]
        )[0]

    return identifier, attempt


@pytest.mark.parametrize(
    "kind",
    [
        "admission_submit",
        "admission_web",
        "admission_operation",
        "admission_binding",
        "trusted_challenge",
        "trusted_operation",
        "trusted_binding",
    ],
)
@pytest.mark.parametrize(
    "writer",
    [
        "self_ceremony",
        "self_decision_APPROVE",
        "self_decision_REJECT",
        "self_decision_CANCEL",
        "configure",
        "lifecycle_cancel",
        "lifecycle_logout_current",
        "lifecycle_logout_all",
        "lifecycle_revoke",
        "web_review",
        "web_decision",
        "web_trust",
        "web_developer",
        "deletion_request",
        "deletion_cancel_operation",
        "deletion_cancel_binding",
    ],
)
@pytest.mark.parametrize("first", ["v1", "v2"])
def test_all_legacy_nonissuers_and_new_reservations_in_both_orders(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    writer: str,
    first: str,
) -> None:
    from autplay.domain.account_deletion import AccountDeletionError
    from autplay.domain.account_recovery import AccountRecoveryError
    from autplay.domain.self_device_pairing import SelfPairingError

    _legacy_pair(profile, database_connection)
    if first == "v2":
        identifier, v2 = reservation_candidate(profile, database_connection, monkeypatch, kind)
        winner = v2()
        _, legacy, observe = _legacy_reservation_writer(
            profile, database_connection, writer, tmp_path, identifier
        )
        before, ledger_before = snapshot(database_connection), observe()
        with pytest.raises(
            (ProfilePairingError, SelfPairingError, AccountRecoveryError, AccountDeletionError),
            match="operation_conflict",
        ):
            legacy()
        assert snapshot(database_connection) == before and observe() == ledger_before
        assert v2()["session_id"] == winner["session_id"]
    else:
        identifier, legacy, observe = _legacy_reservation_writer(
            profile, database_connection, writer, tmp_path
        )
        winner = legacy()
        _, v2 = reservation_candidate(profile, database_connection, monkeypatch, kind, identifier)
        before, ledger_before = snapshot(database_connection), observe()
        with pytest.raises(FAILURES, match="operation_conflict"):
            v2()
        assert snapshot(database_connection) == before and observe() == ledger_before
        replay = legacy()
        assert replay.get("operation_id", identifier) == winner.get("operation_id", identifier)
    assert snapshot(database_connection) == before and observe() == ledger_before


@pytest.mark.parametrize("issuer", ["admission", "trusted"])
@pytest.mark.parametrize(
    "kind", ["admission_operation", "admission_binding", "trusted_operation", "trusted_binding"]
)
@pytest.mark.parametrize("incoming_field", ["exchange_id", "binding_commit_id"])
@pytest.mark.parametrize("first", ["v1", "v2"])
def test_legacy_issuers_and_new_receipts_reserve_operations_and_bindings(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    issuer: str,
    kind: str,
    incoming_field: str,
    first: str,
) -> None:
    legacy, key, old_action, domain = _legacy_admission_binding(
        profile, database_connection, issuer
    )
    if first == "v2":
        identifier, new_action = reservation_candidate(
            profile, database_connection, monkeypatch, kind
        )
        winner = new_action()
        old_unsigned = {name: value for name, value in legacy.items() if name != "proof_b64url"}
        legacy = _signed(key, domain, {**old_unsigned, incoming_field: identifier})
        before = snapshot(database_connection)
        with pytest.raises(
            ProfilePairingError,
            match="admission_request_unavailable"
            if issuer == "admission"
            else "trusted_key_unavailable",
        ):
            old_action(legacy)
        assert new_action()["session_id"] == winner["session_id"]
    else:
        winner, _ = old_action(legacy)
        _, new_action = reservation_candidate(
            profile, database_connection, monkeypatch, kind, str(legacy[incoming_field])
        )
        before = snapshot(database_connection)
        with pytest.raises(FAILURES, match="operation_conflict"):
            new_action()
        replay, replayed = old_action(legacy)
        assert replayed and replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


def test_original_submit_reference_poll_header_path_nonce_and_fixed_domains(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h, a = admission_v2, admission_v2.submit()
    recover = h.recover(a)
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError):
        h.service.admission_recover(
            proof({**recover, "request_sha256": "aa" * 32}, a.key, "admission.recover"),
            a.body["request_id"],
        )
    for method, raw, args in (
        (
            h.service.admission_poll,
            proof(h.poll(a), a.key, "trusted.challenge"),
            (a.body["request_id"], a.created["poll_bearer"]),
        ),
        (h.service.admission_recover, proof(recover, a.key, "admission.recover"), (str(uuid4()),)),
    ):
        with pytest.raises(FAILURES):
            method(raw, *args)
    assert snapshot(database_connection) == before
    rotated = h.service.admission_recover(
        proof(recover, a.key, "admission.recover"), a.body["request_id"]
    )
    assert (
        rotated["expires_at"] == a.created["expires_at"]
        and rotated["poll_bearer"] != a.created["poll_bearer"]
    )
    with pytest.raises(ProfilePairingError):
        h.service.admission_poll(
            proof(h.poll(a), a.key, "admission.poll"),
            a.body["request_id"],
            a.created["poll_bearer"],
        )
    a.created = rotated
    poll = proof(h.poll(a), a.key, "admission.poll")
    h.service.admission_poll(
        poll, a.body["request_id"], require_instance(a.created["poll_bearer"], str)
    )
    with pytest.raises(ProfilePairingError, match="admission_poll_rate_limited"):
        h.service.admission_poll(
            proof(h.poll(a), a.key, "admission.poll"),
            a.body["request_id"],
            require_instance(a.created["poll_bearer"], str),
        )
    h.runtime.clock.advance(timedelta(seconds=2))
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        h.service.admission_poll(
            poll, a.body["request_id"], require_instance(a.created["poll_bearer"], str)
        )
    h.approve(a)
    exchange = h.exchange(a)
    before = snapshot(database_connection)
    for changed in (
        {**exchange, "request_sha256": "bb" * 32},
        {**exchange, "poll_bearer_sha256": "cc" * 32},
        {**exchange, "approved_account_id": str(uuid4())},
    ):
        with pytest.raises(FAILURES):
            h.service.admission_exchange(
                proof(changed, a.key, "admission.exchange"),
                a.body["request_id"],
                require_instance(a.created["poll_bearer"], str),
            )
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize(
    "terminal",
    ["logout", "device", "account", "authority", "role", "block", "trust", "identity", "expiry"],
)
def test_current_authority_defeats_exact_committed_flag_off_replay(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    trusted: bool,
    terminal: str,
) -> None:
    h, a = admission_v2, admission_v2.submit()
    _, raw, result = h.bind(a)

    def exchange_original(original: bytes) -> tuple[dict[str, object], bool]:
        return h.service.admission_exchange(
            original, a.body["request_id"], a.created["poll_bearer"]
        )

    action: Callable[[bytes], tuple[dict[str, object], bool]] = exchange_original
    if trusted:
        challenge = h.service.trusted_challenge(
            proof(h.challenge_body(a), a.key, "trusted.challenge")
        )
        raw = proof(h.trusted_body(a, challenge), a.key, "trusted.exchange")
        result, _ = h.service.trusted_exchange(raw)
        action = h.service.trusted_exchange
    if terminal == "logout":
        database_connection.execute(
            "UPDATE account.user_session SET revoked_at=%s WHERE session_id=%s",
            (h.runtime.clock.value, UUID(result["session_id"])),
        )
    elif terminal == "device":
        database_connection.execute(
            "UPDATE account.device SET revoked_at=%s WHERE device_id=%s",
            (h.runtime.clock.value, UUID(result["device_id"])),
        )
    elif terminal in {"account", "authority", "role"}:
        assignment = {
            "account": "status='DISABLED'",
            "authority": "authority_generation=authority_generation+1",
            "role": "role='USER'",
        }[terminal]
        # Preserve remaining-owner rules with a separate active owner.
        database_connection.execute(
            "INSERT INTO account.user_account(display_name,role) VALUES('D1 other owner','OWNER')"
        )
        database_connection.execute(
            f"UPDATE account.user_account SET {assignment} WHERE user_id=%s", (h.actor.user_id,)
        )
    elif terminal == "block":
        database_connection.execute(
            (
                "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) "
                "VALUES(%s,%s)"
            ),
            (h.actor.user_id, bytes.fromhex(a.body["device_key_thumbprint_sha256"])),
        )
    elif terminal == "trust":
        database_connection.execute(
            (
                "UPDATE account.trusted_device_key SET removed_at=%s,revision=revision+1 "
                "WHERE user_id=%s"
            ),
            (h.runtime.clock.value, h.actor.user_id),
        )
    elif terminal == "identity":
        database_connection.execute(
            "UPDATE account.server_instance SET identity_epoch=identity_epoch+1"
        )
    else:
        h.runtime.clock.advance(timedelta(days=30))
    database_connection.commit()
    h.gate["enabled"] = False
    before = snapshot(database_connection)
    with pytest.raises(FAILURES):
        action(raw)
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("platform", [None, "WEB", "OTHER", "opposite"])
@pytest.mark.parametrize("thumb", ["correct", "missing", "conflicting"])
def test_trusted_never_infers_platform_from_key_or_unknown_history(
    admission_v2: AdmissionHarness,
    database_connection: Connection[Any],
    platform: str | None,
    thumb: str,
) -> None:
    h = admission_v2
    body, key = h.submit_body()
    a = Admission(body, key, b"", {})
    spki, digest = public_spki(key), public_key_thumbprint(public_spki(key))
    database_connection.execute(
        (
            "INSERT INTO account.trusted_device_key(user_id,device_key_thumbprint_sha256,"
            "device_public_key_spki,approved_request_id,key_reference,revision) "
            "VALUES(%s,%s,%s,%s,%s,1)"
        ),
        (h.actor.user_id, digest, spki, uuid4(), uuid4()),
    )
    if platform is not None:
        actual = (
            ("ANDROID" if h.platform == "MACOS" else "MACOS")
            if platform == "opposite"
            else platform
        )
        # Different account, revoked history, NULL/conflicting thumbprint still
        # counts via exact SPKI; no active-state filter can hide the conflict.
        owner = present(
            database_connection.execute(
                "INSERT INTO account.user_account(display_name) VALUES('D1 history') "
                "RETURNING user_id"
            ).fetchone()
        )[0]
        database_connection.execute(
            (
                "INSERT INTO account.device(user_id,device_name,platform,app_version,public_k"
                "ey,public_key_thumbprint_sha256,revoked_at) VALUES(%s,'D1 "
                "history',%s,'fixture',%s,%s,%s)"
            ),
            (
                owner,
                actual,
                spki,
                digest if thumb == "correct" else None if thumb == "missing" else b"x" * 32,
                h.runtime.clock.value,
            ),
        )
    database_connection.commit()
    before = snapshot(database_connection)
    with pytest.raises(FAILURES):
        h.service.trusted_challenge(proof(h.challenge_body(a), key, "trusted.challenge"))
    assert snapshot(database_connection) == before


def test_gate_off_first_effects_recovery_and_terminal_web_actions(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h, pending = admission_v2, admission_v2.submit()
    h.gate["enabled"] = False
    fresh, key = h.submit_body()
    before = snapshot(database_connection)
    for effect in (
        lambda: h.service.admission_submit(proof(fresh, key, "admission.submit")),
        lambda: h.service.admission_recover(
            proof(h.recover(pending), pending.key, "admission.recover"), pending.body["request_id"]
        ),
        lambda: h.approve(pending),
    ):
        with pytest.raises(FAILURES):
            effect()
        assert snapshot(database_connection) == before
    h.approve(pending, "REJECT")
    status = h.service.admission_poll(
        proof(h.poll(pending), pending.key, "admission.poll"),
        pending.body["request_id"],
        pending.created["poll_bearer"],
    )
    assert status["state"] == "REJECTED"
    h.runtime.clock.advance(timedelta(days=1))
    with pytest.raises(FAILURES):
        h.service.admission_poll(
            proof(h.poll(pending), pending.key, "admission.poll"),
            pending.body["request_id"],
            pending.created["poll_bearer"],
        )
    assert cleanup_expired_device_admissions(h.runtime.sessions, now=h.runtime.clock.value) >= 1


@pytest.mark.parametrize("trusted", [False, True])
def test_parallel_exact_exchange_commits_once_and_changed_original_never_replays(
    admission_v2: AdmissionHarness, database_connection: Connection[Any], trusted: bool
) -> None:
    h, a = admission_v2, admission_v2.submit()
    h.approve(a)
    body, operation = h.exchange(a), "admission.exchange"

    def action(raw: bytes) -> tuple[dict[str, object], bool]:
        return h.service.admission_exchange(raw, a.body["request_id"], a.created["poll_bearer"])

    if trusted:
        action(proof(body, a.key, operation))
        challenge = h.service.trusted_challenge(
            proof(h.challenge_body(a), a.key, "trusted.challenge")
        )
        body, operation, action = (
            h.trusted_body(a, challenge),
            "trusted.exchange",
            h.service.trusted_exchange,
        )
    original, barrier = proof(body, a.key, operation), Barrier(2)

    def attempt() -> tuple[dict[str, object], bool]:
        barrier.wait(timeout=10)
        return action(original)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(replayed for _, replayed in results) == [False, True]
    assert len({result["session_id"] for result, _ in results}) == 1
    before = snapshot(database_connection)
    for changed in (
        body,
        {**body, "binding_commit_id": str(uuid4())},
        {**body, "client_nonce_b64url": secrets.token_urlsafe(32)},
    ):
        # Even a new valid ECDSA signature of unchanged fields is not the
        # retained original signature. No stale digest masks these conflicts.
        with pytest.raises(ProfilePairingError, match="operation_conflict"):
            action(proof(changed, a.key, operation))
    assert snapshot(database_connection) == before


def test_submit_freshness_pending_and_shared_source_budget(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h = admission_v2
    body, key = h.submit_body()
    old = h.runtime.clock.value - timedelta(seconds=121)
    with pytest.raises(ProfilePairingError):
        h.service.admission_submit(
            proof(
                {**body, "requested_at": old.isoformat().replace("+00:00", "Z")},
                key,
                "admission.submit",
            )
        )
    # Five real v2 and five real v1 requests consume the SAME source bucket.
    for marker in range(5):
        h.submit(source="d1-shared-source")
        _admission(h.runtime, source="d1-shared-source", nonce_marker=marker + 10)
    fresh, fresh_key = h.submit_body()
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError, match="admission_rate_limited"):
        h.service.admission_submit(
            proof(fresh, fresh_key, "admission.submit"), source="d1-shared-source"
        )
    assert snapshot(database_connection) == before
    # A v2 key cannot gain another pending slot by using a fresh UUID/nonce.
    first = h.submit(source="d1-other-source")
    second, _ = h.submit_body(first.key)
    with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
        h.service.admission_submit(
            proof(second, first.key, "admission.submit"), source="d1-another-source"
        )


def test_recovery_three_rotations_preserve_original_ttl_and_retire_locator(
    admission_v2: AdmissionHarness,
) -> None:
    h, a = admission_v2, admission_v2.submit()
    initial = a.created.copy()
    for _ in range(3):
        recovery = proof(h.recover(a), a.key, "admission.recover")
        a.created = h.service.admission_recover(recovery, a.body["request_id"])
        assert a.created["expires_at"] == initial["expires_at"]
        h.runtime.clock.advance(timedelta(seconds=2))
        with pytest.raises(ProfilePairingError):
            h.service.admission_recover(recovery, a.body["request_id"])
    with pytest.raises(ProfilePairingError):
        h.service.admission_recover(
            proof(h.recover(a), a.key, "admission.recover"), a.body["request_id"]
        )
    with pytest.raises(ProfilePairingError):
        h.runtime.service.bind_device_admission_review(
            actor=h.actor,
            web_session_id=h.actor.web_session_id,
            locator=initial["review_locator"],
            operation_id=uuid4(),
            request_sha256=b"r" * 32,
        )
    h.runtime.clock.advance(timedelta(seconds=900))
    with pytest.raises(ProfilePairingError):
        h.service.admission_recover(
            proof(h.recover(a), a.key, "admission.recover"), a.body["request_id"]
        )


def test_trusted_lost_reply_conflict_nonce_live_and_issue_bounds(
    admission_v2: AdmissionHarness,
) -> None:
    h, a = admission_v2, admission_v2.submit()
    h.bind(a)
    first_body = h.challenge_body(a)
    raw = proof(first_body, a.key, "trusted.challenge")
    first = h.service.trusted_challenge(raw)
    with pytest.raises(ProfilePairingError, match="trusted_key_unavailable"):
        h.service.trusted_challenge(raw)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        h.service.trusted_challenge(
            proof(
                {**first_body, "client_nonce_b64url": secrets.token_urlsafe(32)},
                a.key,
                "trusted.challenge",
            )
        )
    with pytest.raises(ProfilePairingError, match="trusted_key_unavailable"):
        h.service.trusted_challenge(
            proof({**first_body, "challenge_request_id": str(uuid4())}, a.key, "trusted.challenge")
        )
    for _ in range(2):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))
    h.runtime.clock.advance(timedelta(seconds=300))
    with pytest.raises(ProfilePairingError, match="trusted_key_unavailable"):
        h.service.trusted_exchange(proof(h.trusted_body(a, first), a.key, "trusted.exchange"))
    for _ in range(2):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))


def test_shared_capacity_and_unspent_challenge_cannot_first_bind_when_disabled(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h, a = admission_v2, admission_v2.submit()
    _, original, result = h.bind(a)
    challenge = h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))
    incoming = proof(h.trusted_body(a, challenge), a.key, "trusted.exchange")
    from autplay.adapters.postgresql.resource_limits import effective_account_limits

    with h.runtime.sessions() as session:
        capacity = effective_account_limits(session, h.actor.user_id).devices
    # Fill the actual shared policy, including the web actor's source device.
    count = database_connection.execute(
        "SELECT count(*) FROM account.device WHERE user_id=%s AND revoked_at IS NULL",
        (h.actor.user_id,),
    ).fetchone()
    assert count is not None and 0 < count[0] < capacity
    for _ in range(capacity - count[0]):
        database_connection.execute(
            (
                "INSERT INTO account.device(user_id,device_name,platform,app_version) "
                "VALUES(%s,'D1 legacy capacity','ANDROID','fixture')"
            ),
            (h.actor.user_id,),
        )
    database_connection.commit()
    with pytest.raises(ResourceAdmissionError) as denied:
        h.service.trusted_exchange(incoming)
    assert getattr(denied.value, "code", None) == "account_device_limit_reached"
    replay, replayed = h.service.admission_exchange(
        original, a.body["request_id"], a.created["poll_bearer"]
    )
    assert replayed and replay["session_id"] == result["session_id"]
    # Release one synthetic fixture device only, without changing the challenge.
    database_connection.execute(
        (
            "DELETE FROM account.device WHERE device_id=(SELECT device_id FROM "
            "account.device WHERE user_id=%s AND public_key IS NULL LIMIT 1)"
        ),
        (h.actor.user_id,),
    )
    database_connection.commit()
    h.gate["enabled"] = False
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="device_contract_unavailable"):
        h.service.trusted_exchange(incoming)
    assert snapshot(database_connection) == before


def test_v2_never_reads_or_finishes_v1_admission_even_for_android_same_uuid(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h = admission_v2
    legacy, created, key, original_digest = _admission(h.runtime)
    _bind_and_decide(h.runtime, h.actor, created, "APPROVE_ONCE")
    body, _ = h.submit_body(key)
    body.update(request_id=legacy["request_id"], platform="ANDROID")
    a = Admission(body, key, b"", created)
    before = snapshot(database_connection)
    for operation, command, action in (
        (
            "admission.poll",
            h.poll(a),
            lambda raw: h.service.admission_poll(
                raw, body["request_id"], require_instance(created["poll_bearer"], str)
            ),
        ),
        (
            "admission.recover",
            {**h.recover(a), "request_sha256": original_digest},
            lambda raw: h.service.admission_recover(raw, body["request_id"]),
        ),
        (
            "admission.exchange",
            {**h.exchange(a), "request_sha256": original_digest},
            lambda raw: h.service.admission_exchange(
                raw, body["request_id"], require_instance(created["poll_bearer"], str)
            ),
        ),
    ):
        with pytest.raises(ProfilePairingError, match="admission_request_unavailable"):
            action(proof(command, key, operation))
        assert snapshot(database_connection) == before
    # A genuine old request still succeeds with its original v1 fields/domain.
    old_poll = _signed(
        key,
        "autplay:s1b:admission-poll:v1\n",
        {
            "request_id": legacy["request_id"],
            "device_key_thumbprint_sha256": legacy["device_key_thumbprint_sha256"],
            "client_nonce_b64url": _nonce(90),
        },
    )
    assert (
        h.runtime.service.poll_device_admission(
            old_poll, require_instance(created["poll_bearer"], str)
        )["state"]
        == "APPROVED"
    )


def test_v1_refuses_new_v2_state_before_legacy_proof_or_retirement(
    admission_v2: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_connection: Connection[Any],
) -> None:
    h, a = admission_v2, admission_v2.submit()
    _, _, _ = h.bind(a)
    old_challenge = _signed(
        a.key,
        "autplay:s1b:trusted-reenrollment:v1\n",
        {
            "challenge_request_id": str(uuid4()),
            "account_id": str(h.actor.user_id),
            "expected_server_instance_id": a.body["expected_server_instance_id"],
            "expected_identity_epoch": a.body["expected_identity_epoch"],
            "expected_identity_thumbprint_sha256": a.body["expected_identity_thumbprint_sha256"],
            "device_key_thumbprint_sha256": a.body["device_key_thumbprint_sha256"],
            "device_public_key_jwk": a.body["device_public_key_jwk"],
            "client_nonce_b64url": _nonce(91),
        },
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("v2 state reached a v1 proof verifier")

    monkeypatch.setattr("autplay.application.profile_pairing.verify_p1363", forbidden)
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError, match="trusted_key_unavailable"):
        h.runtime.service.request_trusted_reenrollment_challenge(old_challenge)
    assert snapshot(database_connection) == before


def test_per_key_day_budget_is_not_reopened_by_rejection_or_new_ids(
    admission_v2: AdmissionHarness,
) -> None:
    h = admission_v2
    key = ec.generate_private_key(ec.SECP256R1())
    for marker in range(30):
        body, _ = h.submit_body(key)
        raw = proof(body, key, "admission.submit")
        created = h.service.admission_submit(raw, source=f"d1-source-{marker}")
        h.approve(Admission(body, key, raw, created), "REJECT")
    body, _ = h.submit_body(key)
    with pytest.raises(ProfilePairingError, match="admission_rate_limited"):
        h.service.admission_submit(proof(body, key, "admission.submit"), source="d1-next-source")


def test_trusted_account_and_live_key_limits_are_shared_with_v1(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    from autplay.domain.auth import AccountRole, Principal

    h, a = admission_v2, admission_v2.submit()
    h.bind(a)
    old_key = ec.generate_private_key(ec.SECP256R1())
    spki, thumb = public_spki(old_key), public_key_thumbprint(public_spki(old_key))
    database_connection.execute(
        (
            "INSERT INTO account.device(user_id,device_name,platform,app_version,public_k"
            "ey,public_key_thumbprint_sha256) VALUES(%s,'D1 old "
            "Android','ANDROID','fixture',%s,%s)"
        ),
        (h.actor.user_id, spki, thumb),
    )
    database_connection.execute(
        (
            "INSERT INTO account.trusted_device_key(user_id,device_key_thumbprint_sha256,"
            "device_public_key_spki,approved_request_id,key_reference,revision) "
            "VALUES(%s,%s,%s,%s,%s,1)"
        ),
        (h.actor.user_id, thumb, spki, uuid4(), uuid4()),
    )
    database_connection.commit()
    principal = Principal(h.actor.user_id, UUID(int=0), UUID(int=0), AccountRole.OWNER)
    for _ in range(3):
        h.runtime.service.issue_trusted_reenrollment_challenge(
            principal=principal,
            thumbprint=thumb,
            challenge_id=uuid4(),
            request_sha256=secrets.token_bytes(32),
            client_nonce_sha256=secrets.token_bytes(32),
        )
    # A real v2 proof by the legacy ANDROID key cannot get a fourth live slot.
    old_body, _ = h.submit_body(old_key)
    old_body["platform"] = "ANDROID"
    old = Admission(old_body, old_key, b"", {})
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        h.service.trusted_challenge(proof(h.challenge_body(old), old_key, "trusted.challenge"))
    for _ in range(2):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))
    # Only two live on the v2 key, but the account's shared five issues are used.
    with pytest.raises(ProfilePairingError, match="trusted_reenrollment_rate_limited"):
        h.service.trusted_challenge(proof(h.challenge_body(a), a.key, "trusted.challenge"))


def test_web_approval_and_first_exchange_share_identity_resource_lock_order(
    admission_v2: AdmissionHarness, database_connection: Connection[Any]
) -> None:
    h, a = admission_v2, admission_v2.submit()
    original, barrier = proof(h.exchange(a), a.key, "admission.exchange"), Barrier(2)

    def approve() -> None:
        barrier.wait(timeout=10)
        h.approve(a)

    def exchange() -> tuple[dict[str, object], bool] | None:
        barrier.wait(timeout=10)
        try:
            return h.service.admission_exchange(
                original, a.body["request_id"], a.created["poll_bearer"]
            )
        except ProfilePairingError as error:
            assert error.code == "admission_request_unavailable"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        approval, issuance = pool.submit(approve), pool.submit(exchange)
        approval.result(timeout=15)
        outcome = issuance.result(timeout=15)
    result, _ = outcome or h.service.admission_exchange(
        original, a.body["request_id"], a.created["poll_bearer"]
    )
    replay, replayed = h.service.admission_exchange(
        original, a.body["request_id"], a.created["poll_bearer"]
    )
    assert replayed and replay["session_id"] == result["session_id"]
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_admission_exchange_receipt"
    ).fetchone() == (1,)

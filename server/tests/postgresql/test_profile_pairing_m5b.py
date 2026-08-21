"""Real-PostgreSQL M5B enrollment, receipt, lifecycle, and replay gates."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.auth import BootstrapOwnerCommand
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccountRole, DeviceDescription, DevicePlatform, Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
)
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service
from autplay.runtime.settings import ApiSettings, RuntimeProfile
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from psycopg import Connection
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
HTTP_AUTH_SECRET = "m5b-http-retry-signing-secret-at-least-thirty-two-bytes"


@dataclass(frozen=True, slots=True)
class _ReadyProbe:
    def check(self) -> ReadinessResult:
        return ReadinessResult(ready=True, component="postgresql")


@pytest.fixture
def pairing_service(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ProfilePairingService]:
    engine = create_engine(database_url, pool_pre_ping=True)
    monkeypatch.setattr("autplay.application.profile_pairing._now", lambda: NOW)
    service = ProfilePairingService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="M5B test server",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=Hs256AccessTokenCodec(
            b"m5b-test-access-secret-at-least-thirty-two-bytes", issuer="m5b", audience="m5b"
        ),
        access_ttl=timedelta(minutes=10),
    )
    yield service
    engine.dispose()


def _owner(connection: Connection[Any]) -> Principal:
    row = connection.execute(
        "INSERT INTO account.user_account (display_name, role) "
        "VALUES ('M5B owner', 'OWNER') RETURNING user_id"
    ).fetchone()
    assert row is not None
    connection.commit()
    return Principal(UUID(str(row[0])), uuid4(), uuid4(), AccountRole.OWNER)


def _exchange_request(
    invitation: dict[str, object],
    *,
    exchange_id: UUID | None = None,
    key: ec.EllipticCurvePrivateKey | None = None,
    refresh: str = "M5B_REFRESH_TOKEN_012345678901234567890123456",
) -> tuple[dict[str, object], ec.EllipticCurvePrivateKey, str]:
    device_key = key or ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(device_key)
    request: dict[str, object] = {
        "contract_version": "v1",
        "schema_version": 1,
        "exchange_id": str(exchange_id or uuid4()),
        "binding_commit_id": str(uuid4()),
        "invitation_id": invitation["invitation_id"],
        "invitation_secret": invitation["invitation_secret"],
        "expected_server_instance_id": invitation["server_instance_id"],
        "expected_identity_epoch": invitation["identity_epoch"],
        "expected_identity_thumbprint_sha256": invitation["identity_thumbprint_sha256"],
        "expected_api_origin": invitation["api_origin"],
        "expected_stream_origin": invitation["stream_origin"],
        "expected_user_id": invitation["user_id"],
        "device_name": "M5B Android",
        "platform": "ANDROID",
        "app_version": "m5b-test",
        "device_public_key_spki_b64": base64.b64encode(spki).decode("ascii"),
        "device_key_thumbprint_sha256": public_key_thumbprint(spki).hex(),
        "next_refresh_token_sha256": hashlib.sha256(refresh.encode("ascii")).hexdigest(),
        "client_nonce_b64url": "0123456789abcdefghijkl",
        "signature_algorithm": "ES256-P1363",
    }
    digest = canonical_sha256(request)
    request["request_sha256"] = digest.hex()
    request["device_signature_b64url"] = sign_p1363(
        device_key, "AutPlay enrollment exchange v1\n", digest
    )
    return request, device_key, refresh


def _rotation_request(
    exchange: dict[str, object],
    key: ec.EllipticCurvePrivateKey,
    refresh: str,
    *,
    rotation_id: UUID | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "contract_version": "v1",
        "schema_version": 1,
        "rotation_id": str(rotation_id or uuid4()),
        "expected_server_instance_id": exchange["server_instance_id"],
        "expected_identity_epoch": 1,
        "device_id": exchange["device_id"],
        "parent_session_id": exchange["session_id"],
        "current_generation": 0,
        "current_refresh_token": refresh,
        "next_refresh_token_sha256": hashlib.sha256(b"replacement-refresh").hexdigest(),
        "signature_algorithm": "ES256-P1363",
    }
    digest = canonical_sha256(request)
    request["request_sha256"] = digest.hex()
    request["device_signature_b64url"] = sign_p1363(key, "AutPlay session rotation v1\n", digest)
    return request


def test_invitation_role_ttl_rate_cancel_and_lifecycle_idempotency(
    pairing_service: ProfilePairingService, database_connection: Connection[object]
) -> None:
    owner = _owner(database_connection)
    with pytest.raises(ProfilePairingError, match="unauthorized"):
        pairing_service.issue_invitation(
            Principal(owner.user_id, owner.device_id, owner.session_id, AccountRole.USER),
            uuid4(),
            60,
        )
    with pytest.raises(ProfilePairingError, match="enrollment_rate_limited"):
        pairing_service.issue_invitation(owner, uuid4(), 1801)
    invitation = pairing_service.issue_invitation(owner, uuid4(), 60)
    operation_id = uuid4()
    first = pairing_service.cancel_invitation(
        owner, UUID(str(invitation["invitation_id"])), operation_id, "owner_cancelled"
    )
    second = pairing_service.cancel_invitation(
        owner, UUID(str(invitation["invitation_id"])), operation_id, "owner_cancelled"
    )
    assert first["outcome"] == "APPLIED"
    assert second == first
    audit = database_connection.execute(
        "SELECT action, target_id, request_id, reason_code FROM audit.audit_event "
        "WHERE request_id = %s",
        (operation_id,),
    ).fetchone()
    assert audit == (
        "profile.invitation_cancelled",
        UUID(str(invitation["invitation_id"])),
        operation_id,
        "owner_cancelled",
    )
    for _ in range(5):
        pairing_service.issue_invitation(owner, uuid4(), 60)
    with pytest.raises(ProfilePairingError, match="enrollment_rate_limited"):
        pairing_service.issue_invitation(owner, uuid4(), 60)


def test_expired_exchange_and_rotation_receipts_are_deleted_in_bounded_real_pg_batches(
    pairing_service: ProfilePairingService, database_connection: Connection[object]
) -> None:
    invitation = pairing_service.issue_invitation(_owner(database_connection), uuid4(), 60)
    exchange_request, key, refresh = _exchange_request(invitation)
    exchange, _ = pairing_service.exchange(exchange_request)
    pairing_service.rotate(_rotation_request(exchange, key, refresh))
    database_connection.execute(
        "UPDATE account.enrollment_exchange_receipt SET receipt_expires_at = %s",
        (NOW - timedelta(hours=24),),
    )
    database_connection.execute(
        "UPDATE account.session_rotation_receipt SET receipt_expires_at = %s",
        (NOW - timedelta(hours=24),),
    )
    database_connection.commit()

    assert pairing_service.cleanup_expired_receipts(limit=1) == 1
    assert pairing_service.cleanup_expired_receipts(limit=10_000) == 1
    assert database_connection.execute(
        "SELECT count(*) FROM account.enrollment_exchange_receipt"
    ).fetchone() == (0,)
    assert database_connection.execute(
        "SELECT count(*) FROM account.session_rotation_receipt"
    ).fetchone() == (0,)


def test_concurrent_exchange_has_one_create_and_exact_replay_only(
    pairing_service: ProfilePairingService, database_connection: Connection[object]
) -> None:
    invitation = pairing_service.issue_invitation(_owner(database_connection), uuid4(), 60)
    request, _key, _refresh = _exchange_request(invitation)
    gate = Barrier(2)

    def exchange() -> tuple[str, object]:
        gate.wait(timeout=5)
        try:
            return "success", pairing_service.exchange(request)
        except ProfilePairingError as error:
            return "error", error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=15)
            for future in (executor.submit(exchange), executor.submit(exchange))
        ]
    # The creator always succeeds. The simultaneous identical caller may either observe the
    # committed receipt as an exact replay or lose the pre-commit race and receive the same
    # non-disclosing unavailable result; neither outcome may create a second binding.
    assert sum(kind == "success" for kind, _ in outcomes) in {1, 2}
    assert all(
        kind == "success" or value == "enrollment_invitation_unavailable"
        for kind, value in outcomes
    )
    assert database_connection.execute("SELECT count(*) FROM account.device").fetchone() == (1,)
    assert database_connection.execute("SELECT count(*) FROM account.user_session").fetchone() == (
        1,
    )
    replay, replayed = pairing_service.exchange(request)
    assert replayed is True
    assert replay["exchange_id"] == request["exchange_id"]
    changed, _other_key, _ = _exchange_request(
        invitation, exchange_id=UUID(str(request["exchange_id"]))
    )
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        pairing_service.exchange(changed)


def test_rotation_exact_replay_then_changed_replay_must_revoke_device(
    pairing_service: ProfilePairingService, database_connection: Connection[object]
) -> None:
    invitation = pairing_service.issue_invitation(_owner(database_connection), uuid4(), 60)
    request, key, refresh = _exchange_request(invitation)
    exchanged, _ = pairing_service.exchange(request)
    rotation = _rotation_request(exchanged, key, refresh)
    first, replayed = pairing_service.rotate(rotation)
    replay, replayed_again = pairing_service.rotate(rotation)
    assert replayed is False and replayed_again is True
    assert first["session_id"] == replay["session_id"]
    changed = dict(rotation)
    changed["next_refresh_token_sha256"] = hashlib.sha256(b"changed").hexdigest()
    digest = canonical_sha256(
        {k: v for k, v in changed.items() if k not in {"request_sha256", "device_signature_b64url"}}
    )
    changed["request_sha256"] = digest.hex()
    changed["device_signature_b64url"] = sign_p1363(key, "AutPlay session rotation v1\n", digest)
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        pairing_service.rotate(changed)
    assert database_connection.execute(
        "SELECT count(*) FROM account.user_session WHERE device_id = %s AND revoked_at IS NULL",
        (UUID(str(exchanged["device_id"])),),
    ).fetchone() == (0,)


@pytest.mark.parametrize("action", ["logout_current", "logout_all", "revoke_current"])
def test_http_terminal_lifecycle_exact_retry_uses_only_original_revoked_jwt_receipt(
    action: str,
    database_url: str,
    database_connection: Connection[object],
) -> None:
    settings = ApiSettings(
        profile=RuntimeProfile.TEST,
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr(HTTP_AUTH_SECRET),
        auth_issuer="autplay-m5b-http-test",
        auth_audience="autplay-m5b-http-client",
        access_token_ttl_seconds=600,
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    auth = build_auth_service(settings, engine)
    pairing = ProfilePairingService(
        sessionmaker(engine, class_=Session, expire_on_commit=False),
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="M5B HTTP test server",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=Hs256AccessTokenCodec(
            HTTP_AUTH_SECRET.encode("ascii"),
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
        ),
        access_ttl=timedelta(seconds=settings.access_token_ttl_seconds),
    )
    owner = auth.bootstrap_owner(
        BootstrapOwnerCommand(
            display_name="M5B HTTP owner",
            device=DeviceDescription(
                name="M5B bootstrap device",
                platform=DevicePlatform.ANDROID,
                app_version="m5b-http-test",
            ),
        )
    )
    owner_principal = Principal(
        owner.user_id,
        owner.device_id,
        owner.session_id,
        AccountRole.OWNER,
    )
    invitation = pairing.issue_invitation(owner_principal, uuid4(), 600)
    exchange_request, _key, _refresh = _exchange_request(invitation)
    exchanged, _ = pairing.exchange(exchange_request)
    access_token = str(exchanged["access_token"])
    device_id = UUID(str(exchanged["device_id"]))
    operation_id = uuid4()
    path = {
        "logout_current": "/api/v1/account/sessions/current/logout",
        "logout_all": "/api/v1/account/sessions/logout-all",
        "revoke_current": f"/api/v1/account/devices/{device_id}/revoke",
    }[action]
    body = {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(operation_id),
        "reason_code": "user_requested",
    }
    app = create_app(
        settings,
        readiness_probe=_ReadyProbe(),
        auth_service=auth,
        profile_pairing_service=pairing,
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                path,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            exact_retry = client.post(
                path,
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            changed_retry = client.post(
                path,
                json={**body, "reason_code": "changed_reason"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            unrelated_token = client.post(
                path,
                json=body,
                headers={"Authorization": f"Bearer {owner.access_token}"},
            )
    finally:
        engine.dispose()

    assert first.status_code == 200
    assert exact_retry.status_code == 200
    assert exact_retry.json() == first.json()
    assert exact_retry.headers["cache-control"] == "no-store"
    assert exact_retry.headers["pragma"] == "no-cache"
    assert changed_retry.status_code == 401
    assert unrelated_token.status_code in {401, 403}
    claims = auth.decode_access(access_token)
    assert database_connection.execute(
        "SELECT actor_access_token_id, reason_code, outcome, terminal_at "
        "FROM account.profile_lifecycle_command WHERE operation_id = %s",
        (operation_id,),
    ).fetchone() == (
        claims.token_id,
        "user_requested",
        first.json()["outcome"],
        datetime.fromisoformat(first.json()["terminal_at"].replace("Z", "+00:00")),
    )

"""UNEXECUTED real-PostgreSQL/HTTP D1 fixtures; synthetic keys and credentials.

These future gates intentionally exercise the real application transactions,
shared v1 tables/locks and exact original proofs. No result has been qualified.
"""

from __future__ import annotations

import base64
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
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.retained_deletion_reservations import (
    configure_retained_deletion_reservations,
)
from autplay.adapters.postgresql.self_device_pairing import SqlAlchemySelfPairingUnitOfWorkFactory
from autplay.application.device_compatibility import DeviceCompatibilityService
from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.application.portable_account import PortableAccountService
from autplay.application.profile_pairing import (
    ProfilePairingService,
    cleanup_expired_pairing_receipts,
)
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain.account_recovery import AccountRecoveryError
from autplay.domain.auth import Principal
from autplay.domain.canonical_json import JsonValue
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationPolicy
from autplay.domain.device_profile_v2 import MAX_SAFE_INTEGER
from autplay.domain.privacy_deletion import DeletionRequestEvidence
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
)
from autplay.domain.self_device_pairing import SelfPairingError
from autplay.entrypoints.device_profile_v2_http import create_device_profile_v2_router
from autplay.entrypoints.profile_pairing_http import create_profile_pairing_router
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import Connection
from typed_assertions import present, require_instance

from .controlled_clock import control_database_clock
from .test_account_recovery import configured
from .test_account_recovery import request as recovery_request
from .test_device_admission_s1b import (
    EXCHANGE_DOMAIN as ADMISSION_DOMAIN,
)
from .test_device_admission_s1b import (
    TRUSTED_REENROLLMENT_DOMAIN,
    _admission,
    _bind_and_decide,
    _jwk,
    _nonce,
    _web_actor,
)
from .test_device_admission_s1b import (
    _Clock as AdmissionClock,
)
from .test_device_admission_s1b import (
    _Runtime as AdmissionRuntime,
)
from .test_device_admission_s1b import (
    _signed as admission_signed,
)
from .test_profile_pairing_m5b import _exchange_request, _owner, _rotation_request
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_self_device_pairing import PairingHarness

PREFIX = "/api/v1/device-compatibility/v2"
ENROLL_DOMAIN = "autplay:device-compatibility:enrollment:exchange:v2\n"
ROTATE_DOMAIN = "autplay:device-compatibility:session:rotate:v2\n"


def signed(body: dict[str, Any], key: ec.EllipticCurvePrivateKey, domain: str) -> bytes:
    body.pop("request_sha256", None)
    body.pop("device_signature_b64url", None)
    digest = canonical_sha256(body)
    body["request_sha256"] = digest.hex()
    body["device_signature_b64url"] = sign_p1363(key, domain, digest)
    return rfc8785.dumps(cast(JsonValue, body))


def credential() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class ProfileHarness:
    service: DeviceProfileServiceV2
    identity: ProfilePairingService
    owner: Principal
    platform: str
    gate: dict[str, bool]
    clock: dict[str, datetime]

    def invitation(self) -> dict[str, Any]:
        return self.identity.issue_invitation(self.owner, uuid4(), 600)

    def enrollment(
        self,
        *,
        invitation: dict[str, Any] | None = None,
        key: ec.EllipticCurvePrivateKey | None = None,
    ) -> tuple[dict[str, Any], ec.EllipticCurvePrivateKey, str]:
        invitation = invitation or self.invitation()
        key = key or ec.generate_private_key(ec.SECP256R1())
        spki, refresh = public_spki(key), credential()
        body: dict[str, Any] = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": self.platform,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "invitation_id": invitation["invitation_id"],
            "invitation_secret": invitation["invitation_secret"],
            "expected_user_id": invitation["user_id"],
            "device_name": "D1 synthetic device",
            "app_version": "d1-fixture",
            "device_public_key_spki_b64": base64.b64encode(spki).decode(),
            "device_key_thumbprint_sha256": public_key_thumbprint(spki).hex(),
            "next_refresh_token_sha256": hashlib.sha256(refresh.encode("ascii")).hexdigest(),
            "client_nonce_b64url": base64.urlsafe_b64encode(uuid4().bytes).rstrip(b"=").decode(),
            "signature_algorithm": "ES256-P1363",
            **_trust(invitation),
        }
        return body, key, refresh

    def enroll(self) -> tuple[dict[str, Any], dict[str, Any], ec.EllipticCurvePrivateKey, str]:
        body, key, refresh = self.enrollment()
        result, replayed = self.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
        assert replayed is False
        return body, result, key, refresh

    def rotation(
        self,
        binding: dict[str, Any],
        original: dict[str, Any],
        refresh: str,
    ) -> tuple[dict[str, Any], str]:
        next_refresh = credential()
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": self.platform,
            "rotation_id": str(uuid4()),
            "device_id": binding.get("device_id", original.get("device_id")),
            "parent_session_id": binding["session_id"],
            "current_generation": binding.get("generation", 0),
            "current_refresh_token": refresh,
            "next_refresh_token_sha256": hashlib.sha256(next_refresh.encode("ascii")).hexdigest(),
            "signature_algorithm": "ES256-P1363",
            **{name: original[name] for name in _TRUST_NAMES},
        }, next_refresh


_TRUST_NAMES = (
    "expected_server_instance_id",
    "expected_identity_epoch",
    "expected_identity_thumbprint_sha256",
    "expected_api_origin",
    "expected_stream_origin",
)


def _trust(invitation: dict[str, Any]) -> dict[str, Any]:
    return {
        "expected_server_instance_id": invitation["server_instance_id"],
        "expected_identity_epoch": invitation["identity_epoch"],
        "expected_identity_thumbprint_sha256": invitation["identity_thumbprint_sha256"],
        "expected_api_origin": invitation["api_origin"],
        "expected_stream_origin": invitation["stream_origin"],
    }


@pytest.fixture(params=["ANDROID", "MACOS"])
def profile(
    request: pytest.FixtureRequest,
    pairing_service: ProfilePairingService,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ProfileHarness:
    ledger = FilesystemDeletionLedger(
        tmp_path / "profile-reservations.sqlite3",
        b"d1-profile-ledger-test-secret-32bytes",
        "fixture-v1",
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=90))
    configure_retained_deletion_reservations(
        pairing_service._sessions, ledger=ledger, evidence_ready=lambda: True
    )
    clock, gate = {"now": datetime.now(UTC)}, {"enabled": True}
    monkeypatch.setattr("autplay.application.profile_pairing._now", lambda: clock["now"])
    monkeypatch.setattr("autplay.application.device_profile_service_v2._now", lambda: clock["now"])
    control_database_clock(
        monkeypatch,
        lambda: clock["now"],
        "autplay.application.profile_pairing",
        "autplay.application.device_profile_service_v2",
    )
    owner = _owner(database_connection)
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=10",
    )
    database_connection.commit()
    service = DeviceProfileServiceV2(
        pairing_service._sessions,
        identity=pairing_service,
        enabled=lambda family: gate["enabled"],
        access_tokens=pairing_service._access,
        access_ttl=pairing_service._access_ttl,
    )
    return ProfileHarness(service, pairing_service, owner, request.param, gate, clock)


def _client(profile: ProfileHarness) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_device_profile_v2_router(profile.service), prefix="/api/v1")
    app.include_router(
        create_profile_pairing_router(profile.identity, authenticated=lambda request: None),
        prefix="/api/v1",
    )
    return TestClient(app)


def snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    result = tuple(
        connection.execute(statement).fetchall()
        for statement in (
            "SELECT * FROM account.device ORDER BY device_id",
            "SELECT * FROM account.user_session ORDER BY session_id",
            "SELECT * FROM account.enrollment_invitation ORDER BY invitation_id",
            "SELECT * FROM account.enrollment_exchange_receipt ORDER BY exchange_id",
            "SELECT * FROM account.session_rotation_receipt ORDER BY rotation_id",
            "SELECT * FROM account.device_admission ORDER BY request_id",
            "SELECT * FROM account.device_admission_exchange_receipt ORDER BY exchange_id",
            "SELECT * FROM account.device_admission_web_operation_receipt ORDER BY operation_id",
            "SELECT * FROM account.profile_lifecycle_command ORDER BY operation_id",
            "SELECT * FROM account.device_key_block ORDER BY user_id,device_key_thumbprint_sha256",
            (
                "SELECT * FROM account.trusted_device_key ORDER BY "
                "user_id,device_key_thumbprint_sha256"
            ),
            "SELECT * FROM account.trusted_device_reenrollment_challenge ORDER BY challenge_id",
            "SELECT * FROM account.user_account ORDER BY user_id",
            "SELECT * FROM account.self_device_pairing ORDER BY ceremony_id",
            "SELECT * FROM account.self_pairing_command ORDER BY operation_id",
            "SELECT * FROM account.account_recovery_credential ORDER BY user_id",
            "SELECT * FROM account.account_recovery_operation ORDER BY operation_id",
            "SELECT * FROM account.account_deletion_request ORDER BY deletion_request_id",
            "SELECT * FROM account.portable_challenge ORDER BY challenge_id",
            "SELECT * FROM account.portable_identity ORDER BY user_id",
            "SELECT * FROM account.web_session ORDER BY web_session_id",
            "SELECT * FROM social.public_id_registration ORDER BY user_id",
            "SELECT * FROM audit.audit_event ORDER BY audit_event_id",
        )
    )
    connection.commit()
    return result


def test_http_first_commit_lost_reply_replay_and_exact_storage(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    body, key, refresh = profile.enrollment()
    raw = signed(body, key, ENROLL_DOMAIN)
    with _client(profile) as client:
        first = client.post(
            PREFIX + "/enrollment/exchanges",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        replay = client.post(
            PREFIX + "/enrollment/exchanges",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert first.status_code == 201 and replay.status_code == 200
        result = first.json()
        assert result["platform"] == profile.platform and result["refresh_generation"] == 0
        assert result["contract_version"] == "v2" and result["schema_version"] == 2
        assert result["session_id"] == replay.json()["session_id"]
        assert result["binding_commit_id"] == replay.json()["binding_commit_id"]
        for response in (first, replay):
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"
            assert "refresh_token" not in response.json()
        rotate, _ = profile.rotation(result, body, refresh)
        rotation_raw = signed(rotate, key, ROTATE_DOMAIN)
        rotated = client.post(
            PREFIX + "/sessions/rotate",
            content=rotation_raw,
            headers={"Content-Type": "application/json"},
        )
        replay_rotation = client.post(
            PREFIX + "/sessions/rotate",
            content=rotation_raw,
            headers={"Content-Type": "application/json"},
        )
        assert rotated.status_code == replay_rotation.status_code == 200
        assert rotated.json()["session_id"] == replay_rotation.json()["session_id"]
        assert rotated.json()["generation"] == 1
        assert rotated.json()["family_id"] == result["session_id"]
        assert (
            rotated.json()["refresh_absolute_expires_at"] == result["refresh_absolute_expires_at"]
        )
    stored = database_connection.execute(
        "SELECT contract_version,platform,original_document_sha256,original_request_evidence "
        "FROM account.enrollment_exchange_receipt WHERE exchange_id=%s",
        (UUID(body["exchange_id"]),),
    ).fetchone()
    assert stored is not None and stored[:2] == ("v2", profile.platform)
    assert bytes(stored[2]) == hashlib.sha256(raw).digest()
    evidence = stored[3]
    assert (
        evidence["signature_hex"]
        == base64.urlsafe_b64decode(body["device_signature_b64url"] + "==").hex()
    )
    assert evidence["domain"] == ENROLL_DOMAIN
    assert (
        evidence["credential_hashes"]["invitation_secret_sha256"]
        == hashlib.sha256(body["invitation_secret"].encode()).hexdigest()
    )
    storage = str(snapshot(database_connection))
    for secret in (
        body["invitation_secret"],
        refresh,
        rotate["current_refresh_token"],
        result["access_token"],
    ):
        assert secret not in storage
    rotation_evidence = present(
        database_connection.execute(
            (
                "SELECT original_request_evidence FROM account.session_rotation_receipt "
                "WHERE rotation_id=%s"
            ),
            (UUID(rotate["rotation_id"]),),
        ).fetchone()
    )[0]
    assert "current_refresh_token" not in rotation_evidence["fields"]
    assert (
        rotation_evidence["credential_hashes"]["current_refresh_token_sha256"]
        == hashlib.sha256(refresh.encode()).hexdigest()
    )


def test_flag_off_retains_exact_replays_and_refuses_first_commits(profile: ProfileHarness) -> None:
    body, binding, key, refresh = profile.enroll()
    enrollment_raw = rfc8785.dumps(cast(JsonValue, body))
    rotation, next_refresh = profile.rotation(binding, body, refresh)
    rotation_raw = signed(rotation, key, ROTATE_DOMAIN)
    successor, _ = profile.service.session_rotate(rotation_raw)
    fresh, fresh_key, _ = profile.enrollment()
    profile.gate["enabled"] = False
    assert profile.service.session_rotate(rotation_raw)[0]["session_id"] == successor["session_id"]
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        profile.service.enrollment_exchange(enrollment_raw)  # retired result session
    next_rotation, _ = profile.rotation(successor, rotation, next_refresh)
    next_rotation["device_id"] = binding["device_id"]
    for action, raw in (
        (profile.service.enrollment_exchange, signed(fresh, fresh_key, ENROLL_DOMAIN)),
        (profile.service.session_rotate, signed(next_rotation, key, ROTATE_DOMAIN)),
    ):
        with pytest.raises(DeviceCompatibilityError) as error:
            action(raw)
        assert error.value.status == 503 and error.value.code == "device_contract_unavailable"


def test_flag_off_unrotated_enrollment_replays_without_consuming_another_invitation(
    profile: ProfileHarness,
) -> None:
    body, result, _, _ = profile.enroll()
    profile.gate["enabled"] = False
    replay, repeated = profile.service.enrollment_exchange(rfc8785.dumps(cast(JsonValue, body)))
    assert repeated and replay["session_id"] == result["session_id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_server_instance_id", str(UUID(int=17))),
        ("expected_identity_epoch", 2),
        ("expected_identity_thumbprint_sha256", "ab" * 32),
        ("expected_api_origin", "https://other.test.invalid"),
        ("expected_stream_origin", "https://other-stream.test.invalid"),
        ("expected_user_id", str(UUID(int=18))),
        ("binding_commit_id", str(UUID(int=19))),
        ("contract_version", "v1"),
        ("schema_version", True),
        ("platform", "OTHER"),
        ("device_key_thumbprint_sha256", "cd" * 32),
        ("device_name", "Changed signed name"),
    ],
)
def test_enrollment_mutation_never_reissues_or_reopens_consumption(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    field: str,
    value: Any,
) -> None:
    body, _, key, _ = profile.enroll()
    before = snapshot(database_connection)
    changed = dict(body, **{field: value})
    with _client(profile) as client:
        response = client.post(
            PREFIX + "/enrollment/exchanges",
            content=signed(changed, key, ENROLL_DOMAIN),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code in {400, 401, 409}
    assert snapshot(database_connection) == before


@pytest.mark.parametrize(
    "domain", ["AutPlay enrollment exchange v1\n", ROTATE_DOMAIN, ENROLL_DOMAIN.rstrip("\n")]
)
def test_wrong_proof_domain_cannot_consume_invitation(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    domain: str,
) -> None:
    body, key, _ = profile.enrollment()
    before = snapshot(database_connection)
    with _client(profile) as client:
        response = client.post(
            PREFIX + "/enrollment/exchanges",
            content=signed(body, key, domain),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 401
    assert snapshot(database_connection) == before


@pytest.mark.parametrize(
    "terminal", ["account", "device", "session", "authority", "key", "platform", "expired"]
)
@pytest.mark.usefixtures("replacement_owner")
def test_current_authority_defeats_enrollment_replay(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    terminal: str,
) -> None:
    body, result, _, _ = profile.enroll()
    if terminal == "account":
        database_connection.execute(
            "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s",
            (profile.owner.user_id,),
        )
    elif terminal == "authority":
        database_connection.execute(
            (
                "UPDATE account.user_account SET authority_generation=authority_generation+1 "
                "WHERE user_id=%s"
            ),
            (profile.owner.user_id,),
        )
    elif terminal in {"device", "session"}:
        table, column = (
            ("device", "device_id") if terminal == "device" else ("user_session", "session_id")
        )
        database_connection.execute(
            f"UPDATE account.{table} SET revoked_at=%s WHERE {column}=%s",
            (profile.clock["now"], UUID(result[column])),
        )
    elif terminal == "key":
        database_connection.execute(
            "UPDATE account.device SET public_key=%s WHERE device_id=%s",
            (public_spki(ec.generate_private_key(ec.SECP256R1())), UUID(result["device_id"])),
        )
    elif terminal == "platform":
        database_connection.execute(
            "UPDATE account.device SET platform='OTHER' WHERE device_id=%s",
            (UUID(result["device_id"]),),
        )
    else:
        profile.clock["now"] = profile.clock["now"] + timedelta(days=90, seconds=300)
    database_connection.commit()
    before = snapshot(database_connection)
    with _client(profile) as client:
        response = client.post(PREFIX + "/enrollment/exchanges", json=body)
    assert response.status_code in {401, 409}
    assert snapshot(database_connection) == before


def test_same_spki_historical_conflict_without_thumbprint_fails_closed(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    body, key, _ = profile.enrollment()
    database_connection.execute(
        "INSERT INTO account.device(user_id,device_name,platform,app_ver"
        "sion,public_key,revoked_at) "
        "VALUES(%s,'historical other platform','OTHER','fixture',%s,%s)",
        (profile.owner.user_id, public_spki(key), profile.clock["now"]),
    )
    database_connection.commit()
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="device_platform_conflict"):
        profile.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
    assert snapshot(database_connection) == before


def test_v1_routes_keep_positive_bytes_and_cross_version_receipts_conflict(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    invitation = profile.invitation()
    old, old_key, old_refresh = _exchange_request(invitation, refresh=credential())
    old_bytes = rfc8785.dumps(cast(JsonValue, old))
    with _client(profile) as client:
        old_response = client.post("/api/v1/pairing/enrollment/exchanges", json=old)
        assert old_response.status_code == 201
        old_result = old_response.json()
        old_rotate = _rotation_request(old_result, old_key, old_refresh)
        rotation_response = client.post("/api/v1/account/sessions/rotate", json=old_rotate)
        assert rotation_response.status_code == 200
        rotated = rotation_response.json()
    assert old_result["contract_version"] == rotated["contract_version"] == "v1"
    assert rfc8785.dumps(cast(JsonValue, old)) == old_bytes
    stored = database_connection.execute(
        "SELECT contract_version,platform,original_document_sha256,origi"
        "nal_request_evidence,authority_generation "
        "FROM account.enrollment_exchange_receipt WHERE exchange_id=%s",
        (UUID(require_instance(old["exchange_id"], str)),),
    ).fetchone()
    assert stored == ("v1", None, None, None, None)
    body, key, _ = profile.enrollment()
    body["exchange_id"] = old["exchange_id"]
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError):
        profile.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
    assert snapshot(database_connection) == before
    body, result, key, refresh = profile.enroll()
    new_v1, _, _ = _exchange_request(profile.invitation(), exchange_id=UUID(body["exchange_id"]))
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        profile.identity.exchange(new_v1)
    assert snapshot(database_connection) == before
    rotation, _ = profile.rotation(result, body, refresh)
    rotation["rotation_id"] = body["exchange_id"]
    with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert snapshot(database_connection) == before


def test_capacity_is_shared_and_replay_does_not_need_another_slot(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    body, _, _, _ = profile.enroll()
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=2"
    )
    database_connection.commit()
    assert profile.service.enrollment_exchange(rfc8785.dumps(cast(JsonValue, body)))[1] is True
    fresh, key, _ = profile.enrollment()
    with _client(profile) as client:
        response = client.post(
            PREFIX + "/enrollment/exchanges",
            content=signed(fresh, key, ENROLL_DOMAIN),
            headers={"Content-Type": "application/json"},
        )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "account_device_limit_reached"
    )
    legacy, _, _ = _exchange_request(profile.invitation())
    with pytest.raises(ProfilePairingError, match="account_device_limit_reached"):
        profile.identity.exchange(legacy)


def test_parallel_exact_enrollment_commits_once(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    body, key, _ = profile.enrollment()
    raw, barrier = signed(body, key, ENROLL_DOMAIN), Barrier(2)

    def exchange() -> tuple[dict[str, object], bool]:
        barrier.wait(timeout=10)
        return profile.service.enrollment_exchange(raw)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: exchange(), range(2)))
    assert sorted(replayed for _, replayed in results) == [False, True]
    assert len({result["session_id"] for result, _ in results}) == 1
    assert database_connection.execute(
        "SELECT count(*) FROM account.enrollment_exchange_receipt"
    ).fetchone() == (1,)


def test_rotation_chain_absolute_ttl_overflow_and_changed_replay(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    original, binding, key, refresh = profile.enroll()
    first, next_refresh = profile.rotation(binding, original, refresh)
    first_raw = signed(first, key, ROTATE_DOMAIN)
    successor, _ = profile.service.session_rotate(first_raw)
    second, _ = profile.rotation(successor, first, next_refresh)
    second["device_id"] = binding["device_id"]
    second_raw = signed(second, key, ROTATE_DOMAIN)
    next_successor, _ = profile.service.session_rotate(second_raw)
    assert next_successor["generation"] == 2
    assert next_successor["refresh_absolute_expires_at"] == binding["refresh_absolute_expires_at"]
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        profile.service.session_rotate(first_raw)  # successor already retired
    # New operation ID on the valid retired parent is changed replay; revoke device.
    changed = dict(first, rotation_id=str(uuid4()))
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        profile.service.session_rotate(signed(changed, key, ROTATE_DOMAIN))
    assert database_connection.execute(
        "SELECT revoked_at IS NOT NULL FROM account.device WHERE device_id=%s",
        (UUID(binding["device_id"]),),
    ).fetchone() == (True,)


def test_generation_overflow_refuses_before_mutation(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    original, binding, key, refresh = profile.enroll()
    database_connection.execute(
        "UPDATE account.user_session SET generation=%s WHERE session_id=%s",
        (MAX_SAFE_INTEGER, UUID(binding["session_id"])),
    )
    database_connection.commit()
    rotation, _ = profile.rotation(binding, original, refresh)
    rotation["current_generation"] = MAX_SAFE_INTEGER
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert snapshot(database_connection) == before


def test_signed_opposite_platform_cannot_reclassify_key(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    original, binding, key, refresh = profile.enroll()
    other = "MACOS" if profile.platform == "ANDROID" else "ANDROID"
    fresh, _, _ = profile.enrollment(key=key)
    fresh["platform"] = other
    rotation, _ = profile.rotation(binding, original, refresh)
    rotation["platform"] = other
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="device_platform_conflict"):
        profile.service.enrollment_exchange(signed(fresh, key, ENROLL_DOMAIN))
    with pytest.raises(DeviceCompatibilityError, match="device_platform_conflict"):
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert snapshot(database_connection) == before


def test_two_operations_cannot_race_for_same_uuid(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    original, binding, key, refresh = profile.enroll()
    fresh, fresh_key, _ = profile.enrollment()
    rotation, _ = profile.rotation(binding, original, refresh)
    rotation["rotation_id"] = fresh["exchange_id"]
    barrier = Barrier(2)
    attempts = (
        (profile.service.enrollment_exchange, signed(fresh, fresh_key, ENROLL_DOMAIN)),
        (profile.service.session_rotate, signed(rotation, key, ROTATE_DOMAIN)),
    )

    def perform(index: int) -> str:
        action, raw = attempts[index]
        barrier.wait(timeout=10)
        try:
            action(raw)
            return "committed"
        except DeviceCompatibilityError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(perform, range(2)))
    assert sorted(results) == ["committed", "operation_conflict"]
    assert database_connection.execute(
        "SELECT (SELECT count(*) FROM account.enrollment_exchange_receipt WHERE exchange_id=%s) + "
        "(SELECT count(*) FROM account.session_rotation_receipt WHERE rotation_id=%s)",
        (UUID(fresh["exchange_id"]), UUID(fresh["exchange_id"])),
    ).fetchone() == (1,)


def test_qualified_registry_stays_empty_and_v2_receipts_use_existing_pruning(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)
    body, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, body, refresh)
    profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    negotiation = DeviceCompatibilityService(
        profile.identity._sessions,
        identity=profile.identity,
        policy=NegotiationPolicy(enabled=True),
    )
    assert require_instance(negotiation.discovery()["payload"], dict)["families"] == []
    assert (
        cleanup_expired_pairing_receipts(
            profile.identity._sessions, now=profile.clock["now"] + timedelta(days=90, seconds=299)
        )
        == 0
    )
    assert (
        cleanup_expired_pairing_receipts(
            profile.identity._sessions,
            limit=1,
            now=profile.clock["now"] + timedelta(days=90, seconds=300),
        )
        == 1
    )
    assert (
        cleanup_expired_pairing_receipts(
            profile.identity._sessions,
            limit=100,
            now=profile.clock["now"] + timedelta(days=90, seconds=300),
        )
        == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_server_instance_id", str(UUID(int=41))),
        ("expected_identity_epoch", 2),
        ("expected_identity_thumbprint_sha256", "ef" * 32),
        ("expected_api_origin", "https://changed-api.test.invalid"),
        ("expected_stream_origin", "https://changed-stream.test.invalid"),
        ("contract_version", "v1"),
        ("schema_version", 1),
        ("platform", "WEB"),
        ("current_refresh_token", base64.urlsafe_b64encode(b"q" * 32).rstrip(b"=").decode()),
    ],
)
def test_rotation_trust_version_and_refresh_mutations_have_no_effect(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    field: str,
    value: Any,
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    rotation[field] = value
    before = snapshot(database_connection)
    with _client(profile) as client:
        response = client.post(
            PREFIX + "/sessions/rotate",
            content=signed(rotation, key, ROTATE_DOMAIN),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code in {400, 401}
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("mutation", ["generation", "next_refresh", "signature"])
def test_changed_signed_rotation_replay_revokes_device_durably(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    mutation: str,
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    raw = signed(rotation, key, ROTATE_DOMAIN)
    successor, _ = profile.service.session_rotate(raw)
    if mutation == "generation":
        rotation["current_generation"] += 1
    elif mutation == "next_refresh":
        rotation["next_refresh_token_sha256"] = hashlib.sha256(credential().encode()).hexdigest()
    # ECDSA re-signing of unchanged content also fails the exact original proof
    # comparison: the client must retain its original signed bytes for retry.
    changed = signed(rotation, key, ROTATE_DOMAIN)
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        profile.service.session_rotate(changed)
    assert database_connection.execute(
        "SELECT revoked_at IS NOT NULL FROM account.device WHERE device_id=%s",
        (UUID(binding["device_id"]),),
    ).fetchone() == (True,)
    assert database_connection.execute(
        "SELECT revoked_at IS NOT NULL FROM account.user_session WHERE session_id=%s",
        (UUID(require_instance(successor["session_id"], str)),),
    ).fetchone() == (True,)


@pytest.mark.parametrize(
    "terminal", ["logout", "device", "account", "authority", "expiry", "receipt"]
)
@pytest.mark.usefixtures("replacement_owner")
def test_rotation_replay_never_restores_current_authority(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    terminal: str,
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    raw = signed(rotation, key, ROTATE_DOMAIN)
    successor, _ = profile.service.session_rotate(raw)
    if terminal == "logout":
        database_connection.execute(
            "UPDATE account.user_session SET revoked_at=%s WHERE session_id=%s",
            (profile.clock["now"], UUID(require_instance(successor["session_id"], str))),
        )
    elif terminal == "device":
        database_connection.execute(
            "UPDATE account.device SET revoked_at=%s WHERE device_id=%s",
            (profile.clock["now"], UUID(binding["device_id"])),
        )
    elif terminal == "account":
        database_connection.execute(
            "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s",
            (profile.owner.user_id,),
        )
    elif terminal == "authority":
        database_connection.execute(
            (
                "UPDATE account.user_account SET authority_generation=authority_generation+1 "
                "WHERE user_id=%s"
            ),
            (profile.owner.user_id,),
        )
    else:
        profile.clock["now"] = profile.clock["now"] + timedelta(
            days=90, seconds=300 if terminal == "receipt" else 0
        )
    database_connection.commit()
    profile.gate["enabled"] = False
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError):
        profile.service.session_rotate(raw)
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("platform", ["WEB", "OTHER"])
def test_rotation_never_guesses_android_for_legacy_platforms(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    platform: str,
) -> None:
    original, binding, key, refresh = profile.enroll()
    database_connection.execute(
        "UPDATE account.device SET platform=%s WHERE device_id=%s",
        (platform, UUID(binding["device_id"])),
    )
    database_connection.commit()
    rotation, _ = profile.rotation(binding, original, refresh)
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="device_platform_conflict"):
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert snapshot(database_connection) == before


def test_parallel_exact_rotation_produces_one_successor(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    raw, barrier = signed(rotation, key, ROTATE_DOMAIN), Barrier(2)

    def rotate() -> tuple[dict[str, object], bool]:
        barrier.wait(timeout=10)
        return profile.service.session_rotate(raw)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rotate(), range(2)))
    assert sorted(replayed for _, replayed in results) == [False, True]
    assert len({result["session_id"] for result, _ in results}) == 1
    assert database_connection.execute(
        "SELECT count(*) FROM account.session_rotation_receipt"
    ).fetchone() == (1,)


def test_different_keys_cannot_consume_same_invitation_concurrently(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    invitation, barrier = profile.invitation(), Barrier(2)
    first, first_key, _ = profile.enrollment(invitation=invitation)
    second, second_key, _ = profile.enrollment(invitation=invitation)
    raws = [signed(first, first_key, ENROLL_DOMAIN), signed(second, second_key, ENROLL_DOMAIN)]

    def exchange(raw: bytes) -> str:
        barrier.wait(timeout=10)
        try:
            profile.service.enrollment_exchange(raw)
            return "committed"
        except ProfilePairingError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(exchange, raws))
    assert sorted(results) == ["committed", "enrollment_invitation_unavailable"]
    assert database_connection.execute(
        "SELECT count(*) FROM account.enrollment_exchange_receipt"
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "terminal", ["cancelled", "expired", "bad_secret", "wrong_user", "blocked"]
)
def test_first_enrollment_revalidates_invitation_account_and_key(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    terminal: str,
) -> None:
    body, key, _ = profile.enrollment()
    if terminal == "cancelled":
        database_connection.execute(
            "UPDATE account.enrollment_invitation SET cancelled_at=%s WHERE invitation_id=%s",
            (profile.clock["now"], UUID(body["invitation_id"])),
        )
    elif terminal == "expired":
        profile.clock["now"] = profile.clock["now"] + timedelta(seconds=600)
    elif terminal == "bad_secret":
        body["invitation_secret"] = credential()
    elif terminal == "wrong_user":
        body["expected_user_id"] = str(uuid4())
    else:
        database_connection.execute(
            (
                "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) "
                "VALUES(%s,%s)"
            ),
            (profile.owner.user_id, public_key_thumbprint(public_spki(key))),
        )
    database_connection.commit()
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError):
        profile.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
    assert snapshot(database_connection) == before


def test_key_and_platform_are_signed_and_wrong_rotation_domain_cannot_issue(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    before = snapshot(database_connection)
    with _client(profile) as client:
        for signing_key, domain in (
            (ec.generate_private_key(ec.SECP256R1()), ROTATE_DOMAIN),
            (key, "AutPlay session rotation v1\n"),
            (key, ENROLL_DOMAIN),
        ):
            response = client.post(
                PREFIX + "/sessions/rotate",
                content=signed(rotation, signing_key, domain),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 401
        raw = signed(rotation, key, ROTATE_DOMAIN)
        changed = raw.replace(
            b'"platform":"' + profile.platform.encode() + b'"',
            b'"platform":"' + (b"MACOS" if profile.platform == "ANDROID" else b"ANDROID") + b'"',
        )
        response = client.post(
            PREFIX + "/sessions/rotate",
            content=changed,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("change", ["epoch", "origin", "identity_key", "identity_thumbprint"])
def test_changed_m5_identity_cannot_replay_either_grant(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    change: str,
) -> None:
    original, binding, key, refresh = profile.enroll()
    rotation, _ = profile.rotation(binding, original, refresh)
    rotation_raw = signed(rotation, key, ROTATE_DOMAIN)
    profile.service.session_rotate(rotation_raw)
    if change == "epoch":
        database_connection.execute(
            "UPDATE account.server_instance SET identity_epoch=identity_epoch+1"
        )
    elif change == "origin":
        profile.identity._api = "https://changed-api.test.invalid"
    elif change == "identity_key":
        database_connection.execute(
            "UPDATE account.server_instance SET identity_public_key_spki=%s",
            (public_spki(ec.generate_private_key(ec.SECP256R1())),),
        )
    else:
        database_connection.execute(
            "UPDATE account.server_instance SET identity_thumbprint_sha256=%s", (b"t" * 32,)
        )
    database_connection.commit()
    before = snapshot(database_connection)
    with _client(profile) as client:
        for route, raw in (
            ("enrollment/exchanges", rfc8785.dumps(cast(JsonValue, original))),
            ("sessions/rotate", rotation_raw),
        ):
            response = client.post(
                PREFIX + "/" + route, content=raw, headers={"Content-Type": "application/json"}
            )
            assert response.status_code in {401, 503}
            assert "access_token" not in response.text
    assert snapshot(database_connection) == before


def test_v2_rotates_explicit_android_v1_parent_and_receipts_stay_versioned(
    profile: ProfileHarness,
    database_connection: Connection[Any],
) -> None:
    invitation = profile.invitation()
    legacy, key, refresh = _exchange_request(invitation, refresh=credential())
    binding, _ = profile.identity.exchange(legacy)
    v2_original = {**legacy, **_trust(invitation)}
    rotation, _ = profile.rotation(binding, v2_original, refresh)
    rotation["platform"] = "ANDROID"  # Explicit known row evidence, never a default.
    successor, _ = profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert successor["platform"] == "ANDROID"
    assert successor["family_id"] == binding["session_id"]
    legacy_rotation = _rotation_request(
        binding, key, refresh, rotation_id=UUID(rotation["rotation_id"])
    )
    before = snapshot(database_connection)
    with pytest.raises(ProfilePairingError, match="session_revoked"):
        profile.identity.rotate(legacy_rotation)
    assert snapshot(database_connection) == before


def test_v2_cannot_complete_a_v1_rotation_receipt(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    invitation = profile.invitation()
    legacy, key, refresh = _exchange_request(invitation, refresh=credential())
    binding, _ = profile.identity.exchange(legacy)
    old_rotation = _rotation_request(binding, key, refresh)
    profile.identity.rotate(old_rotation)
    rotation, _ = profile.rotation(binding, {**legacy, **_trust(invitation)}, refresh)
    rotation["platform"] = "ANDROID"
    rotation["rotation_id"] = old_rotation["rotation_id"]
    before = snapshot(database_connection)
    with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
        profile.service.session_rotate(signed(rotation, key, ROTATE_DOMAIN))
    assert snapshot(database_connection) == before


def _legacy_pair(profile: ProfileHarness, connection: Connection[Any]) -> PairingHarness:
    # The real self/recovery applications use current wall/DB time. Give their
    # Android source fresh authority too, independent of the historical D1 clock.
    now = datetime.now(UTC)
    profile.clock["now"] = now
    connection.execute(
        (
            "UPDATE account.user_session SET issued_at=%s,last_rotated_at=%s,expires_at=%"
            "s WHERE session_id=%s"
        ),
        (now, now, now + timedelta(days=90), profile.owner.session_id),
    )
    connection.commit()
    invitation = profile.invitation()
    source, _, _ = _exchange_request(invitation, refresh=credential())
    binding, replayed = profile.identity.exchange(source)
    assert replayed is False
    service = SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(profile.identity._sessions),
        profile.identity._access,
        profile.identity._access_ttl,
        b"d1-self-reservation-fixture-secret-32bytes",
    )
    return PairingHarness(
        profile.identity._sessions.kw["bind"],
        service,
        Principal(
            profile.owner.user_id,
            UUID(require_instance(binding["device_id"], str)),
            UUID(require_instance(binding["session_id"], str)),
            profile.owner.role,
        ),
        _trust(invitation),
        uuid4(),
        credential(),
        credential(),
        ec.generate_private_key(ec.SECP256R1()),
    )


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize("reserved_field", ["exchange_id", "binding_commit_id"])
@pytest.mark.parametrize("incoming_field", ["exchange_id", "binding_commit_id"])
def test_enrollment_identifier_collisions_in_both_orders_preserve_exact_replay(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    first: str,
    reserved_field: str,
    incoming_field: str,
) -> None:
    old, old_key, _ = _exchange_request(profile.invitation(), refresh=credential())
    new, new_key, _ = profile.enrollment()
    if first == "v2":
        raw = signed(new, new_key, ENROLL_DOMAIN)
        winner, _ = profile.service.enrollment_exchange(raw)
        old[incoming_field] = new[reserved_field]
        signed(old, old_key, "AutPlay enrollment exchange v1\n")
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
                profile.identity.exchange(old)
            assert snapshot(database_connection) == before
        replay, replayed = profile.service.enrollment_exchange(raw)
    else:
        winner, _ = profile.identity.exchange(old)
        new[incoming_field] = old[reserved_field]
        raw = signed(new, new_key, ENROLL_DOMAIN)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
                profile.service.enrollment_exchange(raw)
            assert snapshot(database_connection) == before
        replay, replayed = profile.identity.exchange(old)
    assert replayed is True and replay["session_id"] == winner["session_id"]
    after = snapshot(database_connection)
    if first == "v1":
        # V1 deliberately appends one replay audit fact. All authority, receipt
        # and prior audit rows remain exactly unchanged.
        assert after[:-1] == before[:-1]
        audit_before = require_instance(before[-1], list)
        audit_after = require_instance(after[-1], list)
        assert audit_after[:-1] == audit_before
        assert len(audit_after) == len(audit_before) + 1
        assert audit_after[-1][5:8] == (
            "profile.enrollment_exchange_replayed",
            "DEVICE",
            UUID(str(winner["device_id"])),
        )
    else:
        assert after == before


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize(
    "v2_operation,v2_field",
    [
        ("enrollment", "exchange_id"),
        ("enrollment", "binding_commit_id"),
        ("rotation", "rotation_id"),
    ],
)
@pytest.mark.parametrize("self_field", ["exchange_id", "binding_commit_id"])
def test_self_profile_reservations_cover_both_orders_and_replay_cannot_escape(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    first: str,
    v2_operation: str,
    v2_field: str,
    self_field: str,
) -> None:
    pair = _legacy_pair(profile, database_connection)
    _, exchange = pair.ready()
    if v2_operation == "enrollment":
        body, key, _ = profile.enrollment()
        action, domain = profile.service.enrollment_exchange, ENROLL_DOMAIN
    else:
        original, binding, key, refresh = profile.enroll()
        body, _ = profile.rotation(binding, original, refresh)
        action, domain = profile.service.session_rotate, ROTATE_DOMAIN
    if first == "v2":
        raw = signed(body, key, domain)
        winner, _ = action(raw)
        exchange[self_field] = body[v2_field]
        exchange = pair.request("exchange", exchange)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(SelfPairingError, match="operation_conflict"):
                pair.service.exchange(pair.poll_secret, exchange)
            assert snapshot(database_connection) == before
        replay, replayed = action(raw)
        assert replayed is True
    else:
        winner = pair.service.exchange(pair.poll_secret, exchange)
        body[v2_field] = exchange[self_field]
        raw = signed(body, key, domain)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
                action(raw)
            assert snapshot(database_connection) == before
        replay = pair.service.exchange(pair.poll_secret, exchange)
        assert replay["replayed"] is True
    assert replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize("recovery_field", ["operation_id", "binding_commit_id"])
@pytest.mark.parametrize("profile_field", ["exchange_id", "binding_commit_id"])
def test_recovery_binding_and_operation_reservations_are_bidirectional(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    first: str,
    recovery_field: str,
    profile_field: str,
) -> None:
    pair = _legacy_pair(profile, database_connection)
    recovery, code, commit = configured(pair)
    # A successful recovery revokes its account's invitations. Use another
    # authorized owner so a stale invitation cannot mask a binding collision.
    other_owner = _owner(database_connection)
    now = profile.clock["now"]
    database_connection.execute(
        (
            "UPDATE account.user_session SET issued_at=%s,last_rotated_at=%s,expires_at=%"
            "s WHERE session_id=%s"
        ),
        (now, now, now + timedelta(days=90), other_owner.session_id),
    )
    database_connection.commit()
    invitation = profile.identity.issue_invitation(other_owner, uuid4(), 600)
    body, key, _ = profile.enrollment(invitation=invitation)
    if first == "v2":
        raw = signed(body, key, ENROLL_DOMAIN)
        winner, _ = profile.service.enrollment_exchange(raw)
        commit = recovery_request(
            pair, "recover", **{**commit, recovery_field: body[profile_field]}
        )
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(AccountRecoveryError, match="operation_conflict"):
                recovery.recover(code, commit)
            assert snapshot(database_connection) == before
        replay, replayed = profile.service.enrollment_exchange(raw)
        assert replayed is True
    else:
        winner = recovery.recover(code, commit)
        body[profile_field] = commit[recovery_field]
        raw = signed(body, key, ENROLL_DOMAIN)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
                profile.service.enrollment_exchange(raw)
            assert snapshot(database_connection) == before
        replay = recovery.recover(code, commit)
        assert replay["replayed"] is True
    assert replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize("profile_field", ["exchange_id", "binding_commit_id"])
def test_legacy_rotation_and_v2_identifiers_cannot_cross_purposes(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    first: str,
    profile_field: str,
) -> None:
    old, old_key, refresh = _exchange_request(profile.invitation(), refresh=credential())
    old_binding, _ = profile.identity.exchange(old)
    rotation = _rotation_request(old_binding, old_key, refresh)
    body, key, _ = profile.enrollment()
    if first == "v2":
        raw = signed(body, key, ENROLL_DOMAIN)
        winner, _ = profile.service.enrollment_exchange(raw)
        rotation["rotation_id"] = body[profile_field]
        signed(rotation, old_key, "AutPlay session rotation v1\n")
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match="session_revoked"):
                profile.identity.rotate(rotation)
            assert snapshot(database_connection) == before
        replay, replayed = profile.service.enrollment_exchange(raw)
    else:
        winner, _ = profile.identity.rotate(rotation)
        body[profile_field] = rotation["rotation_id"]
        raw = signed(body, key, ENROLL_DOMAIN)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
                profile.service.enrollment_exchange(raw)
            assert snapshot(database_connection) == before
        replay, replayed = profile.identity.rotate(rotation)
    assert replayed is True and replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


def _legacy_admission_binding(
    profile: ProfileHarness,
    connection: Connection[Any],
    issuer: str,
) -> tuple[
    dict[str, Any],
    ec.EllipticCurvePrivateKey,
    Callable[[dict[str, object]], tuple[dict[str, object], bool]],
    str,
]:
    runtime = AdmissionRuntime(
        profile.identity, profile.identity._sessions, AdmissionClock(profile.clock["now"])
    )
    request, created, key, request_hash = _admission(runtime)
    actor = _web_actor(runtime, connection, user_id=profile.owner.user_id)
    _bind_and_decide(runtime, actor, created, "TRUST_DEVICE")
    thumb = request["device_key_thumbprint_sha256"]
    body: dict[str, Any] = {
        "exchange_id": str(uuid4()),
        "binding_commit_id": str(uuid4()),
        "device_key_thumbprint_sha256": thumb,
        "device_public_key_jwk": _jwk(key),
        "next_refresh_token_sha256": hashlib.sha256(credential().encode()).hexdigest(),
        "client_nonce_b64url": _nonce(90),
        **{name: request[name] for name in _TRUST_NAMES[:3]},
        "expected_api_origin": profile.identity._api,
        "expected_stream_origin": profile.identity._stream,
    }
    exchange = admission_signed(
        key,
        ADMISSION_DOMAIN,
        {
            **body,
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "approved_account_id": str(actor.user_id),
            "poll_bearer_sha256": hashlib.sha256(str(created["poll_bearer"]).encode()).hexdigest(),
        },
    )
    if issuer == "admission":
        return (
            exchange,
            key,
            lambda document: profile.identity.exchange_device_admission(
                document,
                str(created["poll_bearer"]),
            ),
            ADMISSION_DOMAIN,
        )
    # Establish real approved ANDROID trust before requesting the actual challenge.
    profile.identity.exchange_device_admission(exchange, str(created["poll_bearer"]))
    challenge = profile.identity.issue_trusted_reenrollment_challenge(
        principal=profile.owner,
        thumbprint=bytes.fromhex(str(thumb)),
        challenge_id=uuid4(),
        request_sha256=b"r" * 32,
        client_nonce_sha256=b"n" * 32,
    )
    trusted = admission_signed(
        key,
        TRUSTED_REENROLLMENT_DOMAIN,
        {
            **body,
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "next_refresh_token_sha256": hashlib.sha256(credential().encode()).hexdigest(),
            "account_id": str(actor.user_id),
            "challenge_id": challenge["challenge_id"],
            "challenge": challenge["challenge"],
        },
    )
    return trusted, key, profile.identity.complete_trusted_reenrollment, TRUSTED_REENROLLMENT_DOMAIN


@pytest.mark.parametrize("issuer", ["admission", "trusted"])
@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize("reserved_field", ["exchange_id", "binding_commit_id"])
@pytest.mark.parametrize("incoming_field", ["exchange_id", "binding_commit_id"])
def test_admission_and_trusted_profile_id_collisions_preserve_own_replays(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    issuer: str,
    first: str,
    reserved_field: str,
    incoming_field: str,
) -> None:
    legacy, legacy_key, action, domain = _legacy_admission_binding(
        profile, database_connection, issuer
    )
    body, key, _ = profile.enrollment()
    if first == "v2":
        raw = signed(body, key, ENROLL_DOMAIN)
        winner, _ = profile.service.enrollment_exchange(raw)
        legacy[incoming_field] = body[reserved_field]
        legacy.pop("proof_b64url")
        legacy = admission_signed(legacy_key, domain, legacy)
        before = snapshot(database_connection)
        error = (
            "admission_request_unavailable" if issuer == "admission" else "trusted_key_unavailable"
        )
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match=error):
                action(legacy)
            assert snapshot(database_connection) == before
        replay, replayed = profile.service.enrollment_exchange(raw)
    else:
        winner, _ = action(legacy)
        body[incoming_field] = legacy[reserved_field]
        raw = signed(body, key, ENROLL_DOMAIN)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
                profile.service.enrollment_exchange(raw)
            assert snapshot(database_connection) == before
        replay, replayed = action(legacy)
    assert replayed is True and replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("issuer", ["profile", "self"])
@pytest.mark.parametrize(
    "first,portable_field",
    [
        ("portable", "challenge_id"),
        ("portable", "binding_commit_id"),
        ("other", "binding_commit_id"),
    ],
)
@pytest.mark.parametrize("incoming_field", ["exchange_id", "binding_commit_id"])
def test_portable_v2_reservations_cross_profile_and_self_in_both_orders(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    issuer: str,
    first: str,
    portable_field: str,
    incoming_field: str,
) -> None:
    # Import the owning portable fixture helper only when this future test runs;
    # its module reuses signed() above, so a collection-time import would cycle.
    from .test_device_portable_v2 import PortableHarness, original
    from .test_portable_accounts import passport

    pair = _legacy_pair(profile, database_connection)
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    legacy = PortableAccountService(
        profile.identity._sessions,
        profile.identity._access,
        profile.identity._access_ttl,
        b"d1-portable-reservation-fixture-secret-32bytes",
    )
    service = DevicePortableServiceV2(
        legacy,
        identity=profile.identity,
        enabled=lambda: True,
        portable_enabled=lambda: True,
    )
    portable = PortableHarness(
        service,
        legacy,
        profile.identity,
        profile.platform,
        {"v2": True, "portable": True},
        profile.clock,
        root,
        device,
        passport(root),
    )
    portable_body, _, _ = portable.command()
    if issuer == "self":
        _, body = pair.ready()

        def encode_self() -> dict[str, Any]:
            return pair.request("exchange", body)

        def attempt_self(document: object) -> tuple[dict[str, object], bool]:
            result = pair.service.exchange(pair.poll_secret, require_instance(document, dict))
            return result, result["replayed"]

        encode: Callable[[], object] = encode_self
        attempt: Callable[[object], tuple[dict[str, object], bool]] = attempt_self
    else:
        body, key, _ = profile.enrollment()

        def encode_enrollment() -> bytes:
            return signed(body, key, ENROLL_DOMAIN)

        encode = encode_enrollment

        def attempt_enrollment(document: object) -> tuple[dict[str, object], bool]:
            return profile.service.enrollment_exchange(require_instance(document, bytes))

        attempt = attempt_enrollment
    if first == "portable":
        raw = original(root, portable_body)
        winner = portable.service.register(raw)
        identifier = (
            portable_body["challenge"]["challenge_id"]
            if portable_field == "challenge_id"
            else portable_body["binding_commit_id"]
        )
        body[incoming_field] = identifier
        document = encode()
        before = snapshot(database_connection)
        error_type = SelfPairingError if issuer == "self" else DeviceCompatibilityError
        for _ in range(2):
            with pytest.raises(error_type, match="operation_conflict"):
                attempt(document)
            assert snapshot(database_connection) == before
        replay = portable.service.register(raw)
        assert replay == winner
    else:
        document = encode()
        winner, _ = attempt(document)
        portable_body["binding_commit_id"] = body[incoming_field]
        raw = original(root, portable_body)
        before = snapshot(database_connection)
        for _ in range(2):
            with pytest.raises(ProfilePairingError, match="operation_conflict"):
                portable.service.register(raw)
            assert snapshot(database_connection) == before
        replay, replayed = attempt(document)
        assert replayed is True
        assert replay["session_id"] == winner["session_id"]
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("reserved_field", ["exchange_id", "binding_commit_id"])
def test_legacy_portable_binding_refuses_profile_v2_reservation_before_grant(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    reserved_field: str,
) -> None:
    from .test_portable_accounts import binding_body, passport, proof

    owner_body, owner_result, _, _ = profile.enroll()
    owner_original = rfc8785.dumps(cast(JsonValue, owner_body))
    legacy = PortableAccountService(
        profile.identity._sessions,
        profile.identity._access,
        profile.identity._access_ttl,
        b"d1-legacy-portable-reservation-fixture-secret-32bytes",
    )
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    command = binding_body(
        legacy.challenge("REGISTER", "legacy-collision", document=passport(root)), device
    )
    command["binding_commit_id"] = owner_body[reserved_field]
    signed_original = proof(root, command)
    before = snapshot(database_connection)
    for _ in range(2):
        with pytest.raises(ProfilePairingError, match="operation_conflict"):
            legacy.complete(command, signed_original)
        assert snapshot(database_connection) == before
    # The same valid Android v1 command succeeds after changing only its ID and
    # regenerating its own v1 proof. No Mac platform or unrelated proof refusal
    # can satisfy the collision assertion above.
    allowed = {**command, "binding_commit_id": str(uuid4())}
    allowed_proof = proof(root, allowed)
    granted = legacy.complete(allowed, allowed_proof)
    settled = snapshot(database_connection)
    assert legacy.complete(allowed, allowed_proof) == granted
    replay, replayed = profile.service.enrollment_exchange(owner_original)
    assert replayed is True and replay["session_id"] == owner_result["session_id"]
    assert snapshot(database_connection) == settled


@pytest.mark.parametrize("purpose", ["REGISTER", "LOGIN"])
@pytest.mark.parametrize("stage", ["first_bind", "active_receipt", "expired_with_successor"])
@pytest.mark.parametrize(
    "kind,incoming_field",
    [
        ("enrollment_operation", "binding_commit_id"),
        ("enrollment_binding", "binding_commit_id"),
        ("rotation", "binding_commit_id"),
        ("portable_challenge", "binding_commit_id"),
        ("portable_binding", "binding_commit_id"),
        ("enrollment_operation", "challenge_id"),
        ("enrollment_binding", "challenge_id"),
        ("rotation", "challenge_id"),
        ("portable_binding", "challenge_id"),
    ],
)
def test_legacy_portable_reservations_precede_grants_and_expiry_retirement(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    stage: str,
    kind: str,
    incoming_field: str,
) -> None:
    from autplay.adapters.portable_account_crypto import verify_account_proof
    from autplay.adapters.postgresql.models import PortableAccountChallengeRow
    from autplay.application.portable_account import _database_now as actual_database_now
    from sqlalchemy.orm import Session

    from .test_portable_accounts import binding_body, passport, proof

    _legacy_pair(profile, database_connection)

    def final_clock(session: Session) -> datetime:
        assert actual_database_now(session).utcoffset() == timedelta(0)
        return profile.clock["now"]

    monkeypatch.setattr("autplay.application.portable_account._database_now", final_clock)
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    document = {**passport(root), "public_id": "d1_legacy_receipt"}
    legacy = PortableAccountService(
        profile.identity._sessions,
        profile.identity._access,
        profile.identity._access_ttl,
        b"d1-legacy-expiry-reservation-fixture-secret-32bytes",
    )
    if purpose == "LOGIN":
        registration = binding_body(
            legacy.challenge("REGISTER", "legacy-receipt", document=document),
            ec.generate_private_key(ec.SECP256R1()),
        )
        legacy.complete(registration, proof(root, registration))
        challenge = legacy.challenge("LOGIN", "legacy-receipt", login=document["public_id"])
    else:
        challenge = legacy.challenge("REGISTER", "legacy-receipt", document=document)
    command = binding_body(challenge, device)
    refresh = credential()
    command["next_refresh_token_sha256"] = hashlib.sha256(refresh.encode("ascii")).hexdigest()
    original_command = {**command, "challenge": dict(command["challenge"])}
    granted: dict[str, Any] | None = None
    successor: dict[str, Any] | None = None
    if stage != "first_bind":
        granted = legacy.complete(command, proof(root, command))
    if stage == "expired_with_successor":
        assert granted is not None
        # Exercise a real rotation and its actual successor family. Keep receipt
        # timestamps intact; advancing the fixture clock supplies genuine expiry.
        binding = {
            **granted,
            "server_instance_id": original_command["challenge"]["server_instance_id"],
        }
        rotation = _rotation_request(binding, device, refresh)
        successor, replayed = profile.identity.rotate(rotation)
        assert replayed is False and successor["session_id"] != granted["session_id"]
        profile.clock["now"] = datetime.fromisoformat(granted["access_expires_at"]) + timedelta(
            seconds=1
        )

    identifier, v2_attempt = _v2_reservation_candidate(
        profile, database_connection, monkeypatch, kind
    )
    v2_winner = v2_attempt()
    old_challenge_id = UUID(command["challenge"]["challenge_id"])
    if incoming_field == "challenge_id":
        command["challenge"] = {**command["challenge"], "challenge_id": identifier}
    else:
        command["binding_commit_id"] = identifier
    signed_original = proof(root, command)
    digest = hashlib.sha256(rfc8785.dumps(cast(JsonValue, command))).digest()
    assert (
        verify_account_proof(public_spki(root), purpose.lower(), command, signed_original) == digest
    )
    with legacy.sessions.begin() as session:
        row = session.get(PortableAccountChallengeRow, old_challenge_id, with_for_update=True)
        assert row is not None and row.contract_version == "v1"
        # Only historical LEGACY evidence is seeded on this disposable future
        # fixture. Its matching original digest/proof cannot mask the guard.
        # A v2 challenge PK cannot also identify a v1 row, so that impossible
        # combination is excluded above; the ordinary v2-row refusal stays intact.
        row.challenge_id = UUID(command["challenge"]["challenge_id"])
        row.document = dict(command["challenge"])
        if row.receipt is not None:
            row.request_hash = digest
            row.receipt = {**row.receipt, "binding_commit_id": command["binding_commit_id"]}
            assert row.receipt_expires_at is not None
            assert (row.receipt_expires_at <= profile.clock["now"]) is (
                stage == "expired_with_successor"
            )

    if successor is not None:
        live = database_connection.execute(
            "SELECT family_id,revoked_at,expires_at FROM account.user_session WHERE session_id=%s",
            (UUID(successor["session_id"]),),
        ).fetchone()
        assert granted is not None and live is not None
        assert (
            live[0] == UUID(granted["session_id"])
            and live[1] is None
            and live[2] > profile.clock["now"]
        )
        database_connection.commit()
    before = snapshot(database_connection)
    for _ in range(2):
        with pytest.raises(ProfilePairingError, match="operation_conflict"):
            legacy.complete(command, signed_original)
        # Includes account/root, devices, every session (especially the live
        # successor), challenge/receipt and authority rows. Rate admission is
        # intentionally outside this snapshot because each attempt is committed.
        assert snapshot(database_connection) == before

    # Restore only the synthetic v1 evidence to its independently signed
    # original; no v2 evidence/immutability is disabled, erased or rewritten.
    allowed_proof = proof(root, original_command)
    with legacy.sessions.begin() as session:
        row = session.get(
            PortableAccountChallengeRow,
            UUID(command["challenge"]["challenge_id"]),
            with_for_update=True,
        )
        assert row is not None and row.contract_version == "v1"
        row.challenge_id = UUID(original_command["challenge"]["challenge_id"])
        row.document = dict(original_command["challenge"])
        if row.receipt is not None:
            row.request_hash = hashlib.sha256(
                rfc8785.dumps(cast(JsonValue, original_command))
            ).digest()
            row.receipt = {
                **row.receipt,
                "binding_commit_id": original_command["binding_commit_id"],
            }
    settled = snapshot(database_connection)
    assert v2_attempt()["session_id"] == v2_winner["session_id"]
    assert snapshot(database_connection) == settled
    if stage == "expired_with_successor":
        with pytest.raises(ProfilePairingError, match="account_receipt_expired"):
            legacy.complete(original_command, allowed_proof)
        assert successor is not None
        retired = database_connection.execute(
            "SELECT revoked_at FROM account.user_session WHERE session_id=%s",
            (UUID(successor["session_id"]),),
        ).fetchone()
        assert retired is not None and retired[0] is not None
        database_connection.commit()
    else:
        allowed = legacy.complete(original_command, allowed_proof)
        if granted is not None:
            assert allowed == granted
        assert legacy.complete(original_command, allowed_proof) == allowed


def _v2_reservation_candidate(
    profile: ProfileHarness,
    connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    identifier: str | None = None,
) -> tuple[str, Callable[[], dict[str, Any]]]:
    """Prepare a real issuer; only attempt() may claim the selected reservation."""
    if kind in {"enrollment_operation", "enrollment_binding", "rotation"}:
        # Lifecycle/deletion/recovery on the legacy account cannot invalidate
        # the unrelated v2 issuer and mask an identifier refusal.
        owner = _owner(connection)
        now = profile.clock["now"]
        connection.execute(
            (
                "UPDATE account.user_session SET issued_at=%s,last_rotated_at=%s,expires_at=%"
                "s WHERE session_id=%s"
            ),
            (now, now, now + timedelta(days=90), owner.session_id),
        )
        connection.commit()
        invitation = profile.identity.issue_invitation(owner, uuid4(), 600)
        body, key, refresh = profile.enrollment(invitation=invitation)
        action, domain, field = profile.service.enrollment_exchange, ENROLL_DOMAIN, "exchange_id"
        if kind == "rotation":
            binding, _ = action(signed(body, key, domain))
            body, _ = profile.rotation(binding, body, refresh)
            action, domain, field = profile.service.session_rotate, ROTATE_DOMAIN, "rotation_id"
        elif kind == "enrollment_binding":
            field = "binding_commit_id"
        body[field] = identifier or body[field]
        raw = signed(body, key, domain)
        return body[field], lambda: action(raw)[0]

    from .test_device_portable_v2 import PortableHarness, original
    from .test_portable_accounts import passport

    monkeypatch.setattr(
        "autplay.application.device_portable_service_v2._now", lambda: profile.clock["now"]
    )
    root = ec.generate_private_key(ec.SECP256R1())
    document = {**passport(root), "public_id": "d1_v2_reservation"}
    legacy = PortableAccountService(
        profile.identity._sessions,
        profile.identity._access,
        profile.identity._access_ttl,
        b"d1-v2-reservation-candidate-secret-32bytes",
    )
    portable = PortableHarness(
        DevicePortableServiceV2(
            legacy, identity=profile.identity, enabled=lambda: True, portable_enabled=lambda: True
        ),
        legacy,
        profile.identity,
        profile.platform,
        {"v2": True, "portable": True},
        profile.clock,
        root,
        ec.generate_private_key(ec.SECP256R1()),
        document,
    )
    if kind == "portable_binding":
        body, _, _ = portable.command()
        body["binding_commit_id"] = identifier or body["binding_commit_id"]
        raw = original(root, body)
        return body["binding_commit_id"], lambda: portable.service.register(raw)
    assert kind == "portable_challenge"
    identifier = identifier or str(uuid4())
    originals: list[bytes] = []

    def attempt() -> dict[str, Any]:
        if not originals:
            # Force only the future challenge generator; restore before device,
            # session and token IDs are allocated by the real complete path.
            with monkeypatch.context() as patch:
                patch.setattr(
                    "autplay.application.device_portable_service_v2.uuid4", lambda: UUID(identifier)
                )
                _, raw, _ = portable.command()
            originals.append(raw)
        return portable.service.register(originals[0])

    return identifier, attempt


def _legacy_reservation_writer(
    profile: ProfileHarness,
    connection: Connection[Any],
    writer: str,
    tmp_path: Path,
    identifier: str | None = None,
) -> tuple[str, Callable[[], dict[str, Any]], Callable[[], object]]:
    """All positive controls use live ANDROID authority and complete v1 scopes."""
    from autplay.domain.account_recovery import new_code

    from .test_account_recovery import service as recovery_service
    from .test_account_recovery import verifier

    pair = _legacy_pair(profile, connection)
    identifier = identifier or str(uuid4())
    operation_id = UUID(identifier)

    def observe() -> object:
        return ()

    if writer == "self_ceremony":
        document = pair.request("start", {**pair.start(), "ceremony_id": identifier})
        return identifier, lambda: pair.service.start(pair.actor, document), observe
    if writer.startswith("self_decision_"):
        pair.service.start(pair.actor, pair.start())
        claimed = pair.service.claim(pair.rendezvous, pair.claim())
        action = writer.removeprefix("self_decision_")
        document = pair.request(
            "decision",
            {
                **pair.approve(claimed),
                "operation_id": identifier,
                "action": action,
                "comparison_code": claimed["comparison_code"] if action == "APPROVE" else None,
            },
        )
        return identifier, lambda: pair.service.decide(pair.actor, document), observe
    if writer == "configure":
        recovery = recovery_service(pair)
        document = recovery_request(
            pair,
            "configure",
            operation_id=identifier,
            expected_code_generation=0,
            next_code_verifier_sha256=verifier(pair, new_code()),
        )
        return identifier, lambda: recovery.configure(pair.actor, document), observe
    if writer == "lifecycle_cancel":
        invitation = profile.identity.issue_invitation(pair.actor, uuid4(), 600)
        return (
            identifier,
            lambda: profile.identity.cancel_invitation(
                pair.actor,
                UUID(require_instance(invitation["invitation_id"], str)),
                operation_id,
            ),
            observe,
        )
    if writer == "lifecycle_logout_current":
        return (
            identifier,
            lambda: profile.identity.logout_current(pair.actor, operation_id),
            observe,
        )
    if writer == "lifecycle_logout_all":
        return identifier, lambda: profile.identity.logout_all(pair.actor, operation_id), observe
    if writer == "lifecycle_revoke":
        return (
            identifier,
            lambda: profile.identity.revoke_device(pair.actor, pair.actor.device_id, operation_id),
            observe,
        )
    if writer.startswith("web_"):
        runtime = AdmissionRuntime(
            profile.identity, profile.identity._sessions, AdmissionClock(profile.clock["now"])
        )
        actor = _web_actor(runtime, connection, user_id=pair.actor.user_id)
        if writer == "web_developer":
            return (
                identifier,
                lambda: profile.identity.manage_device_developer_mode(
                    principal=actor,
                    device_id=pair.actor.device_id,
                    enabled=True,
                    operation_id=operation_id,
                    request_sha256=b"d" * 32,
                ),
                observe,
            )
        request, created, _, _ = _admission(runtime)
        if writer == "web_review":
            return (
                identifier,
                lambda: profile.identity.bind_device_admission_review(
                    actor=actor,
                    web_session_id=actor.web_session_id,
                    locator=str(created["review_locator"]),
                    operation_id=operation_id,
                    request_sha256=b"r" * 32,
                ),
                observe,
            )
        if writer == "web_decision":
            profile.identity.bind_device_admission_review(
                actor=actor,
                web_session_id=actor.web_session_id,
                locator=str(created["review_locator"]),
                operation_id=uuid4(),
                request_sha256=b"r" * 32,
            )
            return (
                identifier,
                lambda: profile.identity.decide_device_admission(
                    actor,
                    UUID(str(created["request_id"])),
                    "TRUST_DEVICE",
                    operation_id,
                    b"d" * 32,
                    web_session_id=actor.web_session_id,
                ),
                observe,
            )
        assert writer == "web_trust"
        _bind_and_decide(runtime, actor, created, "TRUST_DEVICE")
        return (
            identifier,
            lambda: profile.identity.manage_trusted_key(
                principal=actor,
                thumbprint=bytes.fromhex(str(request["device_key_thumbprint_sha256"])),
                action="REMOVE_TRUST",
                operation_id=operation_id,
                request_sha256=b"t" * 32,
            ),
            observe,
        )

    from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger

    from .test_account_deletion import PairingHarness as DeletionPair
    from .test_account_deletion import cancel_request, prepared
    from .test_account_deletion import request as deletion_request

    assert writer in {"deletion_request", "deletion_cancel_operation", "deletion_cancel_binding"}
    # Preserve the last-owner rule, using only future disposable fixture data.
    _owner(connection)
    ledger = FilesystemDeletionLedger(
        tmp_path / "reservation-deletion.sqlite3", b"d" * 32, "fixture-v1"
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=2))
    deletion_pair = DeletionPair(**vars(pair), deletion_ledger=ledger)
    deletion, code, key, document = prepared(deletion_pair)

    def observe_deletion() -> tuple[DeletionRequestEvidence, ...]:
        return tuple(ledger.request_read())

    if writer == "deletion_request":
        document = deletion_request(
            deletion_pair, "request", key, **{**document, "operation_id": identifier}
        )
        accepted = False

        def attempt() -> dict[str, Any]:
            nonlocal accepted
            if accepted:
                return deletion.request_receipt(code, document)
            result = deletion.request(pair.actor, code, document)
            accepted = True
            return result

        return identifier, attempt, observe_deletion
    pending = deletion.request(pair.actor, code, document)
    field = "binding_commit_id" if writer == "deletion_cancel_binding" else "operation_id"
    cancellation = cancel_request(deletion_pair, pending)
    cancellation = deletion_request(
        deletion_pair,
        "cancel",
        pair.key,
        **{**cancellation, field: identifier},
    )
    return identifier, lambda: deletion.cancel(code, cancellation), observe_deletion


@pytest.mark.parametrize(
    "kind",
    [
        "enrollment_operation",
        "enrollment_binding",
        "rotation",
        "portable_challenge",
        "portable_binding",
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
def test_legacy_reservation_writers_cannot_poison_v2_winning_replay(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    writer: str,
    first: str,
) -> None:
    from autplay.domain.account_deletion import AccountDeletionError

    # Align every application clock with live DB authority before preparing v2.
    _legacy_pair(profile, database_connection)
    if first == "v2":
        identifier, v2_attempt = _v2_reservation_candidate(
            profile, database_connection, monkeypatch, kind
        )
        winner = v2_attempt()
        _, legacy_attempt, observe = _legacy_reservation_writer(
            profile,
            database_connection,
            writer,
            tmp_path,
            identifier,
        )
        before, ledger_before = snapshot(database_connection), observe()
        for _ in range(2):
            with pytest.raises(
                (ProfilePairingError, SelfPairingError, AccountRecoveryError, AccountDeletionError),
                match="operation_conflict",
            ):
                legacy_attempt()
            assert snapshot(database_connection) == before and observe() == ledger_before
        replay = v2_attempt()
        assert replay["session_id"] == winner["session_id"]
    else:
        identifier, legacy_attempt, observe = _legacy_reservation_writer(
            profile,
            database_connection,
            writer,
            tmp_path,
        )
        winner = legacy_attempt()
        _, v2_attempt = _v2_reservation_candidate(
            profile, database_connection, monkeypatch, kind, identifier
        )
        before, ledger_before = snapshot(database_connection), observe()
        for _ in range(2):
            with pytest.raises(
                (ProfilePairingError, DeviceCompatibilityError), match="operation_conflict"
            ):
                v2_attempt()
            assert snapshot(database_connection) == before and observe() == ledger_before
        replay = legacy_attempt()
        if "session_id" in winner:
            assert replay["session_id"] == winner["session_id"]
        assert replay.get("operation_id", identifier) == winner.get("operation_id", identifier)
    assert snapshot(database_connection) == before and observe() == ledger_before

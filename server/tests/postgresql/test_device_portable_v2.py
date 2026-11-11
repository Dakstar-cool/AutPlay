"""UNEXECUTED D1 portable persistence/HTTP fixtures on guarded disposable DBs.

All key generation, signatures, digest assertions, migrations and concurrency
below are future test actions. This source is not execution/qualification evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
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
from autplay.adapters.portable_account_crypto_v2 import PortableAccountV2Error
from autplay.adapters.postgresql.models import (
    DeviceKeyBlockRow,
    DeviceRow,
    PortableAccountChallengeRow,
    PortableAccountRow,
    PublicIdRegistrationRow,
    SelfPairingRateRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.retained_deletion_reservations import (
    configure_retained_deletion_reservations,
)
from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
from autplay.application.device_profile_service_v2 import DeviceProfileServiceV2
from autplay.application.portable_account import (
    PortableAccountService,
    portable_v2_identifier_reserved,
)
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.canonical_json import JsonValue
from autplay.domain.device_compatibility import DeviceCompatibilityError, NegotiationPolicy
from autplay.domain.profile_pairing import ProfilePairingError, public_key_thumbprint, public_spki
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints.device_portable_v2_http import create_device_portable_v2_router
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import Connection
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness
from .test_device_profile_v2 import ROTATE_DOMAIN, signed
from .test_portable_accounts import binding_body, passport, proof, services

PREFIX = "/api/v1/device-compatibility/v2/portable"


def original(
    root: ec.EllipticCurvePrivateKey, body: dict[str, Any], *, version: str = "v2"
) -> bytes:
    domain = f"autplay:portable-account:{body['challenge']['purpose'].lower()}:{version}\n".encode(
        "ascii"
    )
    signature = root.sign(domain + rfc8785.dumps(cast(JsonValue, body)), ec.ECDSA(hashes.SHA256()))
    return rfc8785.dumps(
        cast(JsonValue, {"request": body, "proof": base64.b64encode(signature).decode("ascii")})
    )


@dataclass
class PortableHarness:
    service: DevicePortableServiceV2
    legacy: PortableAccountService
    identity: ProfilePairingService
    platform: str
    gate: dict[str, bool]
    clock: dict[str, datetime]
    root: ec.EllipticCurvePrivateKey
    device: ec.EllipticCurvePrivateKey
    document: dict[str, Any]

    def command(
        self,
        purpose: str = "REGISTER",
        *,
        device: ec.EllipticCurvePrivateKey | None = None,
    ) -> tuple[dict[str, Any], bytes, str]:
        payload = {"contract_version": "v2", "schema_version": 2, "platform": self.platform}
        if purpose == "REGISTER":
            challenge = self.service.register_challenge(
                rfc8785.dumps(cast(JsonValue, {**payload, "account": self.document})),
                source="fixture-source",
            )
        else:
            challenge = self.service.login_challenge(
                rfc8785.dumps(
                    cast(JsonValue, {**payload, "public_id": self.document["public_id"]})
                ),
                source="fixture-source",
            )
        refresh = secrets.token_urlsafe(32)
        body = {
            **challenge,
            "device_public_key_spki_b64": base64.b64encode(
                public_spki(device or self.device)
            ).decode(),
            "device_name": "D1 portable fixture",
            "app_version": "d1-fixture",
            "next_refresh_token_sha256": hashlib.sha256(refresh.encode("ascii")).hexdigest(),
            "binding_commit_id": str(uuid4()),
        }
        return body, original(self.root, body), refresh

    def register(self) -> tuple[dict[str, Any], bytes, str, dict[str, object]]:
        body, raw, refresh = self.command()
        return body, raw, refresh, self.service.register(raw, source="fixture-source")

    def client(self) -> TestClient:
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(
            create_device_portable_v2_router(
                self.service,
                canonical_source=lambda request: "fixture-source",
            ),
            prefix="/api/v1",
        )
        return TestClient(app)


@pytest.fixture(params=["ANDROID", "MACOS"])
def portable(
    request: pytest.FixtureRequest,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Any:
    engine, legacy, identity = services(database_url, tmp_path)
    ledger = FilesystemDeletionLedger(
        tmp_path / "portable-reservations.sqlite3",
        b"d1-portable-ledger-test-secret-32bytes",
        "fixture-v1",
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=90))
    configure_retained_deletion_reservations(
        legacy.sessions, ledger=ledger, evidence_ready=lambda: True
    )
    from .controlled_clock import control_database_clock

    clock = {"now": datetime.now(UTC).replace(microsecond=0)}
    control_database_clock(
        monkeypatch,
        lambda: clock["now"],
        "autplay.application.profile_pairing",
        "autplay.application.portable_account",
        "autplay.application.device_profile_service_v2",
        "autplay.application.device_portable_service_v2",
    )
    gate = {"v2": True, "portable": True}
    monkeypatch.setattr("autplay.application.device_portable_service_v2._now", lambda: clock["now"])
    monkeypatch.setattr("autplay.application.device_profile_service_v2._now", lambda: clock["now"])
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    service = DevicePortableServiceV2(
        legacy,
        identity=identity,
        enabled=lambda: gate["v2"],
        portable_enabled=lambda: gate["portable"],
    )
    try:
        yield PortableHarness(
            service, legacy, identity, request.param, gate, clock, root, device, passport(root)
        )
    finally:
        engine.dispose()


def binding_snapshot(portable: PortableHarness) -> tuple[object, ...]:
    with portable.legacy.sessions() as s:
        return tuple(
            s.execute(text(statement)).all()
            for statement in (
                (
                    "SELECT user_id,role,status,authority_generation FROM account.user_account "
                    "ORDER BY user_id"
                ),
                "SELECT device_id,user_id,platform,public_key,public_key_thumbpr"
                "int_sha256,revoked_at "
                "FROM account.device ORDER BY device_id",
                "SELECT session_id,user_id,device_id,family_id,generation,refres"
                "h_token_hash,issued_at,expires_at,revoked_at "
                "FROM account.user_session ORDER BY session_id",
                "SELECT challenge_id,request_hash,receipt,receipt_expires_at,ori"
                "ginal_document_sha256,root_proof_sha256,"
                "original_request_evidence FROM account.portable_challenge ORDER BY challenge_id",
            )
        )


def test_real_http_register_login_hash_only_storage_and_lost_reply(
    portable: PortableHarness,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)
    body, raw, _ = portable.command()
    with portable.client() as client:
        challenge_reply = client.post(
            PREFIX + "/register-challenge",
            json={
                "contract_version": "v2",
                "schema_version": 2,
                "platform": portable.platform,
                "account": portable.document,
            },
        )
        assert challenge_reply.status_code == 200
        body = {**body, **challenge_reply.json()}
        raw = original(portable.root, body)
        first = client.post(
            PREFIX + "/register", content=raw, headers={"Content-Type": "application/json"}
        )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["platform"] == portable.platform
        assert (
            first.headers["Cache-Control"] == "no-store" and first.headers["Pragma"] == "no-cache"
        )
        before = binding_snapshot(portable)
        replay = client.post(
            PREFIX + "/register", content=raw, headers={"Content-Type": "application/json"}
        )
        assert replay.status_code == 200 and replay.json() == first_body
        assert binding_snapshot(portable) == before
        assert set(first_body) == {
            "contract_version",
            "schema_version",
            "platform",
            "account_id",
            "device_id",
            "session_id",
            "session_family_id",
            "generation",
            "binding_commit_id",
            "issued_at",
            "access_expires_at",
            "refresh_expires_at",
            "access_token",
        }
        challenge = client.post(
            PREFIX + "/login-challenge",
            json={
                "contract_version": "v2",
                "schema_version": 2,
                "platform": portable.platform,
                "public_id": portable.document["public_id"],
            },
        )
        assert challenge.status_code == 200
        login = {
            **body,
            **challenge.json(),
            "binding_commit_id": str(uuid4()),
            "next_refresh_token_sha256": hashlib.sha256(uuid4().bytes).hexdigest(),
        }
        logged = client.post(
            PREFIX + "/login",
            content=original(portable.root, login),
            headers={"Content-Type": "application/json"},
        )
        assert logged.status_code == 200 and logged.json()["device_id"] == first_body["device_id"]
        assert logged.json()["session_id"] != first_body["session_id"]
        assert (
            client.post(
                PREFIX + "/register", content=raw, headers={"Content-Type": "application/json"}
            ).status_code
            == 401
        )
    outer = json.loads(raw)
    der = base64.b64decode(outer["proof"], validate=True)
    with portable.legacy.sessions() as s:
        row = s.get(PortableAccountChallengeRow, UUID(body["challenge"]["challenge_id"]))
        assert (
            row is not None and row.contract_version == "v2" and row.platform == portable.platform
        )
        assert row.root_proof_sha256 == hashlib.sha256(der).digest()
        assert (
            row.original_document_sha256
            == hashlib.sha256(rfc8785.dumps(cast(JsonValue, outer))).digest()
        )
        assert row.request_hash == hashlib.sha256(rfc8785.dumps(cast(JsonValue, body))).digest()
        stored = json.dumps([row.document, row.original_request_evidence, row.receipt])
        assert outer["proof"] not in stored and der.hex() not in stored
        assert "proof_b64" not in stored and '"request"' not in stored and '"account"' not in stored
        assert "access_token" not in stored and first_body["access_token"] not in stored
        assert row.expires_at - row.created_at == timedelta(seconds=120)
        root = s.get(PortableAccountRow, UUID(portable.document["account_id"]))
        assert root is not None and root.document == portable.document
    assert outer["proof"] not in caplog.text and first_body["access_token"] not in caplog.text
    assert NegotiationPolicy(enabled=True, portable_enabled=True).families() == []


@pytest.mark.parametrize("disabled", ["v2", "portable"])
def test_flag_off_preserves_exact_old_ttl_but_refuses_first_commits(
    portable: PortableHarness,
    disabled: str,
) -> None:
    _, raw, _, first = portable.register()
    _, pending, _ = portable.command("LOGIN", device=ec.generate_private_key(ec.SECP256R1()))
    portable.gate[disabled] = False
    portable.legacy.access_ttl = timedelta(seconds=60)
    before = binding_snapshot(portable)
    assert portable.service.register(raw) == first
    assert binding_snapshot(portable) == before
    with pytest.raises(DeviceCompatibilityError, match="device_contract_unavailable"):
        portable.service.login(pending)
    for method, member in (
        (portable.service.register_challenge, {"account": portable.document}),
        (portable.service.login_challenge, {"public_id": portable.document["public_id"]}),
    ):
        with pytest.raises(DeviceCompatibilityError):
            method(
                rfc8785.dumps(
                    cast(
                        JsonValue,
                        {
                            "contract_version": "v2",
                            "schema_version": 2,
                            "platform": portable.platform,
                            **member,
                        },
                    )
                )
            )
    assert binding_snapshot(portable) == before


def test_expired_receipt_retires_real_rotation_chain_when_both_flags_off(
    portable: PortableHarness,
) -> None:
    body, raw, refresh, first = portable.register()
    profile = DeviceProfileServiceV2(
        portable.legacy.sessions,
        identity=portable.identity,
        enabled=lambda family: True,
        access_tokens=portable.legacy.access_tokens,
        access_ttl=portable.legacy.access_ttl,
    )
    challenge = body["challenge"]
    binding, generation = first, 0
    for _ in range(2):
        next_refresh = secrets.token_urlsafe(32)
        rotation = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": portable.platform,
            "rotation_id": str(uuid4()),
            "device_id": first["device_id"],
            "parent_session_id": binding["session_id"],
            "current_generation": generation,
            "current_refresh_token": refresh,
            "next_refresh_token_sha256": hashlib.sha256(next_refresh.encode()).hexdigest(),
            "signature_algorithm": "ES256-P1363",
            "expected_server_instance_id": challenge["server_instance_id"],
            "expected_identity_epoch": challenge["identity_epoch"],
            "expected_identity_thumbprint_sha256": challenge["identity_thumbprint_sha256"],
            "expected_api_origin": challenge["api_origin"],
            "expected_stream_origin": challenge["stream_origin"],
        }
        binding, _ = profile.session_rotate(signed(rotation, portable.device, ROTATE_DOMAIN))
        refresh, generation = next_refresh, generation + 1
    portable.gate.update(v2=False, portable=False)
    portable.clock["now"] = datetime.fromisoformat(str(first["access_expires_at"]))
    with pytest.raises(ProfilePairingError, match="account_receipt_expired"):
        portable.service.register(raw)
    with portable.legacy.sessions() as s:
        family = s.scalars(
            select(UserSessionRow).where(UserSessionRow.family_id == UUID(str(first["session_id"])))
        ).all()
        assert len(family) == 3 and all(row.revoked_at is not None for row in family)
        assert all(
            row.expires_at == datetime.fromisoformat(str(first["refresh_expires_at"]))
            for row in family
        )
    retired = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="account_receipt_expired"):
        portable.service.register(raw)
    assert binding_snapshot(portable) == retired


@pytest.mark.parametrize(
    "changed",
    [
        "proof",
        "wrong_root_proof",
        "body",
        "device_key",
        "binding",
        "refresh",
        "root",
        "identity",
        "platform",
        "version",
        "purpose",
        "challenge_purpose",
    ],
)
@pytest.mark.parametrize("expired", [False, True])
def test_changed_original_or_authority_never_grants_or_retires(
    portable: PortableHarness,
    changed: str,
    expired: bool,
) -> None:
    body, raw, _, first = portable.register()
    handler = portable.service.register
    if changed == "proof":
        for _ in range(4):
            different = original(portable.root, body)
            if different != raw:
                raw = different
                break
        else:
            raise AssertionError("future ECDSA fixture did not obtain a distinct valid proof")
    elif changed == "wrong_root_proof":
        raw = original(ec.generate_private_key(ec.SECP256R1()), body)
    elif changed == "device_key":
        raw = original(
            portable.root,
            {
                **body,
                "device_public_key_spki_b64": base64.b64encode(
                    public_spki(ec.generate_private_key(ec.SECP256R1()))
                ).decode(),
            },
        )
    elif changed == "binding":
        raw = original(portable.root, {**body, "binding_commit_id": str(uuid4())})
    elif changed == "refresh":
        raw = original(
            portable.root,
            {**body, "next_refresh_token_sha256": hashlib.sha256(uuid4().bytes).hexdigest()},
        )
    elif changed == "challenge_purpose":
        handler = portable.service.login
        raw = original(
            portable.root, {**body, "challenge": {**body["challenge"], "purpose": "LOGIN"}}
        )
    elif changed == "body":
        raw = original(portable.root, {**body, "device_name": "Changed original"})
    elif changed == "root":
        with portable.legacy.sessions.begin() as s:
            root = s.get(PortableAccountRow, UUID(str(first["account_id"])))
            assert root is not None
            replacement = ec.generate_private_key(ec.SECP256R1())
            root.document = {
                **portable.document,
                "public_key_spki_b64": base64.b64encode(public_spki(replacement)).decode(),
            }
            root.public_key, root.key_thumbprint = (
                public_spki(replacement),
                public_key_thumbprint(public_spki(replacement)),
            )
            root.revision += 1
    elif changed == "identity":
        portable.identity._api = "https://changed.test.invalid"
    elif changed == "platform":
        other = "MACOS" if portable.platform == "ANDROID" else "ANDROID"
        raw = original(
            portable.root,
            {**body, "platform": other, "challenge": {**body["challenge"], "platform": other}},
        )
    elif changed == "version":
        raw = original(portable.root, body, version="v1")
    else:
        handler = portable.service.login
    if expired:
        portable.clock["now"] = datetime.fromisoformat(str(first["access_expires_at"]))
    before = binding_snapshot(portable)
    with pytest.raises((ProfilePairingError, PortableAccountV2Error)):
        handler(raw)
    assert binding_snapshot(portable) == before


@pytest.mark.parametrize(
    "fence", ["role", "status", "generation", "root_revision", "device", "session", "blocked"]
)
@pytest.mark.parametrize("expired", [False, True])
def test_current_result_and_authority_fences_defeat_exact_replay(
    portable: PortableHarness,
    fence: str,
    expired: bool,
) -> None:
    _, raw, _, result = portable.register()
    with portable.legacy.sessions.begin() as s:
        account = s.get(UserAccountRow, UUID(str(result["account_id"])))
        device = s.get(DeviceRow, UUID(str(result["device_id"])))
        credential = s.get(UserSessionRow, UUID(str(result["session_id"])))
        root = s.get(PortableAccountRow, UUID(str(result["account_id"])))
        assert (
            account is not None
            and device is not None
            and credential is not None
            and root is not None
        )
        if fence == "role":
            account.role = "ADMIN"
        elif fence == "status":
            account.status = "DISABLED"
        elif fence == "generation":
            account.authority_generation += 1
        elif fence == "root_revision":
            root.revision += 1
        elif fence == "device":
            device.revoked_at = portable.clock["now"]
        elif fence == "session":
            credential.revoked_at = portable.clock["now"]
        else:
            s.add(
                DeviceKeyBlockRow(
                    user_id=account.user_id,
                    device_key_thumbprint_sha256=public_key_thumbprint(
                        public_spki(portable.device)
                    ),
                    blocked_at=portable.clock["now"],
                )
            )
    if expired:
        portable.clock["now"] = datetime.fromisoformat(str(result["access_expires_at"]))
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError):
        portable.service.register(raw)
    assert binding_snapshot(portable) == before


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("null_thumb", [False, True])
def test_historical_raw_spki_platform_contradiction_fails_closed(
    portable: PortableHarness,
    revoked: bool,
    null_thumb: bool,
) -> None:
    _, raw, _, result = portable.register()
    with portable.legacy.sessions.begin() as s:
        device = s.get(DeviceRow, UUID(str(result["device_id"])))
        assert device is not None
        device.platform = "MACOS" if portable.platform == "ANDROID" else "ANDROID"
        device.revoked_at = portable.clock["now"] if revoked else None
        if null_thumb:
            device.public_key_thumbprint_sha256 = None
    _, login, _ = portable.command("LOGIN")
    before = binding_snapshot(portable)
    for handler, command in ((portable.service.login, login), (portable.service.register, raw)):
        with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
            handler(command)
    assert binding_snapshot(portable) == before


def test_shared_v1_v2_rate_window_and_rejected_attempts_do_not_refund(
    portable: PortableHarness,
) -> None:
    for _ in range(30):
        portable.legacy.challenge("REGISTER", "shared-source", document=portable.document)
        portable.service.register_challenge(
            rfc8785.dumps(
                cast(
                    JsonValue,
                    {
                        "contract_version": "v2",
                        "schema_version": 2,
                        "platform": portable.platform,
                        "account": portable.document,
                    },
                )
            ),
            source="shared-source",
        )
    attempts: tuple[Callable[[], object], ...] = (
        lambda: portable.legacy.challenge("REGISTER", "shared-source", document=portable.document),
        lambda: portable.service.register(b"{}", source="shared-source"),
        lambda: portable.service.login_challenge(b"{}", source="shared-source"),
    )
    for attempt in attempts:
        with pytest.raises(ProfilePairingError, match="rate_limited"):
            attempt()
    with portable.legacy.sessions() as s:
        assert s.scalar(select(func.max(SelfPairingRateRow.count))) == 60


def test_capacity_shared_with_v1_and_same_device_retires_prior_sessions(
    portable: PortableHarness,
    database_connection: Connection[Any],
) -> None:
    _, original_raw, _, first = portable.register()
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=2"
    )
    database_connection.commit()
    other = ec.generate_private_key(ec.SECP256R1())
    legacy = binding_body(
        portable.legacy.challenge("LOGIN", "legacy-source", login=portable.document["public_id"]),
        other,
    )
    portable.legacy.complete(legacy, proof(portable.root, legacy))
    _, too_many, _ = portable.command("LOGIN", device=ec.generate_private_key(ec.SECP256R1()))
    before = binding_snapshot(portable)
    with pytest.raises(ResourceAdmissionError, match="account_device_limit_reached"):
        portable.service.login(too_many)
    assert binding_snapshot(portable) == before and portable.service.register(original_raw) == first
    _, same_device, _ = portable.command("LOGIN")
    replacement = portable.service.login(same_device)
    assert (
        replacement["device_id"] == first["device_id"]
        and replacement["session_id"] != first["session_id"]
    )
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        portable.service.register(original_raw)


@pytest.mark.parametrize("collision", ["challenge", "binding", "refresh"])
def test_binding_ids_and_refresh_hashes_cannot_cross_original_results(
    portable: PortableHarness, collision: str
) -> None:
    first_body, _, _, first = portable.register()
    next_body, _, _ = portable.command("LOGIN", device=ec.generate_private_key(ec.SECP256R1()))
    if collision == "challenge":
        next_body["binding_commit_id"] = first_body["challenge"]["challenge_id"]
    elif collision == "binding":
        next_body["binding_commit_id"] = first["binding_commit_id"]
    else:
        next_body["next_refresh_token_sha256"] = first_body["next_refresh_token_sha256"]
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        portable.service.login(original(portable.root, next_body))
    assert binding_snapshot(portable) == before
    with portable.legacy.sessions() as s:
        assert portable_v2_identifier_reserved(s, UUID(str(first["binding_commit_id"])))
        assert portable_v2_identifier_reserved(s, UUID(first_body["challenge"]["challenge_id"]))


def test_v1_rows_bytes_and_domains_stay_distinct_in_both_directions(
    portable: PortableHarness,
) -> None:
    legacy = binding_body(
        portable.legacy.challenge("REGISTER", "legacy-source", document=portable.document),
        ec.generate_private_key(ec.SECP256R1()),
    )
    v1_proof = proof(portable.root, legacy)
    first = portable.legacy.complete(legacy, v1_proof)
    assert portable.legacy.complete(legacy, v1_proof) == first
    with portable.legacy.sessions() as s:
        row = s.get(PortableAccountChallengeRow, UUID(legacy["challenge"]["challenge_id"]))
        assert row is not None and row.contract_version == "v1" and row.platform is None
        assert row.original_request_evidence is None and row.root_proof_sha256 is None
    with pytest.raises(PortableAccountV2Error):
        portable.service.register(
            rfc8785.dumps(cast(JsonValue, {"request": legacy, "proof": v1_proof}))
        )
    decorated = {
        **legacy,
        "contract_version": "v2",
        "schema_version": 2,
        "platform": portable.platform,
        "challenge": {
            **legacy["challenge"],
            "contract_version": "v2",
            "schema_version": 2,
            "platform": portable.platform,
        },
    }
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        portable.service.register(original(portable.root, decorated))
    assert binding_snapshot(portable) == before
    body, _raw, _, _ = portable.register()
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        portable.legacy.complete(body, proof(portable.root, body))
    # Direct receipt/retirement entry points also reject v2 before any effect.
    with portable.legacy.sessions.begin() as s:
        row = s.get(PortableAccountChallengeRow, UUID(body["challenge"]["challenge_id"]))
        assert row is not None
        with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
            portable.legacy._receipt(s, row, portable.clock["now"])
    assert binding_snapshot(portable) == before


def test_exact_concurrent_retry_commits_one_binding_and_one_receipt(
    portable: PortableHarness,
) -> None:
    _, raw, _ = portable.command()
    barrier = Barrier(2)

    def commit() -> dict[str, object]:
        barrier.wait(timeout=10)
        return portable.service.register(raw, source="race-source")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(), range(2)))
    assert results[0] == results[1]
    with portable.legacy.sessions() as s:
        assert s.scalar(select(func.count()).select_from(UserSessionRow)) == 1
        assert (
            s.scalar(
                select(func.count())
                .select_from(PortableAccountChallengeRow)
                .where(
                    PortableAccountChallengeRow.receipt.is_not(None),
                )
            )
            == 1
        )


def test_different_originals_racing_on_one_challenge_cannot_both_commit(
    portable: PortableHarness,
) -> None:
    body, raw, _ = portable.command()
    other = {
        **body,
        "binding_commit_id": str(uuid4()),
        "next_refresh_token_sha256": hashlib.sha256(uuid4().bytes).hexdigest(),
    }
    raws, barrier = (raw, original(portable.root, other)), Barrier(2)

    def commit(command: bytes) -> str:
        barrier.wait(timeout=10)
        try:
            portable.service.register(command, source="race-source")
            return "committed"
        except ProfilePairingError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, raws)) == ["committed", "operation_conflict"]
    with portable.legacy.sessions() as s:
        assert s.scalar(select(func.count()).select_from(UserSessionRow)) == 1


@pytest.mark.parametrize("committed", [False, True])
def test_0070_refuses_retained_v2_before_any_downgrade_drop(
    portable: PortableHarness,
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
    committed: bool,
) -> None:
    if committed:
        portable.register()
    else:
        portable.command()
    columns = database_connection.execute(
        "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='account' "
        "AND table_name='portable_challenge' ORDER BY ordinal_position",
    ).fetchall()
    database_connection.commit()
    with pytest.raises(Exception, match="portable_v2_retention_blocks_downgrade"):
        database_harness.downgrade(database_name, "0069_device_profile_contract_v2")
    assert (
        database_connection.execute(
            "SELECT column_name,data_type FROM information_schema.columns "
            "WHERE table_schema='account' "
            "AND table_name='portable_challenge' ORDER BY ordinal_position",
        ).fetchall()
        == columns
    )
    assert database_connection.execute(
        (
            "SELECT count(*) FROM pg_trigger WHERE tgname='portable_challenge_v2_guard' "
            "AND NOT tgisinternal"
        ),
    ).fetchone() == (1,)


def test_rebuilt_disabled_factory_replays_original_grant_after_ttl_reduction(
    portable: PortableHarness,
) -> None:
    from autplay.entrypoints.device_portable_v2_http import build_device_portable_v2_service
    from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
    from autplay.runtime.settings import ApiSettings
    from pydantic import SecretStr

    _, raw, _, first = portable.register()
    settings = ApiSettings(
        database_url=SecretStr(
            portable.legacy.sessions.kw["bind"].url.render_as_string(hide_password=False)
        ),
        auth_signing_secret=SecretStr("portable-test-access-secret-distinct-32bytes"),
        public_access_source_hmac_secret=SecretStr("portable-source-secret-at-least-32bytes"),
        auth_issuer="test",
        auth_audience="test",
        access_token_ttl_seconds=60,
        device_compatibility_v2_enabled=False,
        portable_accounts_enabled=False,
        privacy_ledger_path=portable.legacy.sessions.kw["info"][
            "autplay.retained_deletion_reservations"
        ].ledger.path,
        privacy_ledger_key=SecretStr("d1-portable-ledger-test-secret-32bytes"),
        privacy_ledger_key_id="fixture-v1",
    )
    evidence = DeletionEvidenceRuntime(settings, portable.legacy.sessions.kw["bind"])
    evidence.restore_guard()
    rebuilt = build_device_portable_v2_service(
        settings,
        portable.legacy.sessions.kw["bind"],
        identity=portable.identity,
        deletion_evidence=evidence,
    )
    assert rebuilt is not None
    before = binding_snapshot(portable)
    assert rebuilt.register(raw, source="fixture-source") == first
    assert binding_snapshot(portable) == before
    claims = rebuilt._portable.access_tokens.decode(
        str(first["access_token"]), now=portable.clock["now"]
    )
    assert claims.session_id == UUID(str(first["session_id"]))


def test_0070_preserves_v1_history_and_unknown_platform_without_backfill(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, "0069_device_profile_contract_v2")
    challenge_id, account_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            "INSERT INTO account.portable_challenge(challenge_id,purpose,sou"
            "rce_key,account_id,document,created_at,expires_at) "
            "VALUES(%s,'REGISTER',%s,%s,'{}'::jsonb,%s,%s)",
            (challenge_id, b"s" * 32, account_id, now, now + timedelta(seconds=120)),
        )
        before = connection.execute("SELECT * FROM account.portable_challenge").fetchall()
        connection.commit()
    database_harness.upgrade(empty_database_name, "0070_device_portable_contract_v2")
    with database_harness.connect(empty_database_name) as connection:
        assert (
            connection.execute(
                "SELECT challenge_id,purpose,source_key,account_id,actor_device_"
                "id,actor_session_id,document,created_at,"
                "expires_at,request_hash,receipt,receipt_expires_at FROM "
                "account.portable_challenge",
            ).fetchall()
            == before
        )
        assert connection.execute(
            "SELECT contract_version,platform,original_document_sha256,root_"
            "proof_sha256,original_request_evidence,"
            "binding_commit_id,authority_generation,root_revision FROM account.portable_challenge",
        ).fetchall() == [("v1", None, None, None, None, None, None, None)]
    database_harness.downgrade(empty_database_name, "0069_device_profile_contract_v2")
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute("SELECT * FROM account.portable_challenge").fetchall() == before


@pytest.mark.parametrize("alias", ["proof", "proof_b64", "request", "encrypted_root_proof"])
def test_database_refuses_root_proof_aliases_and_receipt_rewrites(
    portable: PortableHarness, alias: str
) -> None:
    body, raw, _, _ = portable.register()
    with pytest.raises(DBAPIError), portable.legacy.sessions.begin() as s:
        row = s.get(PortableAccountChallengeRow, UUID(body["challenge"]["challenge_id"]))
        assert row is not None and row.original_request_evidence is not None
        row.original_request_evidence = {
            **row.original_request_evidence,
            alias: json.loads(raw)["proof"],
        }
    # A fresh insert bypasses the UPDATE guard, so this separately exercises
    # the database's CLOSED projection rather than only its immutability.
    with (
        pytest.raises(DBAPIError, match="portable_challenge_v2_evidence_check"),
        portable.legacy.sessions.begin() as s,
    ):
        row = s.get(PortableAccountChallengeRow, UUID(body["challenge"]["challenge_id"]))
        assert (
            row is not None
            and row.original_request_evidence is not None
            and row.receipt is not None
        )
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        challenge_id, binding_id = uuid4(), uuid4()
        values.update(
            challenge_id=challenge_id,
            binding_commit_id=binding_id,
            document={**row.document, "challenge_id": str(challenge_id)},
            receipt={**row.receipt, "binding_commit_id": str(binding_id)},
            original_request_evidence={
                **row.original_request_evidence,
                "binding_commit_id": str(binding_id),
                alias: json.loads(raw)["proof"],
            },
        )
        s.add(PortableAccountChallengeRow(**values))


@pytest.mark.parametrize("case", ["limit", "claim", "role", "public_id", "passport"])
def test_existing_register_admission_and_identity_policy(
    portable: PortableHarness, case: str
) -> None:
    if case == "limit":
        with portable.legacy.sessions.begin() as s:
            s.add_all(
                UserAccountRow(
                    user_id=uuid4(), display_name="Synthetic account", role="USER", status="ACTIVE"
                )
                for _ in range(20)
            )
        _, raw, _ = portable.command()
        before = binding_snapshot(portable)
        with pytest.raises(ProfilePairingError, match="account_limit_reached"):
            portable.service.register(raw)
        assert binding_snapshot(portable) == before
        return
    if case in {"claim", "role"}:
        with portable.legacy.sessions.begin() as s:
            s.add(
                UserAccountRow(
                    user_id=UUID(portable.document["account_id"]),
                    display_name="Existing account",
                    role="USER" if case == "claim" else "ADMIN",
                    status="ACTIVE",
                )
            )
        code = "account_claim_required" if case == "claim" else "account_authentication_failed"
    else:
        portable.register()
        if case == "public_id":
            portable.document = passport(ec.generate_private_key(ec.SECP256R1()))
            code = "public_id_unavailable"
        else:
            portable.document = {
                **portable.document,
                "password_ciphertext_b64": base64.b64encode(b"z" * 100).decode(),
            }
            code = "account_identity_conflict"
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match=code):
        portable.command()
    assert binding_snapshot(portable) == before


def test_expired_or_pruned_pending_challenge_never_creates_authority(
    portable: PortableHarness,
) -> None:
    _, raw, _ = portable.command()
    portable.clock["now"] += timedelta(seconds=120)
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="account_challenge_expired"):
        portable.service.register(raw)
    assert binding_snapshot(portable) == before
    portable.clock["now"] += timedelta(minutes=11)
    with portable.legacy.sessions.begin() as s:
        portable.legacy._prune(s, portable.clock["now"])
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="account_challenge_expired"):
        portable.service.register(raw)
    assert binding_snapshot(portable) == before


def test_concurrent_first_accounts_cannot_bypass_twenty_account_limit(
    portable: PortableHarness,
) -> None:
    with portable.legacy.sessions.begin() as s:
        s.add_all(
            UserAccountRow(
                user_id=uuid4(), display_name="Synthetic account", role="USER", status="ACTIVE"
            )
            for _ in range(19)
        )
    commands: list[bytes] = []
    for index in range(2):
        root, device = (
            ec.generate_private_key(ec.SECP256R1()),
            ec.generate_private_key(ec.SECP256R1()),
        )
        document = {**passport(root), "public_id": f"portable_race_{index}"}
        challenge = portable.service.register_challenge(
            rfc8785.dumps(
                cast(
                    JsonValue,
                    {
                        "contract_version": "v2",
                        "schema_version": 2,
                        "platform": portable.platform,
                        "account": document,
                    },
                )
            )
        )
        body = {
            **challenge,
            "device_public_key_spki_b64": base64.b64encode(public_spki(device)).decode(),
            "device_name": "Account race",
            "app_version": "fixture",
            "binding_commit_id": str(uuid4()),
            "next_refresh_token_sha256": hashlib.sha256(uuid4().bytes).hexdigest(),
        }
        commands.append(original(root, body))
    barrier = Barrier(2)

    def commit(raw: bytes) -> str:
        barrier.wait(timeout=10)
        try:
            portable.service.register(raw)
            return "committed"
        except ProfilePairingError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, commands)) == ["account_limit_reached", "committed"]
    with portable.legacy.sessions() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(UserAccountRow)
                .where(UserAccountRow.status == "ACTIVE")
            )
            == 20
        )
        assert s.scalar(select(func.count()).select_from(PortableAccountRow)) == 1
        assert s.scalar(select(func.count()).select_from(PublicIdRegistrationRow)) == 1
        assert s.scalar(select(func.count()).select_from(UserSessionRow)) == 1


def test_rejected_parse_and_lookup_attempts_are_committed_to_shared_budget(
    portable: PortableHarness,
) -> None:
    with pytest.raises(PortableAccountV2Error):
        portable.service.register(b"{}", source="rejected-source")
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        portable.service.login_challenge(
            rfc8785.dumps(
                cast(
                    JsonValue,
                    {
                        "contract_version": "v2",
                        "schema_version": 2,
                        "platform": portable.platform,
                        "public_id": "absent_user",
                    },
                )
            ),
            source="rejected-source",
        )
    with portable.legacy.sessions() as s:
        assert s.scalar(select(func.max(SelfPairingRateRow.count))) == 2


@pytest.mark.parametrize(
    "column,value",
    [
        ("contract_version", "'v2'"),
        ("platform", "'MACOS'"),
        ("original_document_sha256", "decode(repeat('ab',32),'hex')"),
        ("root_proof_sha256", "decode(repeat('cd',32),'hex')"),
        ("original_request_evidence", "'{}'::jsonb"),
        ("binding_commit_id", "'11111111-2222-4333-8444-555555555555'::uuid"),
        ("authority_generation", "1"),
        ("root_revision", "1"),
    ],
)
def test_downgrade_preflight_catches_partial_new_fields_before_any_drop(
    database_harness: DatabaseHarness,
    database_name: str,
    database_connection: Connection[Any],
    column: str,
    value: str,
) -> None:
    # Explicit synthetic interrupted/manual state, only in the guarded clone.
    # The missing CHECK does not excuse downgrade from its independent preflight.
    database_connection.execute(
        "ALTER TABLE account.portable_challenge DROP CONSTRAINT "
        "portable_challenge_v2_evidence_check"
    )
    # Identifiers/SQL expressions come exclusively from the fixed parameter table.
    database_connection.execute(
        "INSERT INTO account.portable_challenge(challenge_id,purpose,source_key,"
        f"account_id,document,created_at,expires_at,{column}) "
        f"VALUES(%s,'REGISTER',%s,%s,'{{}}'::jsonb,now(),now()+interval '120 seconds',{value})",
        (uuid4(), b"s" * 32, uuid4()),
    )
    database_connection.commit()
    with pytest.raises(Exception, match="portable_v2_retention_blocks_downgrade"):
        database_harness.downgrade(database_name, ("0069_device_profile_contract_v2"))
    assert database_connection.execute(
        "SELECT count(*) FROM information_schema.columns WHERE "
        "table_schema='account' AND table_name='portable_challenge' "
        "AND column_name IN ('contract_version','platform','original_doc"
        "ument_sha256','root_proof_sha256',"
        "'original_request_evidence','binding_commit_id','authority_generation','root_revision')",
    ).fetchone() == (8,)
    assert database_connection.execute(
        (
            "SELECT count(*) FROM pg_trigger WHERE tgname='portable_challenge_v2_guard' "
            "AND NOT tgisinternal"
        ),
    ).fetchone() == (1,)


def test_concurrent_new_devices_share_capacity_across_platforms(
    portable: PortableHarness,
    database_connection: Connection[Any],
) -> None:
    portable.register()
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=2"
    )
    database_connection.commit()
    commands: list[bytes] = []
    original_platform = portable.platform
    try:
        for platform in ("ANDROID", "MACOS"):
            portable.platform = platform
            commands.append(
                portable.command("LOGIN", device=ec.generate_private_key(ec.SECP256R1()))[1]
            )
    finally:
        portable.platform = original_platform
    barrier = Barrier(2)

    def commit(raw: bytes) -> str:
        barrier.wait(timeout=10)
        try:
            portable.service.login(raw)
            return "committed"
        except ResourceAdmissionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, commands)) == ["account_device_limit_reached", "committed"]
    with portable.legacy.sessions() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(DeviceRow).where(DeviceRow.revoked_at.is_(None))
            )
            == 2
        )
        assert (
            s.scalar(
                select(func.count())
                .select_from(UserSessionRow)
                .where(UserSessionRow.revoked_at.is_(None))
            )
            == 2
        )


@pytest.mark.parametrize("collision", ["exchange", "binding", "invitation"])
def test_known_v1_receipt_store_ids_cannot_become_portable_bindings(
    portable: PortableHarness,
    collision: str,
) -> None:
    from autplay.adapters.postgresql.models.profile_pairing import (
        EnrollmentExchangeReceiptRow,
        EnrollmentInvitationRow,
    )

    first_body, _, _, first = portable.register()
    invitation_id, exchange_id, binding_id = uuid4(), uuid4(), uuid4()
    now = portable.clock["now"]
    with portable.legacy.sessions.begin() as s:
        # Historical v1 shape only; this seed makes no claim about invitation issuance.
        s.add(
            EnrollmentInvitationRow(
                invitation_id=invitation_id,
                server_instance_id=UUID(first_body["challenge"]["server_instance_id"]),
                user_id=UUID(str(first["account_id"])),
                issued_by_user_id=None,
                invitation_secret_hash=b"i" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=10),
                consumed_at=now,
            )
        )
        s.flush()
        s.add(
            EnrollmentExchangeReceiptRow(
                exchange_id=exchange_id,
                invitation_id=invitation_id,
                request_sha256=b"r" * 32,
                device_key_thumbprint_sha256=public_key_thumbprint(public_spki(portable.device)),
                device_id=UUID(str(first["device_id"])),
                session_id=UUID(str(first["session_id"])),
                binding_commit_id=binding_id,
                receipt_expires_at=now + timedelta(days=90, seconds=300),
                created_at=now,
            )
        )
    body, _, _ = portable.command("LOGIN", device=ec.generate_private_key(ec.SECP256R1()))
    body["binding_commit_id"] = str(
        {"exchange": exchange_id, "binding": binding_id, "invitation": invitation_id}[collision]
    )
    before = binding_snapshot(portable)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        portable.service.login(original(portable.root, body))
    assert binding_snapshot(portable) == before

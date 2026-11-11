"""Unexecuted D1 PostgreSQL/HTTP qualification fixtures on disposable databases."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.device_compatibility import DeviceCompatibilityService
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccessTokenClaims, AccountRole, Principal
from autplay.domain.device_compatibility import (
    MAX_REVISION,
    DeviceCompatibilityError,
    NegotiationPolicy,
)
from autplay.domain.profile_pairing import (
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    verify_p1363,
)
from autplay.entrypoints.api import create_app
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi.testclient import TestClient
from psycopg import Connection
from psycopg.errors import CheckViolation
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from typed_assertions import require_instance

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
SECRET = "d1-synthetic-http-signing-secret-at-least-thirty-two-bytes"


@dataclass(frozen=True)
class NegotiationHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    identity: ProfilePairingService
    principal: Principal
    claims: AccessTokenClaims
    device_spki: bytes
    codec: Hs256AccessTokenCodec

    def service(self, **gates: bool) -> DeviceCompatibilityService:
        return DeviceCompatibilityService(
            self.sessions, identity=self.identity, policy=NegotiationPolicy(**gates)
        )

    def capabilities(self, **gates: bool) -> dict[str, Any]:
        return self.service(**gates).capabilities(self.principal, self.claims)


@pytest.fixture
def negotiation(
    database_url: str,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[NegotiationHarness]:
    set_device_qualification(monkeypatch)
    monkeypatch.setattr("autplay.application.profile_pairing._now", lambda: NOW)
    monkeypatch.setattr("autplay.application.device_compatibility._now", lambda: NOW)
    monkeypatch.setattr("autplay.adapters.system.SystemClock.now", lambda self: NOW)
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    codec = Hs256AccessTokenCodec(SECRET, issuer="autplay", audience="autplay-android")
    identity = ProfilePairingService(
        sessions,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="D1 synthetic identity",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=codec,
        access_ttl=timedelta(minutes=10),
    )
    user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    spki = public_spki(ec.generate_private_key(ec.SECP256R1()))
    database_connection.execute(
        (
            "INSERT INTO account.user_account(user_id,display_name,role) VALUES(%s,'D1 "
            "fixture','USER')"
        ),
        (user_id,),
    )
    database_connection.execute(
        "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version,"
        "public_key,public_key_thumbprint_sha256) VALUES(%s,%s,'D1 Mac','MACOS','fixture',%s,%s)",
        (device_id, user_id, spki, public_key_thumbprint(spki)),
    )
    database_connection.execute(
        "INSERT INTO account.user_session(session_id,user_id,device_id,refresh_token_hash,"
        "issued_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            session_id,
            user_id,
            device_id,
            hashlib.sha256(session_id.bytes).digest(),
            NOW - timedelta(minutes=1),
            NOW + timedelta(days=1),
        ),
    )
    database_connection.commit()
    principal = Principal(user_id, device_id, session_id, AccountRole.USER)
    claims = AccessTokenClaims(
        user_id, device_id, session_id, uuid4(), NOW, NOW, NOW + timedelta(minutes=10)
    )
    try:
        yield NegotiationHarness(engine, sessions, identity, principal, claims, spki, codec)
    finally:
        engine.dispose()


def test_signed_v2_negotiation_reuses_m5_and_preserves_v1(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    v1_discovery = negotiation.identity.discovery()
    v1_capabilities = negotiation.identity.capabilities(negotiation.principal)
    identity_before = database_connection.execute(
        "SELECT * FROM account.server_instance"
    ).fetchall()
    sessions_before = database_connection.execute("SELECT * FROM account.user_session").fetchall()
    database_connection.commit()
    for enabled in (False, True):
        discovery = negotiation.service(enabled=enabled).discovery()
        capabilities = negotiation.capabilities(enabled=enabled)
        assert (
            require_instance(discovery["payload"], dict)["families"]
            == require_instance(discovery["payload"], dict)["platforms"]
            == []
        )
        assert require_instance(capabilities["payload"], dict)["families"] == []
        assert set(require_instance(discovery["payload"], dict)) == {
            "contract_version",
            "schema_version",
            "server_instance_id",
            "identity_epoch",
            "identity_public_key_spki_b64",
            "identity_thumbprint_sha256",
            "api_origin",
            "stream_origin",
            "api_major",
            "families",
            "platforms",
            "issued_at",
            "expires_at",
        }
        assert set(capabilities["payload"]) == {
            "contract_version",
            "schema_version",
            "server_instance_id",
            "identity_epoch",
            "identity_public_key_spki_b64",
            "identity_thumbprint_sha256",
            "api_origin",
            "stream_origin",
            "api_major",
            "families",
            "platform",
            "user_id",
            "device_id",
            "capability_revision",
            "issued_at",
            "expires_at",
        }
        for field in (
            "server_instance_id",
            "identity_epoch",
            "identity_public_key_spki_b64",
            "identity_thumbprint_sha256",
            "api_origin",
            "stream_origin",
        ):
            assert (
                require_instance(discovery["payload"], dict)[field]
                == require_instance(v1_discovery["payload"], dict)[field]
            )
            assert (
                require_instance(capabilities["payload"], dict)[field]
                == require_instance(discovery["payload"], dict)[field]
            )
        trusted_spki = base64.b64decode(
            require_instance(v1_discovery["payload"], dict)["identity_public_key_spki_b64"]
        )
        for envelope, domain in (
            (discovery, "autplay:device-compatibility:negotiation:discovery:v2\n"),
            (capabilities, "autplay:device-compatibility:negotiation:capabilities:v2\n"),
        ):
            digest = canonical_sha256(require_instance(envelope["payload"], dict))
            assert envelope["payload_sha256"] == digest.hex()
            verify_p1363(
                trusted_spki, domain, digest, require_instance(envelope["signature_b64url"], str)
            )
    assert negotiation.identity.discovery()["payload_sha256"] == v1_discovery["payload_sha256"]
    assert (
        negotiation.identity.capabilities(negotiation.principal)["payload_sha256"]
        == (v1_capabilities["payload_sha256"])
    )
    assert (
        database_connection.execute("SELECT * FROM account.server_instance").fetchall()
        == identity_before
    )
    assert (
        database_connection.execute("SELECT * FROM account.user_session").fetchall()
        == sessions_before
    )


def test_revision_survives_service_recreation_refresh_role_gate_and_limit_changes(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = negotiation.capabilities()["payload"]
    assert first["capability_revision"] == 1
    monkeypatch.setattr(
        "autplay.application.device_compatibility._now", lambda: NOW + timedelta(seconds=1)
    )
    refreshed = negotiation.capabilities()["payload"]
    assert refreshed["capability_revision"] == 1 and refreshed["issued_at"] != first["issued_at"]
    assert negotiation.capabilities(enabled=True)["payload"]["capability_revision"] == 2
    assert negotiation.capabilities(enabled=True)["payload"]["capability_revision"] == 2
    database_connection.execute(
        "UPDATE account.user_account SET role='ADMIN' WHERE user_id=%s",
        (negotiation.principal.user_id,),
    )
    database_connection.commit()
    # The entry Principal still says USER; the transaction uses the current role.
    assert negotiation.capabilities(enabled=True)["payload"]["capability_revision"] == 3
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET default_devices=4,revision=revision+1"
    )
    database_connection.commit()
    assert negotiation.capabilities(enabled=True)["payload"]["capability_revision"] == 4
    assert negotiation.capabilities()["payload"]["capability_revision"] == 5
    assert negotiation.capabilities(portable_enabled=True)["payload"]["capability_revision"] == 6
    assert negotiation.capabilities(portable_enabled=True)["payload"]["families"] == []


@pytest.mark.parametrize(
    "statement,status",
    [
        ("UPDATE account.user_account SET status='DISABLED'", 401),
        ("UPDATE account.user_account SET deleted_at=now()", 401),
        ("UPDATE account.device SET revoked_at=now()", 401),
        ("UPDATE account.user_session SET revoked_at=now()", 401),
        ("UPDATE account.user_session SET expires_at=issued_at+interval '1 second'", 401),
        (
            "UPDATE account.device SET public_key_thumbprint_sha256=decode(repeat('ff',32),'hex')",
            401,
        ),
        ("UPDATE account.device SET public_key=NULL", 401),
        ("UPDATE account.device SET platform='OTHER'", 503),
    ],
)
def test_current_authority_is_rechecked_before_allocating_a_revision(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
    statement: str,
    status: int,
) -> None:
    database_connection.execute(statement)
    database_connection.commit()
    with pytest.raises(DeviceCompatibilityError) as failure:
        negotiation.capabilities()
    assert failure.value.status == status
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_capability_revision"
    ).fetchone() == (0,)


def test_expired_claims_and_wrong_scope_do_not_use_a_stale_principal(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    service = negotiation.service()
    for claims in (
        replace(negotiation.claims, expires_at=NOW),
        replace(negotiation.claims, user_id=uuid4()),
        replace(negotiation.claims, device_id=uuid4()),
        replace(negotiation.claims, session_id=uuid4()),
    ):
        with pytest.raises(DeviceCompatibilityError) as failure:
            service.capabilities(negotiation.principal, claims)
        assert failure.value.status == 401
    assert database_connection.execute(
        "SELECT count(*) FROM account.device_capability_revision"
    ).fetchone() == (0,)


def test_platform_conflict_is_not_an_android_fallback(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    database_connection.execute(
        "INSERT INTO account.device(user_id,device_name,platform,app_version,public_key,"
        "public_key_thumbprint_sha256,revoked_at) "
        "VALUES(%s,'historical','ANDROID','fixture',%s,%s,now())",
        (
            negotiation.principal.user_id,
            negotiation.device_spki,
            public_key_thumbprint(negotiation.device_spki),
        ),
    )
    database_connection.commit()
    with pytest.raises(DeviceCompatibilityError):
        negotiation.capabilities()


def test_blocked_current_key_cannot_obtain_capabilities(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    database_connection.execute(
        "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) VALUES(%s,%s)",
        (negotiation.principal.user_id, public_key_thumbprint(negotiation.device_spki)),
    )
    database_connection.commit()
    with pytest.raises(DeviceCompatibilityError) as failure:
        negotiation.capabilities()
    assert failure.value.status == 401


def test_both_v2_platform_values_are_explicit_current_device_evidence(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    assert negotiation.capabilities()["payload"]["platform"] == "MACOS"
    # Synthetic direct evidence transition; this does not claim a v2 issuer exists.
    database_connection.execute("UPDATE account.device SET platform='ANDROID'")
    database_connection.commit()
    view = negotiation.capabilities()["payload"]
    assert view["platform"] == "ANDROID" and view["capability_revision"] == 2
    assert view["families"] == []


def test_epoch_rollover_allocates_a_new_scope_and_retains_old_high_water(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    assert negotiation.capabilities(enabled=True)["payload"]["capability_revision"] == 1
    assert negotiation.capabilities()["payload"]["capability_revision"] == 2
    database_connection.execute("UPDATE account.server_instance SET identity_epoch=2")
    database_connection.commit()
    view = negotiation.capabilities()["payload"]
    assert (view["identity_epoch"], view["capability_revision"]) == (2, 1)
    assert database_connection.execute(
        "SELECT identity_epoch,capability_revision FROM "
        "account.device_capability_revision ORDER BY identity_epoch"
    ).fetchall() == [(1, 2), (2, 1)]


def test_concurrent_equal_content_has_one_durable_revision(negotiation: NegotiationHarness) -> None:
    gate = Barrier(2)

    def read() -> dict[str, Any]:
        gate.wait(timeout=5)
        return require_instance(negotiation.capabilities()["payload"], dict)

    with ThreadPoolExecutor(max_workers=2) as pool:
        views = list(pool.map(lambda _: read(), range(2)))
    assert views[0] == views[1]
    assert views[0]["capability_revision"] == 1


def test_database_guard_keeps_equal_revision_content_and_retains_high_water(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    negotiation.capabilities()
    original = database_connection.execute(
        "SELECT * FROM account.device_capability_revision"
    ).fetchall()
    database_connection.commit()
    for statement in (
        (
            "UPDATE account.device_capability_revision SET "
            "effective_content_sha256=decode(repeat('ff',32),'hex')"
        ),
        "UPDATE account.device_capability_revision SET capability_revision=capability_revision+1",
        "UPDATE account.device_capability_revision SET identity_epoch=identity_epoch+1",
        "DELETE FROM account.device_capability_revision",
        "TRUNCATE account.device_capability_revision",
    ):
        with pytest.raises(CheckViolation):
            database_connection.execute(statement)
        database_connection.rollback()
        assert (
            database_connection.execute(
                "SELECT * FROM account.device_capability_revision"
            ).fetchall()
            == original
        )
        database_connection.commit()


def test_revision_overflow_fails_closed_without_wraparound(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    identity = require_instance(negotiation.service().discovery()["payload"], dict)
    database_connection.execute(
        "INSERT INTO account.device_capability_revision(server_instance_id,identity_epoch,user_id,"
        "device_id,capability_revision,effective_content_sha256,identity_thumbprint_sha256,"
        "device_key_thumbprint_sha256,created_at,updated_at) VALUES(%s,1,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            UUID(identity["server_instance_id"]),
            negotiation.principal.user_id,
            negotiation.principal.device_id,
            MAX_REVISION,
            b"z" * 32,
            bytes.fromhex(identity["identity_thumbprint_sha256"]),
            public_key_thumbprint(negotiation.device_spki),
            NOW,
            NOW,
        ),
    )
    database_connection.commit()
    with pytest.raises(DeviceCompatibilityError):
        negotiation.capabilities()
    assert database_connection.execute(
        "SELECT capability_revision FROM account.device_capability_revision"
    ).fetchone() == (MAX_REVISION,)


def test_api_factory_exposes_gate_off_reads_and_no_mutator_stub(
    negotiation: NegotiationHarness,
    database_url: str,
) -> None:
    class Ready:
        def check(self) -> ReadinessResult:
            return ReadinessResult(True, "postgresql")

    settings = ApiSettings(
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr(SECRET),
        public_access_source_hmac_secret=SecretStr(
            "d1-source-hmac-secret-at-least-thirty-two-bytes"
        ),
    )
    app = create_app(
        settings, profile_pairing_service=negotiation.identity, readiness_probe=Ready()
    )
    token = negotiation.codec.issue(
        negotiation.principal,
        token_id=negotiation.claims.token_id,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    prefix = "/api/v1/device-compatibility/v2"
    with TestClient(app) as client:
        discovery = client.get(prefix + "/discovery")
        capabilities = client.get(
            prefix + "/capabilities", headers={"Authorization": "Bearer " + token}
        )
        assert discovery.status_code == capabilities.status_code == 200
        assert discovery.json()["payload"]["families"] == []
        assert capabilities.json()["payload"]["platform"] == "MACOS"
        assert capabilities.json()["payload"]["families"] == []
        for response in (
            discovery,
            capabilities,
            client.get(prefix + "/capabilities"),
            client.post(prefix + "/enrollment/exchanges", json={}),
            client.post(prefix + "/discovery"),
        ):
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"
        invalid = client.post(prefix + "/enrollment/exchanges", json={})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "device_contract_invalid"
        assert client.post(prefix + "/discovery").status_code == 405


def test_current_identity_mismatch_is_unavailable_and_keeps_prior_evidence(
    negotiation: NegotiationHarness,
    database_connection: Connection[Any],
) -> None:
    negotiation.capabilities()
    before = database_connection.execute(
        "SELECT * FROM account.device_capability_revision"
    ).fetchall()
    database_connection.execute(
        "UPDATE account.server_instance SET identity_thumbprint_sha256=%s",
        (b"x" * 32,),
    )
    database_connection.commit()
    with pytest.raises(DeviceCompatibilityError):
        negotiation.service().discovery()
    with pytest.raises(DeviceCompatibilityError):
        negotiation.capabilities()
    assert (
        database_connection.execute("SELECT * FROM account.device_capability_revision").fetchall()
        == before
    )

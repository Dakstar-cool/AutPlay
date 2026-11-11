"""UNEXECUTED PostgreSQL/HTTP/M5 SELF gates; all keys and secrets are synthetic.

Authoring only: no imports, signing, hashes, DB, HTTP, tests or checks were run.
Exact retries reuse cached original bytes; structural negatives are freshly signed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock, local
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.resource_limits import require_device_capacity
from autplay.adapters.postgresql.self_device_pairing import (
    SqlAlchemySelfPairingRepository,
    SqlAlchemySelfPairingUnitOfWorkFactory,
    SqlAlchemySelfPairingV2UnitOfWorkFactory,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import Uuid7Generator
from autplay.application.auth import AuthService
from autplay.application.device_self_service_v2 import DeviceSelfServiceV2
from autplay.application.public_access import PublicAccessError, PublicAccessService
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain import device_compatibility
from autplay.domain.auth import Principal
from autplay.domain.device_compatibility import (
    NegotiationIdentity,
)
from autplay.domain.device_self_v2 import (
    SelfOperation,
    comparison_code,
    parse_self_request,
    verify_recipient_proof,
)
from autplay.domain.profile_pairing import canonical_sha256, public_spki, sign_p1363
from autplay.domain.self_device_pairing import SelfPairingError
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.device_self_v2_http import create_device_self_v2_router
from autplay.entrypoints.public_access_http import build_exact_proxy_source_resolver
from autplay.entrypoints.self_device_pairing_http import create_self_device_pairing_router
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from typed_assertions import present, require_instance

from .test_device_profile_v2 import ROTATE_DOMAIN, ProfileHarness, _legacy_pair, signed
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_public_access_pa2 import _request as registration_request

PREFIX = "/api/v1/device-compatibility/v2/self/ceremonies"
TRUST = (
    "expected_server_instance_id",
    "expected_identity_epoch",
    "expected_identity_thumbprint_sha256",
    "expected_api_origin",
    "expected_stream_origin",
)
SOURCE_SECRET = b"self-v1-v2-shared-fixture-source-secret-32bytes"


def wire(kind: str, body: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> bytes:
    body = {
        name: value
        for name, value in body.items()
        if name not in {"request_sha256", "device_signature_b64url"}
    }
    digest = canonical_sha256(body)
    body["request_sha256"] = digest.hex()
    if kind in {"claim", "poll", "exchange"}:
        body["device_signature_b64url"] = sign_p1363(
            key, f"autplay:device-compatibility:self:{kind}:v2\n", digest
        )
    return rfc8785.dumps(body)


@dataclass
class SelfHarness:
    profile: ProfileHarness
    service: DeviceSelfServiceV2
    actor: Principal
    source: dict[str, Any]
    source_binding: dict[str, Any]
    source_key: ec.EllipticCurvePrivateKey
    source_refresh: str
    recipient_platform: str
    key: ec.EllipticCurvePrivateKey
    ceremony_id: str = field(default_factory=lambda: str(uuid4()))
    rendezvous: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    poll_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    originals: dict[str, bytes] = field(default_factory=dict)
    outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    family_gate: dict[str, bool] = field(default_factory=lambda: {"enabled": True})

    def now(self) -> datetime:
        return self.profile.clock["now"]

    def command(self, kind: str, **changes: Any) -> bytes:
        if kind in self.originals:
            if not changes:
                return self.originals[kind]
            return wire(kind, {**json.loads(self.originals[kind]), **changes}, self.key)
        document = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": self.profile.platform
            if kind in {"start", "decision"}
            else self.recipient_platform,
            "ceremony_id": self.ceremony_id,
            "requested_at": self.now().isoformat().replace("+00:00", "Z"),
            **{name: self.source[name] for name in TRUST},
        }
        if kind == "start":
            document.update(
                operation_id=str(uuid4()),
                rendezvous_secret_sha256=hashlib.sha256(self.rendezvous.encode()).hexdigest(),
            )
        elif kind == "claim":
            key = public_spki(self.key)
            document.update(
                claim_id=str(uuid4()),
                poll_secret_sha256=hashlib.sha256(self.poll_secret.encode()).hexdigest(),
                device_public_key_spki_b64=base64.b64encode(key).decode(),
                device_key_thumbprint_sha256=hashlib.sha256(key).hexdigest(),
                device_name="Synthetic recipient",
                app_version="d1-self-fixture",
            )
        else:
            claim = json.loads(self.originals["claim"])
            document.update(
                claim_id=claim["claim_id"], claim_request_sha256=claim["request_sha256"]
            )
            if kind == "decision":
                status = self.outcomes["claim"]
                document.update(
                    operation_id=str(uuid4()),
                    action="APPROVE",
                    expected_revision=status["revision"],
                    comparison_code=status["comparison_code"],
                )
            elif kind == "exchange":
                document.update(
                    exchange_id=str(uuid4()),
                    binding_commit_id=str(uuid4()),
                    approval_operation_id=self.outcomes["decision"]["approval_operation_id"],
                    confirmed_account_id=str(self.actor.user_id),
                    next_refresh_token_sha256=hashlib.sha256(
                        secrets.token_urlsafe(32).encode()
                    ).hexdigest(),
                )
        self.originals[kind] = wire(kind, {**document, **changes}, self.key)
        return self.originals[kind]

    def token(self) -> str:
        return self.profile.identity._access.issue(
            self.actor,
            token_id=uuid4(),
            issued_at=self.now(),
            expires_at=self.now() + timedelta(minutes=10),
        )

    def client(self, *, proxy: str | None = None, peer: str = "198.51.100.8") -> TestClient:
        identity = self.profile.identity
        auth = AuthService(
            SqlAlchemyAuthUnitOfWorkFactory(identity._sessions),
            self,
            Uuid7Generator(),
            identity._access,
            OpaqueRefreshTokenCodec(),
            identity._access_ttl,
            timedelta(days=90),
        )
        app = FastAPI()
        install_error_handlers(app)
        resolver = build_exact_proxy_source_resolver(proxy)
        authenticated = bearer_authentication(auth)
        app.include_router(
            create_device_self_v2_router(
                self.service, authenticated=authenticated, canonical_source=resolver
            ),
            prefix="/api/v1",
        )
        app.include_router(
            create_self_device_pairing_router(
                self.service._rates, authenticated=authenticated, canonical_source=resolver
            ),
            prefix="/api/v1",
        )
        return TestClient(app, client=(peer, 45000))

    def send(self, kind: str, raw: bytes | None = None, *, secret: str | None = None) -> Response:
        headers = {"Content-Type": "application/json"}
        if kind in {"start", "decision", "status"}:
            headers["Authorization"] = "Bearer " + self.token()
        else:
            headers["X-AutPlay-Pairing-Secret"] = (
                secret
                if secret is not None
                else (self.rendezvous if kind == "claim" else self.poll_secret)
            )
        path = (
            PREFIX
            if kind == "start"
            else f"{PREFIX}/{self.ceremony_id}" + ("" if kind == "status" else "/" + kind)
        )
        client = self.client()
        return (
            client.get(path, headers=headers)
            if kind == "status"
            else client.post(
                path, content=raw if raw is not None else self.command(kind), headers=headers
            )
        )

    def through(self, stage: str) -> dict[str, Any]:
        for kind in ("start", "claim", "decision", "exchange"):
            if kind not in self.outcomes:
                response = self.send(kind)
                assert response.status_code == 200, response.json()
                self.outcomes[kind] = response.json()
            if kind == stage:
                return self.outcomes[kind]
        raise AssertionError("stage")


@pytest.fixture(params=["ANDROID", "MACOS"])
def self_v2(
    request: pytest.FixtureRequest,
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> SelfHarness:
    # profile itself supplies BOTH source platforms and real configured M5 keys.
    profile.clock["now"] = datetime.now(UTC)
    database_connection.execute(
        (
            "UPDATE account.user_session SET issued_at=%s,last_rotated_at=%s,expires_at=%"
            "s WHERE session_id=%s"
        ),
        (
            profile.clock["now"],
            profile.clock["now"],
            profile.clock["now"] + timedelta(days=90),
            profile.owner.session_id,
        ),
    )
    database_connection.commit()
    source, binding, source_key, refresh = profile.enroll()
    # Enrollment issues the source family using PostgreSQL time. Start the
    # controlled clock after that real issue time, then move only its return.
    profile.clock["now"] = present(
        database_connection.execute("SELECT clock_timestamp()").fetchone()
    )[0]
    database_connection.commit()
    actual_database_now = SqlAlchemySelfPairingRepository.database_now

    def final_clock(repo: SqlAlchemySelfPairingRepository) -> datetime:
        actual_database_now(repo)
        return profile.clock["now"]

    monkeypatch.setattr(SqlAlchemySelfPairingRepository, "database_now", final_clock)
    identity = profile.identity
    rates = SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(identity._sessions),
        identity._access,
        identity._access_ttl,
        SOURCE_SECRET,
    )
    family_gate = {"enabled": True}
    service = DeviceSelfServiceV2(
        SqlAlchemySelfPairingV2UnitOfWorkFactory(
            identity._sessions, lambda s: identity.device_contract_identity(s, profile.clock["now"])
        ),
        enabled=lambda: profile.gate["enabled"] and family_gate["enabled"],
        access_tokens=identity._access,
        access_ttl=identity._access_ttl,
        rates=rates,
    )
    monkeypatch.setattr(
        "autplay.application.device_self_service_v2._now", lambda: profile.clock["now"]
    )
    return SelfHarness(
        profile,
        service,
        Principal(
            profile.owner.user_id,
            UUID(binding["device_id"]),
            UUID(binding["session_id"]),
            profile.owner.role,
        ),
        source,
        binding,
        source_key,
        refresh,
        request.param,
        ec.generate_private_key(ec.SECP256R1()),
        family_gate=family_gate,
    )


def test_all_six_routes_mixed_platforms_and_exact_lost_response(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)
    h = self_v2
    start = h.through("start")
    assert start["source_platform"] == h.profile.platform and start["recipient_platform"] is None
    assert h.send("start").json() == start
    assert h.send("status").json() == start
    claim = h.through("claim")
    assert "account_id" not in claim and claim["recipient_platform"] == h.recipient_platform
    assert h.send("claim").json() == claim
    parsed_start = parse_self_request(SelfOperation.START, h.originals["start"])
    parsed_claim = parse_self_request(SelfOperation.CLAIM, h.originals["claim"])
    verified = verify_recipient_proof(
        parsed_claim,
        approved_public_key_spki=public_spki(h.key),
        approved_platform=parsed_claim.platform,
    )
    assert claim["comparison_code"] == comparison_code(parsed_start, verified)
    # Independent recipe assertion, including NEW domain and raw inner digest.
    payload = {
        "server_instance_id": h.source["expected_server_instance_id"],
        "identity_epoch": h.source["expected_identity_epoch"],
        "ceremony_id": h.ceremony_id,
        "claim_request_sha256": parsed_claim.request_sha256.hex(),
        "key_thumbprint": hashlib.sha256(public_spki(h.key)).hexdigest(),
    }
    sas = hashlib.sha256(
        b"autplay:device-compatibility:self:sas:v2\n" + canonical_sha256(payload)
    ).digest()
    assert claim["comparison_code"] == f"{int.from_bytes(sas[:8], 'big') % 10**12:012d}"
    approved = h.through("decision")
    assert h.send("decision").json() == approved
    poll = h.send("poll")
    assert poll.status_code == 200 and poll.json()["account_id"] == str(h.actor.user_id)
    assert h.send("poll").status_code == 429
    h.profile.clock["now"] += timedelta(seconds=2)
    first = h.through("exchange")
    h.profile.clock["now"] += timedelta(minutes=11)
    replay = h.send("exchange")
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert (
        replay.json()["device_id"] == first["device_id"]
        and replay.json()["session_id"] == first["session_id"]
    )
    assert replay.json()["access_expires_at"] > first["access_expires_at"]
    stored = database_connection.execute(
        "SELECT platform FROM account.device WHERE device_id=%s", (first["device_id"],)
    ).fetchone()
    assert stored == (h.recipient_platform,)
    for response in (poll, replay):
        assert (
            response.headers["cache-control"] == "no-store"
            and response.headers["pragma"] == "no-cache"
        )
    assert frozenset() == device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES


@pytest.mark.parametrize("stage", ["start", "claim", "decision", "exchange"])
def test_gate_off_retains_exact_commits_and_denies_next_first_effect(
    self_v2: SelfHarness, stage: str
) -> None:
    h = self_v2
    h.through(stage)
    h.profile.gate["enabled"] = False
    h.family_gate["enabled"] = False
    response = h.send(stage)
    assert response.status_code == 200
    assert h.send("status").status_code == 200
    if stage != "exchange":
        next_kind = {"start": "claim", "claim": "decision", "decision": "exchange"}[stage]
        assert h.send(next_kind).status_code == 503
    if stage in {"claim", "decision"}:
        cancel = h.command(
            "decision",
            operation_id=str(uuid4()),
            action="CANCEL",
            expected_revision=h.outcomes[stage]["revision"],
            comparison_code=None,
        )
        result = h.send("decision", cancel)
        assert result.status_code == 200 and result.json()["state"] == "CANCELLED"


@pytest.mark.parametrize("kind", ["claim", "exchange"])
def test_valid_changed_signature_is_not_exact_original(self_v2: SelfHarness, kind: str) -> None:
    h = self_v2
    h.through(kind)
    original = h.originals[kind]
    resigned = wire(kind, json.loads(original), h.key)
    assert resigned != original  # randomized ECDSA; original remains cached
    assert h.send(kind, resigned).status_code == 409
    assert h.send(kind, original).status_code == 200


@pytest.mark.parametrize(
    "change",
    ["secret", "claim_id", "claim_hash", "platform", "trust", "proof", "account", "approval"],
)
def test_exchange_rejects_wrong_possession_proof_and_locked_references(
    self_v2: SelfHarness, change: str
) -> None:
    h = self_v2
    h.through("decision")
    original = h.command("exchange")
    changes = {
        "claim_id": {"claim_id": str(uuid4())},
        "claim_hash": {"claim_request_sha256": "0" * 64},
        "platform": {"platform": "MACOS" if h.recipient_platform == "ANDROID" else "ANDROID"},
        "trust": {"expected_stream_origin": "https://other.test.invalid"},
        "account": {"confirmed_account_id": str(uuid4())},
        "approval": {"approval_operation_id": str(uuid4())},
    }
    raw = h.command("exchange", **changes[change]) if change in changes else original
    if change == "proof":
        raw = wire("exchange", json.loads(original), ec.generate_private_key(ec.SECP256R1()))
    response = h.send(
        "exchange", raw, secret=secrets.token_urlsafe(32) if change == "secret" else None
    )
    assert response.status_code in {401, 403, 409}
    assert h.send("exchange", original).status_code == 200


@pytest.mark.parametrize("change", ["sas", "revision", "claim", "source_platform"])
def test_decision_rechecks_sas_revision_exact_claim_and_source_platform(
    self_v2: SelfHarness, change: str
) -> None:
    h = self_v2
    h.through("claim")
    raw = h.command("decision")
    code = h.outcomes["claim"]["comparison_code"]
    wrong_code = str((int(code[0]) + 1) % 10) + code[1:]
    values = {
        "sas": {"comparison_code": wrong_code},
        "revision": {"expected_revision": 99},
        "claim": {"claim_id": str(uuid4())},
        "source_platform": {"platform": "MACOS" if h.profile.platform == "ANDROID" else "ANDROID"},
    }
    assert h.send("decision", h.command("decision", **values[change])).status_code in {403, 409}
    assert h.send("decision", raw).status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        b'{"schema_version":2,"schema_version":2}',
        b"[]",
        b"NaN",
        b'{"schema_version":2.0}',
        b'{"schema_version":2e0}',
        b'{"schema_version":true}',
    ],
)
def test_transport_errors_precede_gate_and_authority(self_v2: SelfHarness, bad: bytes) -> None:
    h = self_v2
    h.profile.gate["enabled"] = False
    response = h.client().post(PREFIX, content=bad, headers={"Content-Type": "application/json"})
    assert (
        response.status_code == 400
        and response.json()["error"]["code"] == "device_contract_invalid"
    )
    assert (
        h.client()
        .post(PREFIX, content=b"x" * 8193, headers={"Content-Type": "application/json"})
        .status_code
        == 413
    )
    assert h.client().post(PREFIX + "/bogus/unknown", json={}).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "generation",
        "account_disabled",
        "source_revoked",
        "source_session_revoked",
        "source_block",
        "source_key",
        "source_platform",
    ],
)
@pytest.mark.usefixtures("replacement_owner")
def test_current_authority_fences_pending_first_exchange(
    self_v2: SelfHarness, database_connection: Connection[Any], change: str
) -> None:
    h = self_v2
    h.through("decision")
    if change == "generation":
        database_connection.execute(
            (
                "UPDATE account.user_account SET authority_generation=authority_generation+1 "
                "WHERE user_id=%s"
            ),
            (h.actor.user_id,),
        )
    elif change == "account_disabled":
        database_connection.execute(
            "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s", (h.actor.user_id,)
        )
    elif change == "source_session_revoked":
        database_connection.execute(
            "UPDATE account.user_session SET revoked_at=%s WHERE session_id=%s",
            (h.now(), h.actor.session_id),
        )
    elif change == "source_block":
        database_connection.execute(
            (
                "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256,bl"
                "ocked_at) VALUES(%s,%s,%s)"
            ),
            (h.actor.user_id, hashlib.sha256(public_spki(h.source_key)).digest(), h.now()),
        )
    else:
        column, value = {
            "source_revoked": ("revoked_at", h.now()),
            "source_key": ("public_key", public_spki(h.key)),
            "source_platform": (
                "platform",
                "MACOS" if h.profile.platform == "ANDROID" else "ANDROID",
            ),
        }[change]
        database_connection.execute(
            f"UPDATE account.device SET {column}=%s WHERE device_id=%s", (value, h.actor.device_id)
        )
    database_connection.commit()
    assert h.send("exchange").status_code in {401, 403, 409}
    assert database_connection.execute(
        "SELECT count(*) FROM account.self_device_pairing WHERE state='EXCHANGED'"
    ).fetchone() == (0,)


@pytest.mark.parametrize("same_family", [True, False])
def test_source_rotation_preserves_only_original_family(
    self_v2: SelfHarness, database_connection: Connection[Any], same_family: bool
) -> None:
    h = self_v2
    h.through("claim")
    rotation, _ = h.profile.rotation(h.source_binding, h.source, h.source_refresh)
    result, _ = h.profile.service.session_rotate(signed(rotation, h.source_key, ROTATE_DOMAIN))
    h.actor = Principal(
        h.actor.user_id,
        h.actor.device_id,
        UUID(require_instance(result["session_id"], str)),
        h.actor.role,
    )
    if not same_family:
        database_connection.execute(
            "UPDATE account.user_session SET family_id=session_id WHERE session_id=%s",
            (h.actor.session_id,),
        )
        database_connection.commit()
    assert h.send("decision").status_code == (200 if same_family else 403)


@pytest.mark.parametrize("change", ["session", "device", "generation", "rotation"])
def test_success_replay_never_resurrects_original_binding(
    self_v2: SelfHarness, database_connection: Connection[Any], change: str
) -> None:
    h = self_v2
    binding = h.through("exchange")
    if change == "generation":
        database_connection.execute(
            (
                "UPDATE account.user_account SET authority_generation=authority_generation+1 "
                "WHERE user_id=%s"
            ),
            (h.actor.user_id,),
        )
    elif change == "device":
        database_connection.execute(
            "UPDATE account.device SET revoked_at=%s WHERE device_id=%s",
            (h.now(), binding["device_id"]),
        )
    else:
        database_connection.execute(
            "UPDATE account.user_session SET revoked_at=%s WHERE session_id=%s",
            (h.now(), binding["session_id"]),
        )
        if change == "rotation":
            database_connection.execute(
                "INSERT INTO account.user_session(session_id,user_id,device_id,r"
                "efresh_token_hash,issued_at,expires_at,family_id,generation,ses"
                "sion_mode) "
                "SELECT %s,user_id,device_id,%s,%s,expires_at,family_id,generati"
                "on+1,'V2' FROM account.user_session WHERE session_id=%s",
                (uuid4(), secrets.token_bytes(32), h.now(), binding["session_id"]),
            )
    database_connection.commit()
    assert h.send("exchange").status_code in {401, 403, 409}


@pytest.mark.parametrize("action", ["REJECT", "CANCEL"])
def test_terminal_and_expiry_horizons_do_not_reopen(self_v2: SelfHarness, action: str) -> None:
    h = self_v2
    h.through("claim")
    result = h.send("decision", h.command("decision", action=action, comparison_code=None))
    assert (
        result.status_code == 200
        and result.json()["state"] == {"REJECT": "REJECTED", "CANCEL": "CANCELLED"}[action]
    )
    assert h.send("claim").status_code == 403
    h.profile.clock["now"] += timedelta(minutes=16)
    assert h.send("status").status_code == 200
    fresh_poll = h.command("poll", requested_at=h.now().isoformat().replace("+00:00", "Z"))
    assert h.send("poll", fresh_poll).status_code == 200
    h.profile.clock["now"] += timedelta(days=1)
    assert h.send("status").status_code == 403
    assert h.service.cleanup(10) == 1


def test_expired_pending_status_poll_freshness_and_cleanup(self_v2: SelfHarness) -> None:
    h = self_v2
    h.through("claim")
    poll = h.command("poll")
    h.profile.clock["now"] += timedelta(seconds=901)
    assert h.send("status").json()["state"] == "EXPIRED"
    assert h.send("poll", poll).status_code == 403
    fresh_poll = h.command("poll", requested_at=h.now().isoformat().replace("+00:00", "Z"))
    assert h.send("poll", fresh_poll).json()["state"] == "EXPIRED"
    assert h.send("decision").status_code == 403
    assert h.service.cleanup(10) == 0
    h.profile.clock["now"] += timedelta(days=1)
    assert h.service.cleanup(10) == 1


def test_two_real_transactions_serialize_one_exchange_without_resigning(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    database_url: str,
) -> None:
    h = self_v2
    h.through("decision")
    original = h.command("exchange")
    before = database_connection.execute(
        "SELECT (SELECT count(*) FROM account.device),(SELECT count(*) FROM account.user_session)"
    ).fetchone()
    database_connection.commit()  # no observer transaction holds locks during the race
    barrier, stopping, registry_lock = Barrier(2), Event(), Lock()
    pids: list[int] = []
    connections: list[Connection[Any]] = []
    completed = [Event(), Event()]
    worker_state = local()
    identity = h.profile.identity
    original_units = h.service._units
    overall_deadline = monotonic() + 30
    application_name = "self-v2-race-" + uuid4().hex
    # Dedicated fixture connections: DatabaseHarness.connect() settings do NOT
    # reach SQLAlchemy sessions. PostgreSQL 18 bounds the whole transaction too.
    engine = create_engine(
        database_url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={
            "connect_timeout": 5,
            "application_name": application_name,
            "options": "-c lock_timeout=5000 -c statement_timeout=10000 "
            "-c idle_in_transaction_session_timeout=15000 -c transaction_timeout=25000",
        },
    )
    sessions = sessionmaker(
        engine,
        class_=Session,
        expire_on_commit=False,
        info=identity._sessions.kw["info"],
    )

    def remember(connection: Connection[Any], _record: Any) -> None:
        with registry_lock:
            connections.append(connection)

    def require_deadline(*_args: Any) -> None:
        if stopping.is_set() or monotonic() >= min(overall_deadline, worker_state.deadline):
            raise TimeoutError("SELF exchange fixture deadline")

    event.listen(engine, "connect", remember)
    event.listen(engine, "before_cursor_execute", require_deadline)
    event.listen(sessions, "before_commit", require_deadline)

    def coordinated(session: Session) -> NegotiationIdentity:
        require_deadline()
        pid = session.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        with registry_lock:
            pids.append(pid)
        barrier.wait(timeout=max(0, min(10, worker_state.deadline - monotonic())))
        require_deadline()  # both real transactions exist BEFORE shared identity locks
        return identity.device_contract_identity(session, h.now())

    def exchange(index: int) -> dict[str, Any]:
        worker_state.deadline = min(overall_deadline, monotonic() + 25)
        try:
            # The real unit of work rolls back and closes its own session in
            # __exit__, including a deadline, broken barrier or terminated DB.
            return h.service.exchange(h.poll_secret, original)
        except BaseException:
            stopping.set()
            barrier.abort()
            raise
        finally:
            completed[index].set()

    pool = ThreadPoolExecutor(max_workers=2)
    futures = []
    try:
        h.service._units = SqlAlchemySelfPairingV2UnitOfWorkFactory(sessions, coordinated)
        for index in range(2):
            futures.append(pool.submit(exchange, index))
        _, pending = wait(futures, timeout=max(0, overall_deadline - monotonic()))
        assert not pending, "SELF exchange workers exceeded the overall deadline"
        results = [future.result(timeout=0) for future in futures]
    finally:
        stopping.set()
        barrier.abort()  # also release the peer when setup/one worker fails
        try:
            if any(not future.done() for future in futures):
                # Terminate only this private engine's still-open fixture
                # connections. PostgreSQL waits at most 5s per backend; killing
                # the session rolls back its transaction and releases DB locks.
                with registry_lock:
                    active = tuple(
                        connection for connection in connections if not connection.closed
                    )
                for connection in active:
                    database_connection.execute(
                        "SELECT pg_terminate_backend(pid,5000) FROM pg_stat_activity "
                        "WHERE pid=%s AND datname=current_database() AND application_name=%s",
                        (connection.info.backend_pid, application_name),
                    )
                database_connection.commit()
        finally:
            # Do not discard running DB workers. Server timeouts, the deadline
            # hooks and backend termination bound their remaining DB work.
            pool.shutdown(wait=True, cancel_futures=True)
            try:
                database_connection.rollback()
            finally:
                h.service._units = original_units
                event.remove(sessions, "before_commit", require_deadline)
                event.remove(engine, "before_cursor_execute", require_deadline)
                event.remove(engine, "connect", remember)
                engine.dispose()
        assert all(future.done() for future in futures)
        assert all(
            future.cancelled() or completed[index].is_set() for index, future in enumerate(futures)
        )
    assert len(set(pids)) == 2 and sorted(item["replayed"] for item in results) == [False, True]
    assert (
        len({item["device_id"] for item in results})
        == len({item["session_id"] for item in results})
        == 1
    )
    assert before is not None
    assert database_connection.execute(
        "SELECT (SELECT count(*) FROM account.device),(SELECT count(*) FROM account.user_session)"
    ).fetchone() == (before[0] + 1, before[1] + 1)
    assert database_connection.execute(
        "SELECT count(*) FROM account.self_device_pairing WHERE state='EXCHANGED'"
    ).fetchone() == (1,)
    assert database_connection.execute(
        "SELECT exchange_document FROM account.self_device_pairing WHERE ceremony_id=%s",
        (h.ceremony_id,),
    ).fetchone() == (json.loads(original),)
    database_connection.commit()
    replay = h.service.exchange(h.poll_secret, original)
    assert replay["replayed"] is True and replay["session_id"] == results[0]["session_id"]
    assert h.originals["exchange"] == original


def test_genuine_legacy_android_positive_still_verifies_original_v1_bytes(
    profile: ProfileHarness, database_connection: Connection[Any]
) -> None:
    pair = _legacy_pair(profile, database_connection)
    _, original = pair.ready()
    first = pair.service.exchange(pair.poll_secret, original)
    replay = pair.service.exchange(pair.poll_secret, original)
    assert first["contract_version"] == "v1" and replay["session_id"] == first["session_id"]


@pytest.mark.parametrize("scope,maximum", [("global", 6000), ("source", 1200)])
def test_v1_v2_http_share_both_fifteen_minute_rate_counters(
    self_v2: SelfHarness, database_connection: Connection[Any], scope: str, maximum: int
) -> None:
    h = self_v2
    source = "198.51.100.8"
    h.service.rate_gate(source)
    digest = (
        hashlib.sha256(b"self-pairing-global-v1").digest()
        if scope == "global"
        else hmac.digest(SOURCE_SECRET, b"self-pairing-source\x00" + source.encode(), "sha256")
    )
    database_connection.execute(
        "UPDATE account.self_pairing_rate SET count=%s WHERE key_hash=%s", (maximum - 1, digest)
    )
    database_connection.commit()
    assert h.send("start").status_code == 200
    old = h.client().get(
        f"/api/v1/account/device-pairings/{h.ceremony_id}",
        headers={"Authorization": "Bearer " + h.token()},
    )
    assert old.status_code == 429 and old.headers["Retry-After"] == "15"
    assert h.send("status").status_code == 429


def test_exact_proxy_and_spoof_policy_do_not_partition_self_versions(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    h.through("start")
    client = h.client(proxy="127.0.0.7", peer="127.0.0.7")
    headers = {"Authorization": "Bearer " + h.token(), "x-autplay-client-ip": "2001:0db8::8"}
    assert client.get(f"{PREFIX}/{h.ceremony_id}", headers=headers).status_code == 200
    # Old v1 refuses a v2 ceremony, but consumes the SAME canonical source counter.
    assert (
        client.get(f"/api/v1/account/device-pairings/{h.ceremony_id}", headers=headers).status_code
        == 409
    )
    digest = hmac.digest(SOURCE_SECRET, b"self-pairing-source\x002001:db8::8", "sha256")
    assert database_connection.execute(
        "SELECT count FROM account.self_pairing_rate WHERE key_hash=%s", (digest,)
    ).fetchone() == (2,)
    untrusted = h.client(proxy="127.0.0.7", peer="198.51.100.8")
    assert untrusted.get(f"{PREFIX}/{h.ceremony_id}", headers=headers).status_code == 200
    unknown = hmac.digest(SOURCE_SECRET, b"self-pairing-source\x00unknown", "sha256")
    assert database_connection.execute(
        "SELECT count FROM account.self_pairing_rate WHERE key_hash=%s", (unknown,)
    ).fetchone() == (1,)


def test_three_pending_ceremonies_count_both_versions_together(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    old = _legacy_pair(h.profile, database_connection)
    old.service.start(old.actor, old.start())
    for _ in range(2):
        assert (
            h.send(
                "start", h.command("start", ceremony_id=str(uuid4()), operation_id=str(uuid4()))
            ).status_code
            == 200
        )
    assert (
        h.send(
            "start", h.command("start", ceremony_id=str(uuid4()), operation_id=str(uuid4()))
        ).status_code
        == 429
    )
    assert database_connection.execute(
        "SELECT count(*) FROM account.self_device_pairing WHERE state='OPEN'"
    ).fetchone() == (3,)


def test_exchanged_retention_and_no_source_dependency_after_commit(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    result = h.through("exchange")
    database_connection.execute(
        "UPDATE account.device SET revoked_at=%s WHERE device_id=%s", (h.now(), h.actor.device_id)
    )
    database_connection.commit()
    assert h.send("exchange").status_code == 200
    h.profile.clock["now"] = datetime.fromisoformat(
        result["refresh_absolute_expires_at"].replace("Z", "+00:00")
    )
    assert h.service.cleanup(10) == 0
    assert h.send("exchange").status_code == 403  # receipt survives; expired session grants nothing
    h.profile.clock["now"] += timedelta(seconds=301)
    assert h.service.cleanup(10) == 1


@pytest.mark.parametrize(
    "column",
    [
        "ceremony_id",
        "start_operation_id",
        "claim_id",
        "decision_operation_id",
        "exchange_id",
        "binding_commit_id",
    ],
)
def test_v1_nonissuer_and_issuer_reservations_cannot_poison_v2_receipts(
    self_v2: SelfHarness, database_connection: Connection[Any], column: str
) -> None:
    h = self_v2
    h.through("exchange")
    documents = {name: json.loads(raw) for name, raw in h.originals.items()}
    identifier = (
        h.ceremony_id
        if column == "ceremony_id"
        else documents[
            "start"
            if column == "start_operation_id"
            else "claim"
            if column == "claim_id"
            else "decision"
            if column == "decision_operation_id"
            else "exchange"
        ][
            {"start_operation_id": "operation_id", "decision_operation_id": "operation_id"}.get(
                column, column
            )
        ]
    )
    old = _legacy_pair(h.profile, database_connection)
    # This source has independently valid v1 authority and its own positive start.
    old_start = old.start()
    old.service.start(old.actor, old_start)
    with pytest.raises(SelfPairingError, match="operation_conflict"):
        old.service.start(
            old.actor, old.request("start", {**old.start(), "operation_id": identifier})
        )
    assert h.send("exchange").status_code == 200


@pytest.mark.parametrize("proof_field", ["device_name", "poll_secret_sha256", "app_version"])
def test_changed_valid_claim_is_conflict_without_mutating_original(
    self_v2: SelfHarness, proof_field: str
) -> None:
    h = self_v2
    h.through("claim")
    changed = "0" * 64 if proof_field.endswith("sha256") else "Changed"
    assert h.send("claim", h.command("claim", **{proof_field: changed})).status_code == 409
    assert h.send("claim").status_code == 200


@pytest.mark.parametrize("thumb_kind", ["exact", "null", "wrong"])
@pytest.mark.parametrize("same_platform", [True, False])
@pytest.mark.parametrize("other_owner", [True, False])
def test_historical_raw_spki_evidence_covers_missing_wrong_thumb_and_other_owner(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    thumb_kind: str,
    same_platform: bool,
    other_owner: bool,
) -> None:
    h = self_v2
    h.through("start")
    owner = h.actor.user_id
    if other_owner:
        owner = uuid4()
        database_connection.execute(
            (
                "INSERT INTO account.user_account(user_id,display_name) VALUES(%s,'Other "
                "synthetic owner')"
            ),
            (owner,),
        )
    spki = public_spki(h.key)
    thumb = {"exact": hashlib.sha256(spki).digest(), "null": None, "wrong": b"x" * 32}[thumb_kind]
    platform = (
        h.recipient_platform
        if same_platform
        else ("ANDROID" if h.recipient_platform == "MACOS" else "MACOS")
    )
    database_connection.execute(
        "INSERT INTO account.device(user_id,device_name,platform,app_ver"
        "sion,public_key,public_key_thumbprint_sha256,revoked_at) "
        "VALUES(%s,'Historical synthetic key',%s,'fixture',%s,%s,%s)",
        (owner, platform, spki, thumb, h.now()),
    )
    database_connection.commit()
    response = h.send("claim")
    assert response.status_code == (200 if same_platform and thumb_kind == "exact" else 409)


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize(
    "field",
    [
        "ceremony_id",
        "start_operation_id",
        "claim_id",
        "decision_operation_id",
        "exchange_id",
        "binding_commit_id",
    ],
)
def test_bidirectional_self_reservations_keep_exact_winner_replay(
    self_v2: SelfHarness, database_connection: Connection[Any], first: str, field: str
) -> None:
    h = self_v2
    old = _legacy_pair(h.profile, database_connection)
    if first == "v1":
        _claim, exchange = old.ready()
        old_result = old.service.exchange(old.poll_secret, exchange)
        row = database_connection.execute(
            "SELECT ceremony_id,start_operation_id,claim_id,approval_operati"
            "on_id,exchange_id,exchange_document->>'binding_commit_id' "
            "FROM account.self_device_pairing WHERE ceremony_id=%s",
            (old.ceremony_id,),
        ).fetchone()
        identifier = str(
            present(row)[
                (
                    "ceremony_id",
                    "start_operation_id",
                    "claim_id",
                    "decision_operation_id",
                    "exchange_id",
                    "binding_commit_id",
                ).index(field)
            ]
        )
        assert h.send("start", h.command("start", operation_id=identifier)).status_code == 409
        assert (
            old.service.exchange(old.poll_secret, exchange)["session_id"]
            == old_result["session_id"]
        )
    else:
        winner = h.through("exchange")
        row = database_connection.execute(
            "SELECT ceremony_id,start_operation_id,claim_id,approval_operati"
            "on_id,exchange_id,binding_commit_id "
            "FROM account.self_device_pairing WHERE ceremony_id=%s",
            (h.ceremony_id,),
        ).fetchone()
        identifier = str(
            present(row)[
                (
                    "ceremony_id",
                    "start_operation_id",
                    "claim_id",
                    "decision_operation_id",
                    "exchange_id",
                    "binding_commit_id",
                ).index(field)
            ]
        )
        with pytest.raises(SelfPairingError, match="operation_conflict"):
            old.service.start(
                old.actor, old.request("start", {**old.start(), "operation_id": identifier})
            )
        assert h.send("exchange").json()["session_id"] == winner["session_id"]


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange", "poll"])
@pytest.mark.parametrize("offset", [-121, 121])
def test_first_effects_and_every_poll_require_fresh_original_time(
    self_v2: SelfHarness, kind: str, offset: int
) -> None:
    h = self_v2
    if kind != "start":
        h.through(
            {"claim": "start", "decision": "claim", "exchange": "decision", "poll": "claim"}[kind]
        )
    original = h.command(kind)
    stamp = (h.now() + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
    assert h.send(kind, h.command(kind, requested_at=stamp)).status_code == 403
    assert h.send(kind, original).status_code == 200


@pytest.mark.parametrize("first", ["pa2", "v2"])
@pytest.mark.parametrize(
    "reserved",
    [
        "ceremony_id",
        "start_operation_id",
        "claim_id",
        "decision_operation_id",
        "exchange_id",
        "binding_commit_id",
    ],
)
def test_pa2_issuers_and_owner_nonissuers_cannot_poison_self_exact_replay(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    first: str,
    reserved: str,
) -> None:
    h = self_v2
    monkeypatch.setattr("autplay.application.public_access._now", h.now)
    pa2 = PublicAccessService(
        h.profile.identity._sessions,
        require_instance(h.profile.identity._access, Hs256AccessTokenCodec),
        h.profile.identity._access_ttl,
        b"self-pa2-bidirectional-source-secret-32bytes",
    )
    create = {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "account_display_name": "Synthetic PA2 friend",
        "expires_in_seconds": 600,
    }
    invitation, _ = pa2.create_invitation(h.profile.owner, create)
    key = ec.generate_private_key(ec.SECP256R1())
    registration = dict(registration_request(invitation, key))
    registration["next_refresh_token_sha256"] = hashlib.sha256(
        secrets.token_urlsafe(32).encode()
    ).hexdigest()

    def sign_registration() -> dict[str, Any]:
        result = {
            name: value
            for name, value in registration.items()
            if name not in {"request_sha256", "device_signature_b64url"}
        }
        digest = canonical_sha256(result)
        return {
            **result,
            "request_sha256": digest.hex(),
            "device_signature_b64url": sign_p1363(key, "AutPlay account registration v1\n", digest),
        }

    if first == "pa2":
        original = sign_registration()  # freeze once, including exact v1 signature
        winner, replayed = pa2.redeem(original, "synthetic-source")
        assert replayed is False
        for identifier in (
            registration["registration_id"],
            registration["binding_commit_id"],
            invitation["invitation_id"],
            create["operation_id"],
        ):
            assert h.send("start", h.command("start", operation_id=identifier)).status_code == 409
        replay, replayed = pa2.redeem(original, "synthetic-source")
        assert replayed is True and replay["session_id"] == winner["session_id"]
    else:
        winner = h.through("exchange")
        row = database_connection.execute(
            "SELECT ceremony_id,start_operation_id,claim_id,approval_operati"
            "on_id,exchange_id,binding_commit_id "
            "FROM account.self_device_pairing WHERE ceremony_id=%s",
            (h.ceremony_id,),
        ).fetchone()
        identifier = str(
            present(row)[
                (
                    "ceremony_id",
                    "start_operation_id",
                    "claim_id",
                    "decision_operation_id",
                    "exchange_id",
                    "binding_commit_id",
                ).index(reserved)
            ]
        )
        registration["registration_id"] = identifier
        with pytest.raises(PublicAccessError, match="registration_conflict"):
            pa2.redeem(sign_registration(), "synthetic-source")
        with pytest.raises(PublicAccessError, match="operation_conflict"):
            pa2.create_invitation(h.profile.owner, {**create, "operation_id": identifier})
        assert h.send("exchange").json()["session_id"] == winner["session_id"]
        # Prove independent OWNER/registration authority remains valid after denial.
        registration["registration_id"] = str(uuid4())
        assert pa2.redeem(sign_registration(), "synthetic-source")[1] is False


def test_capacity_is_rechecked_at_first_commit_without_stranding_existing_receipt(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    h.through("decision")
    original = h.command("exchange")
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=1"
    )
    database_connection.commit()
    response = h.send("exchange", original)
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "account_device_limit_reached"
    )
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=10"
    )
    database_connection.commit()
    first = h.send("exchange", original)
    assert first.status_code == 200
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=1"
    )
    database_connection.commit()
    replay = h.send("exchange", original)
    assert replay.status_code == 200 and replay.json()["session_id"] == first.json()["session_id"]


@pytest.mark.parametrize("actor_change", ["other_device", "wrong_account"])
def test_source_get_and_decision_require_exact_current_source_principal(
    self_v2: SelfHarness, actor_change: str
) -> None:
    h = self_v2
    h.through("claim")
    original_actor = h.actor
    h.actor = (
        h.profile.owner
        if actor_change == "other_device"
        else Principal(uuid4(), h.actor.device_id, h.actor.session_id, h.actor.role)
    )
    assert h.send("status").status_code == (403 if actor_change == "other_device" else 401)
    assert h.send("decision").status_code == (403 if actor_change == "other_device" else 401)
    h.actor = original_actor
    assert h.send("status").status_code == 200 and h.send("decision").status_code == 200


@pytest.mark.parametrize(
    "kind,changed",
    [
        ("start", "rendezvous_secret_sha256"),
        ("decision", "action"),
        ("exchange", "binding_commit_id"),
    ],
)
def test_changed_committed_original_conflicts_before_gate_or_freshness(
    self_v2: SelfHarness, kind: str, changed: str
) -> None:
    h = self_v2
    h.through(kind)
    h.profile.gate["enabled"] = False
    values = {
        "rendezvous_secret_sha256": "0" * 64,
        "action": "CANCEL",
        "binding_commit_id": str(uuid4()),
    }
    overrides: dict[str, object] = {changed: values[changed]}
    if kind == "decision":
        overrides["comparison_code"] = None
    assert h.send(kind, h.command(kind, **overrides)).status_code == 409
    assert h.send(kind).status_code == 200


@pytest.mark.parametrize("boundary", ["gate", "freshness"])
def test_change_during_real_capacity_read_is_rechecked_before_any_binding_write(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from autplay.adapters.postgresql import self_device_pairing as storage

    h = self_v2
    h.through("decision")
    original = h.command("exchange")
    devices = database_connection.execute("SELECT count(*) FROM account.device").fetchone()
    database_connection.commit()
    capacity = require_device_capacity

    def changing_capacity(session: Session, user_id: UUID) -> None:
        capacity(session, user_id)  # real quota read under real admission/account locks
        if boundary == "gate":
            h.profile.gate["enabled"] = False
        else:
            h.profile.clock["now"] += timedelta(seconds=121)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "require_device_capacity", changing_capacity)
        result = h.send("exchange", original)
    assert result.status_code == (503 if boundary == "gate" else 403)
    assert database_connection.execute("SELECT count(*) FROM account.device").fetchone() == devices
    assert database_connection.execute(
        "SELECT count(*) FROM account.self_device_pairing WHERE state='EXCHANGED'"
    ).fetchone() == (0,)
    database_connection.commit()
    h.profile.gate["enabled"] = True
    retry = (
        original
        if boundary == "gate"
        else h.command("exchange", requested_at=h.now().isoformat().replace("+00:00", "Z"))
    )
    assert h.send("exchange", retry).status_code == 200


@pytest.mark.parametrize("kind", ["claim", "poll", "exchange"])
def test_secret_header_must_be_single_on_an_otherwise_valid_real_proof(
    self_v2: SelfHarness, kind: str
) -> None:
    h = self_v2
    h.through({"claim": "start", "poll": "claim", "exchange": "decision"}[kind])
    original = h.command(kind)
    secret = h.rendezvous if kind == "claim" else h.poll_secret
    response = h.client().post(
        f"{PREFIX}/{h.ceremony_id}/{kind}",
        content=original,
        headers=[
            ("Content-Type", "application/json"),
            ("X-AutPlay-Pairing-Secret", secret),
            ("X-AutPlay-Pairing-Secret", secret),
        ],
    )
    assert response.status_code == 401
    assert h.send(kind, original, secret="").status_code == 401
    assert h.send(kind, original).status_code == 200

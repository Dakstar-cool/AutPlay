"""UNEXECUTED D1 deletion fixtures: genuine PG, M5, JWT and independent ledger.

No digest/signature/SQL has been evaluated while authoring this source. Mutation
precedes this fixture's independent JCS/P1363 signing; continuations cache exact
original bytes. Existing v1 recipes are asserted independently, never substituted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock, local
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.device_identifier_reservations import (
    device_identifier_reserved,
    device_v2_identifier_reserved,
)
from autplay.adapters.postgresql.models.account import DeviceRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.retained_deletion_reservations import (
    configure_retained_deletion_reservations,
)
from autplay.adapters.security.tokens import OpaqueRefreshTokenCodec
from autplay.adapters.system import Uuid7Generator
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.account_recovery import (
    AccountRecoveryService,
    cleanup_expired_recovery_operations,
    database_now,
)
from autplay.application.auth import AuthService
from autplay.application.device_deletion_service_v2 import DeviceDeletionServiceV2
from autplay.application.device_recovery_service_v2 import DeviceRecoveryServiceV2
from autplay.domain import device_compatibility
from autplay.domain.account_recovery import code_verifier, new_code
from autplay.domain.auth import Principal
from autplay.domain.privacy_deletion import (
    DeletionEvidence,
    DeletionEvidenceError,
    DeletionRequestEvidence,
)
from autplay.domain.profile_pairing import ProfilePairingError, public_spki
from autplay.entrypoints.device_deletion_v2_http import create_device_deletion_v2_router
from autplay.entrypoints.public_access_http import build_exact_proxy_source_resolver
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection as SAConnection
from sqlalchemy.engine.interfaces import DBAPICursor as SADBAPICursor
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry as SAConnectionPoolEntry
from typed_assertions import present

from .test_account_recovery import request as recovery_request
from .test_account_recovery import service as recovery_service
from .test_device_profile_v2 import ProfileHarness, _legacy_pair
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_self_device_pairing import PairingHarness

PREFIX = "/api/v1/device-compatibility/v2/deletion"
ROUTES = {
    "request": "requests",
    "receipt": "request-receipt",
    "resolve": "request-resolve",
    "preview": "cancel/preview",
    "cancel": "cancel/commit",
    "outcome": "cancel/outcome",
}

if TYPE_CHECKING:
    from .test_device_portable_v2 import PortableHarness


def sign_original(doc: dict[str, Any], key: ec.EllipticCurvePrivateKey, operation: str) -> bytes:
    """Independent future MESSAGE recipe; mutation must happen before this call."""
    body = {
        name: value
        for name, value in doc.items()
        if name not in {"request_sha256", "device_signature_b64url"}
    }
    digest = hashlib.sha256(rfc8785.dumps(body)).digest()
    signature = key.sign(
        ("autplay:device-compatibility:deletion:" + operation + ":v2\n").encode("ascii") + digest,
        ec.ECDSA(hashes.SHA256()),
    )
    r, s = utils.decode_dss_signature(signature)
    body["request_sha256"] = digest.hex()
    body["device_signature_b64url"] = (
        base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return rfc8785.dumps(body)


def snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    result = tuple(
        connection.execute(sql).fetchall()
        for sql in (
            "SELECT to_jsonb(r) FROM account.user_account r ORDER BY user_id",
            "SELECT to_jsonb(r) FROM account.device r ORDER BY device_id",
            "SELECT to_jsonb(r) FROM account.user_session r ORDER BY session_id",
            (
                "SELECT to_jsonb(r) FROM account.account_deletion_request r ORDER BY "
                "deletion_request_id"
            ),
            "SELECT to_jsonb(r) FROM account.account_deletion_proof r ORDER BY operation_id",
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id",
            "SELECT to_jsonb(r) FROM account.account_recovery_credential r ORDER BY user_id",
        )
    )
    connection.commit()
    return result


@dataclass
class DeletionHarness:
    profile: ProfileHarness
    pair: PairingHarness
    recovery: AccountRecoveryService
    service: DeviceDeletionServiceV2
    ledger: FilesystemDeletionLedger
    code: str
    next_code: str
    refresh: str
    gates: dict[str, bool]
    recipient: ec.EllipticCurvePrivateKey
    originals: dict[str, bytes] = field(default_factory=dict)
    parent: dict[str, Any] | None = None

    def now(self) -> datetime:
        return self.profile.clock["now"]

    def wire(self, kind: str, *, platform: str | None = None, **changes: Any) -> bytes:
        if kind in {"receipt", "resolve"}:
            return self.originals["request"]
        if kind == "outcome":
            return self.originals["cancel"]
        if kind in self.originals and not changes and platform is None:
            return self.originals[kind]
        key = self.pair.key if kind == "request" else self.recipient
        spki = public_spki(key)
        doc = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": platform or self.profile.platform,
            "operation_id": str(uuid4()),
            "account_id": str(self.pair.actor.user_id),
            "requested_at": self.now().isoformat().replace("+00:00", "Z"),
            **self.pair.identity,
            "device_public_key_spki_b64": base64.b64encode(spki).decode("ascii"),
            "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
            "device_name": "Synthetic deletion device",
            "app_version": "d1-delete-fixture",
        }
        if kind == "request":
            doc.update(
                confirmed_account_id=str(self.pair.actor.user_id),
                expected_code_generation=1,
                expected_authority_generation=1,
            )
        if kind == "cancel":
            assert self.parent is not None
            doc.update(
                confirmed_account_id=str(self.pair.actor.user_id),
                expected_code_generation=1,
                deletion_request_id=self.parent["deletion_request_id"],
                expected_revision=self.parent["revision"],
                next_code_verifier_sha256=code_verifier(
                    UUID(self.pair.identity["expected_server_instance_id"]),
                    self.pair.actor.user_id,
                    self.next_code,
                ).hex(),
                next_refresh_token_sha256=hashlib.sha256(self.refresh.encode("ascii")).hexdigest(),
                binding_commit_id=str(uuid4()),
            )
        doc.update(changes)
        raw = sign_original(doc, key, kind)
        if not changes and platform is None:
            self.originals[kind] = raw
        return raw

    def token(self) -> str:
        return self.recovery.access.issue(
            self.pair.actor,
            token_id=uuid4(),
            issued_at=self.now() - timedelta(seconds=1),
            expires_at=self.now() + timedelta(minutes=9),
        )

    def client(self, *, proxy: str | None = None, peer: str = "198.51.100.8") -> TestClient:
        auth = AuthService(
            SqlAlchemyAuthUnitOfWorkFactory(self.recovery.sessions),
            self,
            Uuid7Generator(),
            self.recovery.access,
            OpaqueRefreshTokenCodec(),
            self.recovery.ttl,
            timedelta(days=90),
        )
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(
            create_device_deletion_v2_router(
                self.service,
                authenticate=auth.authenticate_access_with_claims,
                canonical_source=build_exact_proxy_source_resolver(proxy),
            ),
            prefix="/api/v1",
        )
        return TestClient(app, client=(peer, 45123))

    def send(
        self,
        kind: str,
        *,
        raw: bytes | None = None,
        secret: str | None = None,
        headers: list[tuple[str, str]] | None = None,
    ) -> Response:
        credential = self.refresh if kind == "outcome" else self.code
        name = "X-AutPlay-Recovery-Refresh" if kind == "outcome" else "X-AutPlay-Recovery-Code"
        supplied = [("Content-Type", "application/json"), (name, secret or credential)]
        if kind == "request":
            supplied.append(("Authorization", "Bearer " + self.token()))
        supplied.extend(headers or [])
        with self.client() as client:
            return client.post(
                PREFIX + "/" + ROUTES[kind], content=raw or self.wire(kind), headers=supplied
            )

    def accept(self) -> dict[str, Any]:
        reply = self.send("request")
        assert reply.status_code == 200, reply.text
        self.parent = reply.json()
        return self.parent


@pytest.fixture
def deletion_v2(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> DeletionHarness:
    ledger = FilesystemDeletionLedger(
        tmp_path / "d1-deletion.sqlite3", b"d1-synthetic-ledger-secret-32bytes", "fixture-v1"
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=60))
    gates = {"v2": True, "deletion": True, "recovery": True, "evidence": True}
    configure_retained_deletion_reservations(
        profile.identity._sessions, ledger=ledger, evidence_ready=lambda: gates["evidence"]
    )
    pair = _legacy_pair(profile, database_connection)
    recovery, code = recovery_service(pair), new_code()
    configuration_time = datetime.now(UTC) - timedelta(days=45)
    configuration = recovery_request(
        pair,
        "configure",
        requested_at=configuration_time.isoformat().replace("+00:00", "Z"),
        expected_code_generation=0,
        next_code_verifier_sha256=code_verifier(
            UUID(pair.identity["expected_server_instance_id"]), pair.actor.user_id, code
        ).hex(),
    )

    def configuration_clock(session: Session) -> datetime:
        database_now(session)
        return configuration_time

    # Only setup's actual DB-clock return is controlled. Its v1 document/hash is
    # fresh at that time; no parser, proof or freshness predicate is bypassed.
    with monkeypatch.context() as setup_clock:
        setup_clock.setattr(
            "autplay.application.account_recovery.database_now", configuration_clock
        )
        recovery.configure(pair.actor, configuration)
    database_connection.execute(
        "INSERT INTO account.user_account(display_name,role) VALUES ('Synthetic "
        "remaining owner','OWNER')"
    )
    database_connection.commit()
    with recovery.sessions.begin() as session:
        device = session.get(DeviceRow, pair.actor.device_id)
        assert device is not None
        device.public_key, device.platform = public_spki(pair.key), profile.platform
        device.public_key_thumbprint_sha256 = hashlib.sha256(device.public_key).digest()
    service = DeviceDeletionServiceV2(
        AccountDeletionService(recovery, ledger),
        identity=profile.identity,
        enabled=lambda: gates["v2"],
        deletion_enabled=lambda: gates["deletion"],
        recovery_enabled=lambda: gates["recovery"],
        evidence_ready=lambda: gates["evidence"],
    )
    profile.clock["now"] = datetime.now(UTC)
    h = DeletionHarness(
        profile,
        pair,
        recovery,
        service,
        ledger,
        code,
        new_code(),
        secrets.token_urlsafe(32),
        gates,
        ec.generate_private_key(ec.SECP256R1()),
    )

    def final_clock(session: Session) -> datetime:
        database_now(session)  # Real last query, only the returned time is controlled.
        return h.now()

    monkeypatch.setattr("autplay.application.device_deletion_service_v2.database_now", final_clock)
    return h


@pytest.mark.parametrize("cross_platform", [False, True])
def test_full_original_revoked_actor_read_cancel_replay_and_live_late_outcome(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    cross_platform: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)
    h = deletion_v2
    h.profile.clock["now"] -= timedelta(days=2)
    accepted = h.accept()
    assert accepted["state"] == "PENDING" and accepted["revision"] == 1
    assert datetime.fromisoformat(
        accepted["cancel_before"].replace("Z", "+00:00")
    ) - datetime.fromisoformat(accepted["requested_at"].replace("Z", "+00:00")) == timedelta(
        days=30
    )
    assert h.send("receipt").json() == {**accepted, "replayed": True}
    assert h.send("resolve").json()["state"] == "PENDING"
    assert h.send("preview").status_code == 200
    platform = (
        ("MACOS" if h.profile.platform == "ANDROID" else "ANDROID")
        if cross_platform
        else h.profile.platform
    )
    original = h.wire("cancel", platform=platform)
    h.originals["cancel"] = original
    cancelled = h.send("cancel", raw=original)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["platform"] == platform
    assert h.send("cancel", raw=original).json()["session_id"] == cancelled.json()["session_id"]
    assert h.send("receipt").json()["state"] == "CANCELLED"
    h.profile.clock["now"] += timedelta(days=2)
    cleanup_expired_recovery_operations(h.recovery.sessions)
    outcome = h.send("outcome")
    assert outcome.status_code == 200 and outcome.json()["outcome_recovered"] is True
    assert outcome.json()["session_id"] == cancelled.json()["session_id"]
    stored = database_connection.execute(
        "SELECT original_request_document,original_document_sha256 FROM "
        "account.account_deletion_proof"
    ).fetchone()
    assert stored == (
        json.loads(h.originals["request"]),
        hashlib.sha256(h.originals["request"]).digest(),
    )
    receipt = database_connection.execute(
        "SELECT original_request_document,original_document_sha256 FROM "
        "account.account_recovery_operation WHERE kind='DELETE_CANCEL'"
    ).fetchone()
    assert receipt == (json.loads(original), hashlib.sha256(original).digest())
    assert h.code not in json.dumps(stored[0]) and h.refresh not in json.dumps(receipt[0])
    assert frozenset() == device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES


def test_sealed_veto_is_durable_exact_original_and_unchanged_recipe(
    deletion_v2: DeletionHarness, database_connection: Connection[Any]
) -> None:
    h = deletion_v2
    h.wire("request")
    h.profile.clock["now"] += timedelta(seconds=121)
    before = snapshot(database_connection)
    reply = h.send("resolve")
    assert reply.status_code == 200 and reply.json()["state"] == "NOT_ACCEPTED"
    (item,) = h.ledger.request_read()
    doc = json.loads(h.originals["request"])
    expected = hashlib.sha256(
        rfc8785.dumps(
            {
                "purpose": "account-deletion-sealed-proof-v1",
                "request_sha256": doc["request_sha256"],
                "code_verifier_sha256": code_verifier(
                    UUID(doc["expected_server_instance_id"]), h.pair.actor.user_id, h.code
                ).hex(),
            }
        )
    ).hexdigest()
    assert item.receipt_sha256 == expected
    with h.recovery.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.account_recovery_credential SET "
                "generation=generation+1,verifier_sha256=:value WHERE user_id=:owner"
            ),
            {
                "value": hashlib.sha256(b"synthetic-rotation").digest(),
                "owner": h.pair.actor.user_id,
            },
        )
    assert h.send("resolve").json() == {**reply.json(), "replayed": True}
    assert h.send("resolve", secret=new_code()).status_code == 403
    # Historical veto history is the only addition; no binding or session appears.
    after = snapshot(database_connection)
    assert before[:4] == after[:4]
    assert h.send("receipt").status_code != 200


@pytest.mark.parametrize("gate", ["v2", "deletion", "recovery", "evidence"])
def test_independent_gates_and_terminal_cancellation_exception(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], gate: str
) -> None:
    h = deletion_v2
    h.gates[gate] = False
    before = snapshot(database_connection)
    assert h.send("request").status_code == 503
    assert snapshot(database_connection) == before
    h.gates[gate] = True
    h.accept()
    h.gates[gate] = False
    if gate == "evidence":
        assert h.send("receipt").status_code == 503
        return
    assert h.send("receipt").status_code == 200
    assert h.send("preview").status_code == 200
    assert h.send("cancel").status_code == 200
    assert h.send("outcome").status_code == 200


def test_attempted_without_db_acceptance_never_becomes_negative(
    deletion_v2: DeletionHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_connection: Connection[Any],
) -> None:
    h = deletion_v2
    original_record = h.ledger.request_record

    def lose_reply(item: DeletionRequestEvidence) -> None:
        original_record(item)  # Actual independent durable record survives rollback.
        raise DeletionEvidenceError()

    monkeypatch.setattr(h.ledger, "request_record", lose_reply)
    assert h.send("request").status_code == 503
    monkeypatch.setattr(h.ledger, "request_record", original_record)
    assert h.ledger.request_read()[0].decision == "ATTEMPTED"
    h.profile.clock["now"] += timedelta(seconds=121)
    assert h.send("resolve").status_code == 503
    assert database_connection.execute(
        "SELECT count(*) FROM account.account_deletion_request"
    ).fetchone() == (0,)


def test_resolution_microsecond_margin_and_actual_coverage_cutover(
    deletion_v2: DeletionHarness, tmp_path: Path
) -> None:
    h = deletion_v2
    original_time = h.now()
    h.wire("request")
    h.profile.clock["now"] = original_time + timedelta(seconds=120, microseconds=1)
    assert h.send("resolve").status_code == 409
    h.profile.clock["now"] += timedelta(microseconds=1)
    # A genuinely independently initialized ledger at the exact unsafe margin
    # cannot certify this original, even when the original itself is expired.
    ledger = FilesystemDeletionLedger(
        tmp_path / "cutover.sqlite3", b"d1-synthetic-cutover-secret-32bytes", "fixture-v1"
    )
    ledger.initialize(coverage_started_at=original_time - timedelta(seconds=120, microseconds=1))
    h.ledger, h.service._ledger, h.service._deletion.ledger = ledger, ledger, ledger
    configure_retained_deletion_reservations(
        h.recovery.sessions, ledger=ledger, evidence_ready=lambda: h.gates["evidence"]
    )
    configure_retained_deletion_reservations(
        h.profile.identity._sessions, ledger=ledger, evidence_ready=lambda: h.gates["evidence"]
    )
    assert h.send("resolve").status_code == 503
    assert ledger.request_read() == ()


@pytest.mark.parametrize("kind", ["request", "receipt", "resolve", "preview", "cancel", "outcome"])
def test_duplicate_secret_headers_never_create_or_reissue(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], kind: str
) -> None:
    h = deletion_v2
    if kind != "request":
        h.accept()
    if kind == "outcome":
        assert h.send("cancel").status_code == 200
    before = snapshot(database_connection)
    name = "X-AutPlay-Recovery-Refresh" if kind == "outcome" else "X-AutPlay-Recovery-Code"
    secret = h.refresh if kind == "outcome" else h.code
    assert h.send(kind, headers=[(name, secret)]).status_code == 403
    assert snapshot(database_connection) == before


def test_re_signed_original_and_wrong_chosen_refresh_are_not_exact_history(
    deletion_v2: DeletionHarness, database_connection: Connection[Any]
) -> None:
    h = deletion_v2
    h.accept()
    replacement = sign_original(json.loads(h.originals["request"]), h.pair.key, "request")
    assert replacement != h.originals["request"]
    before = snapshot(database_connection)
    assert h.send("receipt", raw=replacement).status_code != 200
    assert snapshot(database_connection) == before
    assert h.send("cancel").status_code == 200
    before = snapshot(database_connection)
    assert h.send("outcome", secret=secrets.token_urlsafe(32)).status_code != 200
    assert snapshot(database_connection) == before


def test_ledger_wait_past_freshness_cannot_commit_authority(
    deletion_v2: DeletionHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_connection: Connection[Any],
) -> None:
    h = deletion_v2
    original = h.ledger.request_record

    def delayed_write(item: DeletionRequestEvidence) -> DeletionRequestEvidence:
        result = original(item)
        h.profile.clock["now"] += timedelta(seconds=121)
        return result

    monkeypatch.setattr(h.ledger, "request_record", delayed_write)
    before = snapshot(database_connection)
    assert h.send("request").status_code != 200
    assert snapshot(database_connection) == before
    assert h.ledger.request_read()[0].decision == "ATTEMPTED"
    assert h.send("resolve").status_code == 503


@pytest.mark.parametrize(
    "mutation",
    ["code_generation", "authority_generation", "confirmation", "platform", "operation_collision"],
)
def test_valid_independent_proof_does_not_mask_authority_negative(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], mutation: str
) -> None:
    h = deletion_v2
    mutation_fields: dict[str, dict[str, object]] = {
        "code_generation": {"expected_code_generation": 2},
        "authority_generation": {"expected_authority_generation": 2},
        "confirmation": {"confirmed_account_id": str(uuid4())},
        "platform": {"platform": "MACOS" if h.profile.platform == "ANDROID" else "ANDROID"},
        "operation_collision": {"operation_id": str(h.pair.ceremony_id)},
    }
    fields = mutation_fields[mutation]
    if mutation == "operation_collision":
        row = database_connection.execute(
            "SELECT operation_id FROM account.account_recovery_operation WHERE "
            "kind='CONFIGURE' LIMIT 1"
        ).fetchone()
        assert row is not None
        fields["operation_id"] = str(row[0])
        database_connection.commit()
    wire: Callable[..., bytes] = h.wire
    raw = wire("request", **fields)  # Sign AFTER the semantic mutation.
    before = snapshot(database_connection)
    assert h.send("request", raw=raw).status_code != 200
    assert snapshot(database_connection) == before
    assert h.ledger.request_read() == ()


@pytest.mark.parametrize(
    "invalidate", ["logout", "device", "rotation", "generation", "account", "expiry"]
)
def test_outcome_never_resurrects_invalidated_result(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], invalidate: str
) -> None:
    h = deletion_v2
    h.accept()
    reply = h.send("cancel")
    assert reply.status_code == 200
    result = reply.json()
    if invalidate == "expiry":
        h.profile.clock["now"] += timedelta(days=91)
    else:
        sql = {
            ("logout"): (
                "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=%s"
            ),
            "device": "UPDATE account.device SET revoked_at=clock_timestamp() WHERE device_id=%s",
            ("rotation"): (
                "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=%s"
            ),
            ("generation"): (
                "UPDATE account.account_recovery_credential SET "
                "generation=generation+1,verifier_sha256=pg_catalog.sha256(verifier_sha256),u"
                "pdated_at=clock_timestamp() WHERE user_id=%s"
            ),
            "account": "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s",
        }[invalidate]
        target = (
            result["device_id"]
            if invalidate == "device"
            else result["user_id"]
            if invalidate in {"generation", "account"}
            else result["session_id"]
        )
        database_connection.execute(sql, (target,))
        database_connection.commit()
    before = snapshot(database_connection)
    assert h.send("outcome").status_code != 200
    assert snapshot(database_connection) == before


def test_replacement_at_full_old_quota_and_delayed_final_binding_expiry(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = deletion_v2
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=1"
    )
    database_connection.commit()
    h.accept()
    reply = h.send("cancel")
    assert reply.status_code == 200
    before = snapshot(database_connection)
    deadline = datetime.fromisoformat(
        reply.json()["refresh_absolute_expires_at"].replace("Z", "+00:00")
    )

    def delayed_clock(session: Session) -> datetime:
        database_now(session)
        return deadline

    monkeypatch.setattr(
        "autplay.application.device_deletion_service_v2.database_now", delayed_clock
    )
    assert h.send("outcome").status_code != 200
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("other", ["cancel", "recovery", "purge"])
def test_independent_pg_race_is_bounded_and_retains_one_authority_branch(
    deletion_v2: DeletionHarness, database_connection: Connection[Any], other: str
) -> None:
    h = deletion_v2
    h.accept()
    raw = h.wire("cancel")
    application_name = "deletion_v2_race_" + uuid4().hex
    engine = create_engine(
        h.pair.engine.url,
        pool_size=3,
        max_overflow=0,
        connect_args={
            "application_name": application_name,
            "options": "-c lock_timeout=3000 -c statement_timeout=8000",
        },
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    configure_retained_deletion_reservations(
        sessions, ledger=h.ledger, evidence_ready=lambda: h.gates["evidence"]
    )
    recovery = AccountRecoveryService(
        sessions, h.recovery.access, h.recovery.ttl, h.recovery.secret
    )
    service = DeviceDeletionServiceV2(
        AccountDeletionService(recovery, h.ledger),
        identity=h.profile.identity,
        enabled=lambda: True,
        deletion_enabled=lambda: True,
        recovery_enabled=lambda: True,
        evidence_ready=lambda: True,
    )
    barrier = Barrier(2, timeout=4)
    stopping, registry_lock, state = Event(), Lock(), local()
    connections = []
    overall_deadline = monotonic() + 20

    def require_deadline(*args: object) -> None:
        if stopping.is_set() or monotonic() >= min(overall_deadline, state.deadline):
            raise TimeoutError("deletion private worker deadline")

    def remember(connection: Connection[Any], record: SAConnectionPoolEntry) -> None:
        with registry_lock:
            connections.append(connection)

    def observe(
        connection: SAConnection,
        cursor: SADBAPICursor,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        require_deadline()

    event.listen(engine, "connect", remember)
    event.listen(engine, "before_cursor_execute", observe)
    event.listen(sessions, "before_commit", require_deadline)

    def run(first: bool) -> dict[str, object] | DeletionEvidence:
        state.deadline = min(overall_deadline, monotonic() + 15)
        barrier.wait()
        if first or other == "cancel":
            return service.cancel(h.code, raw)
        if other == "purge":
            from autplay.application.privacy_deletion import PrivacyDeletionService

            return PrivacyDeletionService(recovery.sessions, h.ledger).purge(
                h.pair.actor.user_id, UUID(present(h.parent)["deletion_request_id"])
            )
        # Real recovery transaction must refuse DELETION_PENDING, never race it
        # into ACTIVE. Its own v2 original/domain is independently constructed.
        from .test_device_recovery_v2_migration import sign_history_original

        doc = json.loads(raw)
        doc.pop("deletion_request_id")
        doc.pop("expected_revision")
        doc.update(operation_id=str(uuid4()), binding_commit_id=str(uuid4()))
        wire = sign_history_original(doc, h.recipient)
        return DeviceRecoveryServiceV2(
            recovery, identity=h.profile.identity, enabled=lambda: True
        ).recover(h.code, wire)

    pool = ThreadPoolExecutor(max_workers=2)
    futures = []
    try:
        futures = [pool.submit(run, value) for value in (True, False)]
        done, pending = wait(futures, timeout=14)
        assert not pending
        outcomes: list[object] = []
        for future in done:
            try:
                outcomes.append(future.result())
            except (DBAPIError, DeletionEvidenceError, RuntimeError) as error:
                outcomes.append(error)
        assert any(isinstance(item, dict) for item in outcomes)
        state.deadline = overall_deadline
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM account.user_session WHERE user_id=:owner AND "
                        "revoked_at IS NULL"
                    ),
                    {"owner": h.pair.actor.user_id},
                )
                == 1
            )
    finally:
        stopping.set()
        barrier.abort()
        try:
            if any(not future.done() for future in futures):
                with registry_lock:
                    active = tuple(
                        connection for connection in connections if not connection.closed
                    )
                for owned_connection in active:
                    database_connection.execute(
                        "SELECT pg_terminate_backend(pid,5000) FROM pg_stat_activity "
                        "WHERE pid=%s AND datname=current_database() AND application_name=%s",
                        (owned_connection.info.backend_pid, application_name),
                    )
                database_connection.commit()
            _, pending = wait(futures, timeout=6)
            assert not pending, "private deletion workers did not stop"
        finally:
            event.remove(engine, "connect", remember)
            event.remove(engine, "before_cursor_execute", observe)
            event.remove(sessions, "before_commit", require_deadline)
            pool.shutdown(wait=False, cancel_futures=True)
            engine.dispose()


def portable_issuer(h: DeletionHarness, monkeypatch: pytest.MonkeyPatch) -> PortableHarness:
    """Use the real M5 owner and SAME guard/ledger, with a distinct portable owner."""
    from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
    from autplay.application.portable_account import PortableAccountService

    from .test_device_portable_v2 import PortableHarness
    from .test_portable_accounts import passport

    legacy = PortableAccountService(
        h.profile.identity._sessions,
        h.profile.identity._access,
        h.profile.identity._access_ttl,
        b"d1-portable-cross-family-test-secret",
    )
    service = DevicePortableServiceV2(
        legacy, identity=h.profile.identity, enabled=lambda: True, portable_enabled=lambda: True
    )
    monkeypatch.setattr("autplay.application.device_portable_service_v2._now", lambda: h.now())
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    return PortableHarness(
        service,
        legacy,
        h.profile.identity,
        h.profile.platform,
        {"v2": True, "portable": True},
        h.profile.clock,
        root,
        device,
        passport(root),
    )


@pytest.mark.parametrize("purpose", ["REGISTER", "LOGIN"])
def test_sealed_proof_without_request_blocks_valid_root_binding_and_positive_issues(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
) -> None:
    from .test_device_portable_v2 import original

    h, connection = deletion_v2, database_connection
    h.wire("request")
    h.profile.clock["now"] += timedelta(seconds=121)
    negative = h.send("resolve")
    assert negative.status_code == 200, negative.text
    reserved = json.loads(h.originals["request"])["operation_id"]
    assert connection.execute(
        (
            "SELECT count(*) FROM account.account_deletion_proof WHERE operation_id=%s "
            "AND decision='SEALED'"
        ),
        (reserved,),
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_request WHERE deletion_request_id=%s",
        (reserved,),
    ).fetchone() == (0,)
    connection.commit()
    portable = portable_issuer(h, monkeypatch)
    if purpose == "LOGIN":
        portable.register()  # Real grant, never malformed/missing ROOT authority.
    body, _, _ = portable.command(purpose)
    body["binding_commit_id"] = reserved
    raw = original(portable.root, body)  # Sign AFTER mutation, using the v2 ROOT domain.
    action = portable.service.register if purpose == "REGISTER" else portable.service.login
    before = snapshot(connection)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        action(raw, source="fixture-source")
    assert snapshot(connection) == before
    body["binding_commit_id"] = str(uuid4())
    result = action(original(portable.root, body), source="fixture-source")
    assert result["account_id"] == portable.document["account_id"]
    assert result["access_token"] and result["binding_commit_id"] == body["binding_commit_id"]
    with h.recovery.sessions.begin() as session:
        lock_resource_admission(session)
        # Claimed own SQL IDs must never hide independent SEALED evidence.
        from autplay.adapters.postgresql.retained_deletion_reservations import (
            DeletionReservationReplay,
        )

        sealed = next(item for item in h.ledger.request_read() if item.request_id == UUID(reserved))
        assert device_identifier_reserved(
            session,
            UUID(reserved),
            own_recovery_operation_id=UUID(reserved),
            own_deletion_request_id=UUID(reserved),
            own_portable_challenge_id=UUID(reserved),
            deletion_replay=DeletionReservationReplay(
                h.pair.actor.user_id, UUID(reserved), sealed.request_sha256, sealed.receipt_sha256
            ),
        )


@pytest.mark.parametrize(
    "failure", ["missing", "not_ready", "untrusted", "no_lock", "old_transaction"]
)
def test_shared_reservation_dependency_fails_closed_and_then_real_issuance_succeeds(
    deletion_v2: DeletionHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from autplay.adapters.postgresql.retained_deletion_reservations import (
        RETAINED_DELETION_RESERVATIONS_INFO_KEY,
    )

    h = deletion_v2
    original_read = h.ledger.request_read
    if failure == "not_ready":
        h.gates["evidence"] = False
    elif failure == "untrusted":

        def unavailable() -> None:
            raise DeletionEvidenceError()

        monkeypatch.setattr(h.ledger, "request_read", unavailable)
    with h.recovery.sessions() as session:
        if failure == "old_transaction":
            with session.begin():
                lock_resource_admission(session)
        with session.begin():
            if failure not in {"no_lock", "old_transaction"}:
                lock_resource_admission(session)
            if failure == "missing":
                session.info.pop(RETAINED_DELETION_RESERVATIONS_INFO_KEY)
            with pytest.raises(DeletionEvidenceError):
                device_identifier_reserved(session, uuid4())
    if failure == "untrusted":
        monkeypatch.setattr(h.ledger, "request_read", original_read)
    h.gates["evidence"] = True
    # A separate new factory session has the explicit real-ledger dependency.
    portable_issuer(h, monkeypatch).register()


@pytest.mark.parametrize("retained", ["ATTEMPTED", "SEALED", "CANCEL"])
def test_owner_purge_keeps_global_reservation_for_other_account_issuers(
    deletion_v2: DeletionHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    retained: str,
) -> None:
    from autplay.application.privacy_deletion import PrivacyDeletionService
    from autplay.domain.device_compatibility import DeviceCompatibilityError

    from .test_device_portable_v2 import original
    from .test_device_profile_v2 import ENROLL_DOMAIN, signed
    from .test_profile_pairing_m5b import _owner

    h, connection = deletion_v2, database_connection
    if retained == "SEALED":
        h.wire("request")
        h.profile.clock["now"] += timedelta(seconds=121)
        assert h.send("resolve").status_code == 200
        reserved = UUID(json.loads(h.originals.pop("request"))["operation_id"])
    h.profile.clock["now"] = datetime.now(UTC) - timedelta(days=31)
    if retained == "CANCEL":
        h.accept()
        cancelled = h.send("cancel")
        assert cancelled.status_code == 200, cancelled.text
        result = cancelled.json()
        reserved = UUID(result["operation_id"])
        # A later valid deletion of this restored account purges the old cancel
        # receipt too. Keep its independent operation UUID globally reserved.
        h.pair = replace(
            h.pair,
            actor=Principal(
                h.pair.actor.user_id,
                UUID(result["device_id"]),
                UUID(result["session_id"]),
                h.pair.actor.role,
            ),
            key=h.recipient,
        )
        h.code = h.next_code
        h.originals.clear()
        h.profile.clock["now"] += timedelta(seconds=1)
        h.originals["request"] = h.wire(
            "request", expected_code_generation=2, expected_authority_generation=3
        )
    h.accept()
    if retained == "ATTEMPTED":
        reserved = UUID(present(h.parent)["deletion_request_id"])
    h.profile.clock["now"] = datetime.now(UTC)
    PrivacyDeletionService(h.recovery.sessions, h.ledger).purge(
        h.pair.actor.user_id, UUID(present(h.parent)["deletion_request_id"])
    )
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_proof WHERE user_id=%s",
        (h.pair.actor.user_id,),
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT count(*) FROM account.account_deletion_request WHERE user_id=%s",
        (h.pair.actor.user_id,),
    ).fetchone() == (0,)
    connection.commit()
    assert any(
        reserved in {item.request_id, item.cancel_operation_id} for item in h.ledger.request_read()
    )
    with h.recovery.sessions.begin() as session:
        lock_resource_admission(session)
        assert device_identifier_reserved(session, reserved)
        assert device_v2_identifier_reserved(session, reserved)
    # Other owner's valid invitation and genuine P1363 original reach enrollment.
    other = replace(h.profile, owner=_owner(connection))
    pair = _legacy_pair(other, connection)  # Refresh actual other-owner authority/time.
    body, key, _ = other.enrollment()
    body["exchange_id"] = str(reserved)
    with pytest.raises(DeviceCompatibilityError, match="operation_conflict"):
        other.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
    body["exchange_id"] = str(uuid4())
    binding, replayed = other.service.enrollment_exchange(signed(body, key, ENROLL_DOMAIN))
    assert binding["access_token"] and replayed is False
    portable = portable_issuer(h, monkeypatch)
    body, _, _ = portable.command()
    body["binding_commit_id"] = str(reserved)
    with pytest.raises(ProfilePairingError, match="operation_conflict"):
        portable.service.register(original(portable.root, body))
    body["binding_commit_id"] = str(uuid4())
    assert portable.service.register(original(portable.root, body))["access_token"]
    from autplay.domain.account_recovery import AccountRecoveryError

    from .test_account_recovery import configured
    from .test_device_recovery_v2 import RecoveryHarness

    recovery, code, _ = configured(pair)
    recover = RecoveryHarness(
        other,
        pair,
        DeviceRecoveryServiceV2(recovery, identity=other.identity, enabled=lambda: True),
        recovery,
        code,
        new_code(),
        secrets.token_urlsafe(32),
        {"v2": True, "recovery": True},
    )
    raw = recover.wire("commit", operation_id=str(reserved))
    with pytest.raises(AccountRecoveryError, match="operation_conflict"):
        recover.service.recover(code, raw)
    assert recover.service.recover(code, recover.wire("commit"))["access_token"]

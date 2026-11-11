"""UNEXECUTED genuine PostgreSQL/HTTP recovery fixtures with real M5/proofs/codes.

Source authoring only. Exact replay/outcome sends cached original bytes, never
another ECDSA signature. Clocks below alter only the final DB-clock return after
the real SQL sample; all identity, code, proof and locked storage reads stay real.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, local
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.security.tokens import OpaqueRefreshTokenCodec
from autplay.adapters.system import Uuid7Generator
from autplay.application.account_recovery import AccountRecoveryService, database_now
from autplay.application.auth import AuthService
from autplay.application.device_recovery_service_v2 import DeviceRecoveryServiceV2
from autplay.domain import device_compatibility
from autplay.domain.account_recovery import AccountRecoveryError, code_verifier, new_code
from autplay.domain.auth import AccessTokenClaims, Principal
from autplay.domain.profile_pairing import ProfilePairingError, public_spki
from autplay.entrypoints.account_recovery_http import create_account_recovery_router
from autplay.entrypoints.auth_http import bearer_authentication
from autplay.entrypoints.device_recovery_v2_http import create_device_recovery_v2_router
from autplay.entrypoints.public_access_http import build_exact_proxy_source_resolver
from autplay.ports.auth import AccessTokenCodec
from autplay.runtime.http import install_error_handlers
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from sqlalchemy import Select, create_engine, event, func, select
from sqlalchemy.engine import Connection as SAConnection
from sqlalchemy.engine.interfaces import DBAPIConnection as SADBAPIConnection
from sqlalchemy.engine.interfaces import DBAPICursor as SADBAPICursor
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry as SAConnectionPoolEntry
from typed_assertions import present, require_instance

from .test_account_recovery import configured, seed_authority
from .test_account_recovery import request as v1_request
from .test_device_profile_v2 import ROTATE_DOMAIN, ProfileHarness, _legacy_pair, signed
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import _exchange_request
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_self_device_pairing import PairingHarness

PREFIX = "/api/v1/device-compatibility/v2/recovery"


@dataclass
class GrantProbe:
    delegate: AccessTokenCodec
    principals: list[Principal] = field(default_factory=list)

    def issue(
        self, principal: Principal, *, token_id: UUID, issued_at: datetime, expires_at: datetime
    ) -> str:
        self.principals.append(principal)
        return self.delegate.issue(
            principal, token_id=token_id, issued_at=issued_at, expires_at=expires_at
        )

    def decode(self, token: str, *, now: datetime) -> AccessTokenClaims:
        return self.delegate.decode(token, now=now)


def snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    result = tuple(
        connection.execute(statement).fetchall()
        for statement in (
            "SELECT * FROM account.user_account ORDER BY user_id",
            "SELECT * FROM account.device ORDER BY device_id",
            "SELECT * FROM account.user_session ORDER BY session_id",
            "SELECT * FROM account.account_recovery_credential ORDER BY user_id",
            "SELECT * FROM account.account_recovery_operation ORDER BY operation_id",
            "SELECT * FROM account.device_capability_revision ORDER BY 1,2,3,4",
            "SELECT * FROM audit.audit_event ORDER BY audit_event_id",
        )
    )
    connection.commit()
    return result


@dataclass
class RecoveryHarness:
    profile: ProfileHarness
    pair: PairingHarness
    service: DeviceRecoveryServiceV2
    v1: AccountRecoveryService
    code: str
    next_code: str
    refresh: str
    gates: dict[str, bool]
    originals: dict[str, bytes] = field(default_factory=dict)

    def now(self) -> datetime:
        return self.profile.clock["now"]

    def wire(self, kind: str, **changes: Any) -> bytes:
        if kind in self.originals and not changes:
            return self.originals[kind]
        key = public_spki(self.pair.key)
        body = {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": self.profile.platform,
            "operation_id": str(uuid4()),
            "account_id": str(self.pair.actor.user_id),
            "requested_at": self.now().isoformat().replace("+00:00", "Z"),
            **self.pair.identity,
            "device_public_key_spki_b64": base64.b64encode(key).decode(),
            "device_key_thumbprint_sha256": hashlib.sha256(key).hexdigest(),
            "device_name": "Synthetic recovered device",
            "app_version": "d1-recovery-fixture",
        }
        if kind == "commit":
            body.update(
                expected_code_generation=1,
                next_code_verifier_sha256=code_verifier(
                    UUID(self.pair.identity["expected_server_instance_id"]),
                    self.pair.actor.user_id,
                    self.next_code,
                ).hex(),
                next_refresh_token_sha256=hashlib.sha256(self.refresh.encode("ascii")).hexdigest(),
                binding_commit_id=str(uuid4()),
                confirmed_account_id=str(self.pair.actor.user_id),
            )
        body.update(changes)
        raw = signed(
            body,
            self.pair.key,
            "autplay:device-compatibility:recovery:"
            + ("recover" if kind == "commit" else "preview")
            + ":v2\n",
        )
        if not changes:
            self.originals[kind] = raw
        return raw

    def client(self, *, proxy: str | None = None, peer: str = "198.51.100.8") -> TestClient:
        auth = AuthService(
            SqlAlchemyAuthUnitOfWorkFactory(self.v1.sessions),
            self,
            Uuid7Generator(),
            self.v1.access,
            OpaqueRefreshTokenCodec(),
            self.v1.ttl,
            timedelta(days=90),
        )
        app = FastAPI()
        install_error_handlers(app)
        resolver = build_exact_proxy_source_resolver(proxy)
        app.include_router(
            create_device_recovery_v2_router(self.service, canonical_source=resolver),
            prefix="/api/v1",
        )
        app.include_router(
            create_account_recovery_router(
                self.v1, authenticated=bearer_authentication(auth), canonical_source=resolver
            ),
            prefix="/api/v1",
        )
        return TestClient(app, client=(peer, 45000))

    def send(self, kind: str, *, raw: bytes | None = None, secret: str | None = None) -> Response:
        original = raw if raw is not None else self.wire("commit" if kind == "outcome" else kind)
        header = "X-AutPlay-Recovery-Refresh" if kind == "outcome" else "X-AutPlay-Recovery-Code"
        value = secret if secret is not None else (self.refresh if kind == "outcome" else self.code)
        return self.client().post(
            PREFIX + "/" + kind,
            content=original,
            headers={"Content-Type": "application/json", header: value},
        )


@pytest.fixture
def recovery_v2(
    profile: ProfileHarness, database_connection: Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> RecoveryHarness:
    pair = _legacy_pair(profile, database_connection)
    recovery, code, _ = configured(pair)
    profile.clock["now"] = datetime.now(UTC)  # after the real configure clock/credential write
    gates = {"v2": True, "recovery": True}
    service = DeviceRecoveryServiceV2(
        recovery, identity=profile.identity, enabled=lambda: gates["v2"] and gates["recovery"]
    )
    h = RecoveryHarness(
        profile, pair, service, recovery, code, new_code(), secrets.token_urlsafe(32), gates
    )

    def final_clock(session: Session) -> datetime:
        database_now(session)  # REAL last SQL query; simulated time only changes its return.
        return h.now()

    monkeypatch.setattr("autplay.application.device_recovery_service_v2.database_now", final_clock)
    return h


def test_preview_commit_original_replay_and_outcome_after_one_day(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_device_qualification(monkeypatch)
    h = recovery_v2
    # Old ANDROID authority has a distinct real key; it must not contradict a
    # legitimate new MACOS recipient before the actual recovery proof is used.
    seed_authority(replace(h.pair, key=ec.generate_private_key(ec.SECP256R1())))
    h.profile.clock["now"] = datetime.now(UTC)
    preview = h.send("preview")
    assert preview.status_code == 200 and preview.json()["role"] == h.pair.actor.role.value
    assert preview.json()["confirmation_required"] is True
    original = h.wire("commit")
    result = h.send("commit", raw=original)
    assert result.status_code == 200 and result.json()["platform"] == h.profile.platform
    result = result.json()
    initial = snapshot(database_connection)
    replay = h.send("commit", raw=original)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert (replay.json()["device_id"], replay.json()["session_id"]) == (
        result["device_id"],
        result["session_id"],
    )
    h.profile.clock["now"] += timedelta(days=2)
    assert h.send("commit", raw=original).status_code == 403
    h.gates.update(v2=False, recovery=False)
    outcome = h.send("outcome", raw=original)
    assert outcome.status_code == 200 and outcome.json()["outcome_recovered"] is True
    assert outcome.json()["platform"] == h.profile.platform
    auth = AuthService(
        SqlAlchemyAuthUnitOfWorkFactory(h.v1.sessions),
        h,
        Uuid7Generator(),
        h.v1.access,
        OpaqueRefreshTokenCodec(),
        h.v1.ttl,
        timedelta(days=90),
    )
    principal = auth.authenticate_access(outcome.json()["access_token"])
    assert principal.role == h.pair.actor.role
    assert (
        snapshot(database_connection) == initial
    )  # Grants have no persisted response copy/effect.
    stored = database_connection.execute(
        "SELECT original_request_document,original_document_sha256,reque"
        "st_sha256,signature_algorithm,proof_domain "
        "FROM account.account_recovery_operation WHERE operation_id=%s",
        (json.loads(original)["operation_id"],),
    ).fetchone()
    assert stored is not None and stored[0] == json.loads(original)
    assert bytes(stored[1]) == hashlib.sha256(original).digest()
    assert bytes(stored[2]).hex() == json.loads(original)["request_sha256"]
    assert stored[3:] == ("ES256-P1363", "autplay:device-compatibility:recovery:recover:v2\n")
    assert h.code not in json.dumps(stored[0]) and h.refresh not in json.dumps(stored[0])
    assert not device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES


@pytest.mark.parametrize("gate", ["v2", "recovery"])
def test_independent_flags_refuse_first_effect_and_preserve_exact_continuations(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    gate: str,
) -> None:
    h = recovery_v2
    original = h.wire("commit")
    before = snapshot(database_connection)
    h.gates[gate] = False
    assert h.send("preview").status_code == 503
    assert h.send("commit", raw=original).status_code == 503
    assert snapshot(database_connection) == before
    h.gates[gate] = True
    assert h.send("commit", raw=original).status_code == 200
    h.gates[gate] = False
    assert h.send("commit", raw=original).status_code == 200
    assert h.send("outcome", raw=original).status_code == 200


@pytest.mark.parametrize(
    "mutation",
    [
        "logout",
        "rotate",
        "superseded",
        "expired",
        "blocked",
        "disabled",
        "wrong_refresh",
        "changed_signature",
    ],
)
@pytest.mark.usefixtures("replacement_owner")
def test_outcome_never_resurrects_or_partially_mutates(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    mutation: str,
) -> None:
    h = recovery_v2
    original = h.wire("commit")
    binding = h.send("commit", raw=original).json()
    raw, secret = original, h.refresh
    if mutation == "rotate":
        rotate, _ = h.profile.rotation(binding, json.loads(original), h.refresh)
        h.profile.service.session_rotate(signed(rotate, h.pair.key, ROTATE_DOMAIN))
    elif mutation == "superseded":
        actor = Principal(
            h.pair.actor.user_id,
            UUID(binding["device_id"]),
            UUID(binding["session_id"]),
            h.pair.actor.role,
        )
        h.v1.configure(
            actor,
            v1_request(
                h.pair,
                "configure",
                expected_code_generation=2,
                next_code_verifier_sha256=code_verifier(
                    UUID(h.pair.identity["expected_server_instance_id"]), actor.user_id, new_code()
                ).hex(),
            ),
        )
    elif mutation == "wrong_refresh":
        secret = secrets.token_urlsafe(32)
    elif mutation == "changed_signature":
        # Same D, different valid ECDSA signature: full original must still differ.
        raw = signed(
            json.loads(original), h.pair.key, "autplay:device-compatibility:recovery:recover:v2\n"
        )
    elif mutation == "expired":
        h.profile.clock["now"] += timedelta(days=91)
    else:
        if mutation == "logout":
            database_connection.execute(
                "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=%s",
                (binding["session_id"],),
            )
        elif mutation == "disabled":
            database_connection.execute(
                "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s",
                (h.pair.actor.user_id,),
            )
        else:
            database_connection.execute(
                (
                    "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) "
                    "VALUES(%s,%s)"
                ),
                (h.pair.actor.user_id, hashlib.sha256(public_spki(h.pair.key)).digest()),
            )
        database_connection.commit()
    before = snapshot(database_connection)
    grants = GrantProbe(h.v1.access)
    h.v1.access = grants
    assert h.send("outcome", raw=raw, secret=secret).status_code in {401, 403, 409}
    assert not grants.principals
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("first", ["v1", "v2"])
@pytest.mark.parametrize("reserved", ["operation_id", "binding_commit_id"])
@pytest.mark.parametrize("incoming", ["exchange_id", "binding_commit_id"])
def test_cross_family_reservations_both_commit_orders_with_valid_proofs(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    first: str,
    reserved: str,
    incoming: str,
) -> None:
    h = recovery_v2
    # A second account's valid enrollment avoids recovery revoking the competing
    # invitation. Distinct real keys/proofs and valid codes expose the collision.
    other = present(
        database_connection.execute(
            "INSERT INTO account.user_account(display_name) VALUES('other synthetic "
            "owner') RETURNING user_id"
        ).fetchone()
    )[0]
    database_connection.commit()
    invitation = h.profile.identity.issue_recovery_invitation(other, uuid4(), 600)
    old, old_key, _ = _exchange_request(invitation)
    new = json.loads(h.wire("commit"))
    old[incoming] = new[reserved]
    old = json.loads(signed(old, old_key, "AutPlay enrollment exchange v1\n"))
    raw = h.originals["commit"]
    if first == "v1":
        h.profile.identity.exchange(old)
        before = snapshot(database_connection)
        response = h.send("commit", raw=raw)
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "operation_conflict"
        )
    else:
        assert h.send("commit", raw=raw).status_code == 200
        before = snapshot(database_connection)
        with pytest.raises(ProfilePairingError):
            h.profile.identity.exchange(old)
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("operation", ["preview", "commit", "outcome"])
def test_identity_owner_callback_failure_is_safe_503(
    recovery_v2: RecoveryHarness, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    h = recovery_v2
    if operation == "outcome":
        assert h.send("commit").status_code == 200

    def mismatch(session: Session) -> None:
        raise RuntimeError("synthetic private key/origin mismatch must never appear")

    monkeypatch.setattr(h.profile.identity, "current_device_contract_identity", mismatch)
    reply = h.send(operation)
    assert reply.status_code == 503 and "private key" not in reply.text


@pytest.mark.parametrize("deadline", ["freshness", "result_session", "receipt"])
def test_final_clock_is_after_last_blocking_read_and_before_first_effect_or_grant(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    deadline: str,
) -> None:
    h = recovery_v2
    original = h.wire("commit")
    if deadline != "freshness":
        assert h.send("commit", raw=original).status_code == 200
    before = snapshot(database_connection)
    sampled, sql_after_clock = [], []
    first_effect: list[object] = []
    grants = GrantProbe(h.v1.access)
    h.v1.access = grants
    future = (
        h.now()
        + (
            {
                "freshness": timedelta(seconds=121),
                "result_session": timedelta(days=91),
                "receipt": timedelta(days=2),
            }[deadline]
        )
    )

    def final_clock(session: Session) -> datetime:
        database_now(session)  # last query really executes before the time advance
        sampled.append(True)
        return future

    def observe(
        connection: SAConnection,
        cursor: SADBAPICursor,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if sampled and not first_effect:
            sql_after_clock.append(statement.split(None, 1)[0].upper())
            if sql_after_clock[-1] in {"INSERT", "UPDATE", "DELETE"}:
                first_effect.append(True)

    monkeypatch.setattr("autplay.application.device_recovery_service_v2.database_now", final_clock)
    event.listen(h.pair.engine, "before_cursor_execute", observe)
    try:
        reply = h.send("outcome" if deadline == "result_session" else "commit", raw=original)
    finally:
        event.remove(h.pair.engine, "before_cursor_execute", observe)
    assert sampled and reply.status_code == 403
    assert not sql_after_clock and not first_effect and not grants.principals
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("operation", ["preview", "commit", "outcome"])
def test_success_path_has_no_storage_read_after_final_clock(
    recovery_v2: RecoveryHarness,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    h = recovery_v2
    if operation == "outcome":
        assert h.send("commit").status_code == 200
    sampled: list[bool] = []
    verbs: list[str] = []
    grant_clock_reads: list[bool] = []
    grants = GrantProbe(h.v1.access)
    h.v1.access = grants

    def final_clock(session: Session) -> datetime:
        database_now(session)
        sampled.append(True)
        return h.now()

    def observe(
        connection: SAConnection,
        cursor: SADBAPICursor,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if sampled:
            query = getattr(getattr(context, "compiled", None), "statement", None)
            if isinstance(query, Select) and query.compare(select(func.clock_timestamp())):
                # Commit samples again after its writes, immediately before
                # granting access. Only that exact clock-only query is allowed.
                assert operation == "commit" and verbs and verbs[0] == "UPDATE"
                assert len(sampled) == 1 and not grant_clock_reads
                grant_clock_reads.append(True)
                return
            verbs.append(statement.split(None, 1)[0].upper())

    monkeypatch.setattr("autplay.application.device_recovery_service_v2.database_now", final_clock)
    event.listen(h.pair.engine, "before_cursor_execute", observe)
    try:
        response = h.send(operation)
    finally:
        event.remove(h.pair.engine, "before_cursor_execute", observe)
    assert response.status_code == 200 and sampled
    assert not any(verb in {"SELECT", "WITH"} for verb in verbs)
    if operation == "commit":
        assert verbs and verbs[0] == "UPDATE"
        assert len(sampled) == 2 and grant_clock_reads == [True]
    else:
        assert not verbs and len(sampled) == 1 and not grant_clock_reads
    assert len(grants.principals) == (0 if operation == "preview" else 1)


@pytest.mark.parametrize("scope,maximum", [("global", 3000), ("source", 60), ("account", 30)])
def test_v1_shared_rate_values_and_exact_proxy_source_across_both_routers(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    scope: str,
    maximum: int,
) -> None:
    h = recovery_v2
    source = "198.51.100.23"
    h.v1.rate_gate(source)
    h.v1.rate_gate("", h.pair.actor.user_id)
    label = {
        "global": b"global",
        "source": b"source\x00" + source.encode(),
        "account": b"account\x00" + h.pair.actor.user_id.bytes,
    }[scope]
    key = hmac.digest(h.v1.secret, b"account-recovery-rate:v1\x00" + label, "sha256")
    row = database_connection.execute(
        (
            "SELECT count,expires_at-clock_timestamp() FROM account.self_pairing_rate "
            "WHERE key_hash=%s"
        ),
        (key,),
    ).fetchone()
    assert (
        row is not None and row[0] == 1 and timedelta(minutes=14) < row[1] <= timedelta(minutes=15)
    )
    database_connection.execute(
        "UPDATE account.self_pairing_rate SET count=%s WHERE key_hash=%s", (maximum - 1, key)
    )
    database_connection.commit()
    client = h.client(proxy="127.0.0.1", peer="127.0.0.1")
    headers = {"X-AutPlay-Recovery-Code": h.code, "X-AutPlay-Client-IP": "::ffff:198.51.100.23"}
    old = client.post(
        "/api/v1/recovery/preview", json=v1_request(h.pair, "preview"), headers=headers
    )
    assert old.status_code == 200  # Real v1 verifier/proof/current account stays positive.
    headers.update({"Content-Type": "application/json", "X-AutPlay-Client-IP": source})
    new = client.post(PREFIX + "/preview", content=h.wire("preview"), headers=headers)
    assert new.status_code == 429 and new.headers["Retry-After"] == "900"
    assert new.json()["error"]["code"] == "account_recovery_rate_limited"
    assert database_connection.execute(
        "SELECT count FROM account.self_pairing_rate WHERE key_hash=%s", (key,)
    ).fetchone() == (maximum + 1,)


def test_replacement_quota_uses_planned_revocations(
    recovery_v2: RecoveryHarness, database_connection: Connection[Any]
) -> None:
    h = recovery_v2
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET revision=revision+1,default_devices=1"
    )
    database_connection.commit()
    assert (
        present(
            database_connection.execute(
                "SELECT count(*) FROM account.device WHERE user_id=%s AND revoked_at IS NULL",
                (h.pair.actor.user_id,),
            ).fetchone()
        )[0]
        > 1
    )
    database_connection.commit()
    assert h.send("commit").status_code == 200
    assert database_connection.execute(
        "SELECT count(*) FROM account.device WHERE user_id=%s AND revoked_at IS NULL",
        (h.pair.actor.user_id,),
    ).fetchone() == (1,)


def test_current_code_generation_conflict_has_valid_current_code_and_proof(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
) -> None:
    h = recovery_v2
    raw = h.wire("commit")  # Original expects generation one.
    current_code = new_code()
    h.v1.configure(
        h.pair.actor,
        v1_request(
            h.pair,
            "configure",
            expected_code_generation=1,
            next_code_verifier_sha256=code_verifier(
                UUID(h.pair.identity["expected_server_instance_id"]),
                h.pair.actor.user_id,
                current_code,
            ).hex(),
        ),
    )
    before = snapshot(database_connection)
    reply = h.send("commit", raw=raw, secret=current_code)
    assert (
        reply.status_code == 409 and reply.json()["error"]["code"] == "recovery_generation_conflict"
    )
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("thumbprint", ["valid", "null", "wrong"])
def test_global_revoked_other_owner_raw_spki_platform_conflict_is_not_hidden(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    thumbprint: str,
) -> None:
    h = recovery_v2
    other = present(
        database_connection.execute(
            "INSERT INTO account.user_account(display_name) VALUES('historical synthetic "
            "owner') RETURNING user_id"
        ).fetchone()
    )[0]
    key = public_spki(h.pair.key)
    thumb = {
        "valid": hashlib.sha256(key).digest(),
        "null": None,
        "wrong": hashlib.sha256(public_spki(ec.generate_private_key(ec.SECP256R1()))).digest(),
    }[thumbprint]
    database_connection.execute(
        "INSERT INTO account.device(user_id,device_name,platform,app_ver"
        "sion,public_key,public_key_thumbprint_sha256,revoked_at) "
        "VALUES(%s,'historical synthetic key',%s,'fixture',%s,%s,clock_timestamp())",
        (other, "ANDROID" if h.profile.platform == "MACOS" else "MACOS", key, thumb),
    )
    database_connection.commit()
    before = snapshot(database_connection)
    reply = h.send("commit")
    assert reply.status_code == 409 and reply.json()["error"]["code"] == "device_platform_conflict"
    assert snapshot(database_connection) == before


@pytest.mark.parametrize("field", ["operation_id", "binding_commit_id"])
def test_v1_nonissuer_configure_and_recovery_cannot_dispatch_a_v2_receipt(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
    field: str,
) -> None:
    h = recovery_v2
    original = h.wire("commit")
    binding = h.send("commit", raw=original).json()
    actor = Principal(
        h.pair.actor.user_id,
        UUID(binding["device_id"]),
        UUID(binding["session_id"]),
        h.pair.actor.role,
    )
    reserved = json.loads(original)[field]
    configure = v1_request(
        h.pair,
        "configure",
        operation_id=reserved,
        expected_code_generation=2,
        next_code_verifier_sha256=code_verifier(
            UUID(h.pair.identity["expected_server_instance_id"]), actor.user_id, new_code()
        ).hex(),
    )
    # Recovery candidate has its own valid ANDROID proof and CURRENT real code.
    different = PairingHarness(
        h.pair.engine,
        h.pair.service,
        actor,
        h.pair.identity,
        uuid4(),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        ec.generate_private_key(ec.SECP256R1()),
    )
    recover = v1_request(
        different,
        "recover",
        operation_id=reserved,
        expected_code_generation=2,
        confirmed_account_id=str(actor.user_id),
        binding_commit_id=str(uuid4()),
        next_code_verifier_sha256=code_verifier(
            UUID(h.pair.identity["expected_server_instance_id"]), actor.user_id, new_code()
        ).hex(),
        next_refresh_token_sha256=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
    )
    before = snapshot(database_connection)
    with pytest.raises(AccountRecoveryError, match="operation_conflict"):
        h.v1.configure(actor, configure)
    with pytest.raises(AccountRecoveryError, match="operation_conflict"):
        h.v1.recover(h.next_code, recover)
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        h.profile.identity.issue_invitation(actor, UUID(reserved), 600)
    assert snapshot(database_connection) == before


def test_duplicate_secret_headers_cannot_create_or_reissue(
    recovery_v2: RecoveryHarness, database_connection: Connection[Any]
) -> None:
    h = recovery_v2
    raw = h.wire("commit")
    before = snapshot(database_connection)
    headers = [
        ("Content-Type", "application/json"),
        ("X-AutPlay-Recovery-Code", h.code),
        ("X-AutPlay-Recovery-Code", h.code),
    ]
    assert h.client().post(PREFIX + "/commit", content=raw, headers=headers).status_code == 403
    assert snapshot(database_connection) == before
    assert h.send("commit", raw=raw).status_code == 200
    before = snapshot(database_connection)
    headers = [
        ("Content-Type", "application/json"),
        ("X-AutPlay-Recovery-Refresh", h.refresh),
        ("X-AutPlay-Recovery-Refresh", h.refresh),
    ]
    assert h.client().post(PREFIX + "/outcome", content=raw, headers=headers).status_code == 403
    assert snapshot(database_connection) == before


def test_real_lock_race_has_private_timeouts_deadlines_and_cleanup(
    recovery_v2: RecoveryHarness,
    database_connection: Connection[Any],
) -> None:
    h = recovery_v2
    # PRIVATE engine; timeouts apply to the worker's actual SQLAlchemy sessions.
    application_name = "recovery_v2_race_" + uuid4().hex
    engine = create_engine(
        h.pair.engine.url,
        connect_args={
            "application_name": application_name,
            "options": "-c lock_timeout=1500ms -c statement_timeout=5000ms",
        },
    )
    sessions = sessionmaker(
        engine,
        class_=Session,
        expire_on_commit=False,
        info=h.profile.identity._sessions.kw["info"],
    )
    delegate = AccountRecoveryService(sessions, h.v1.access, h.v1.ttl, h.v1.secret)
    service = DeviceRecoveryServiceV2(delegate, identity=h.profile.identity, enabled=lambda: True)
    entered, stopping, registry_lock, state = Event(), Event(), Lock(), local()
    connections, futures = [], []
    overall_deadline = monotonic() + 20
    original = h.wire("commit")
    operation_id = json.loads(original)["operation_id"]

    def require_deadline(*args: object) -> None:
        if stopping.is_set() or monotonic() >= min(overall_deadline, state.deadline):
            raise TimeoutError("recovery private worker deadline")

    def remember(connection: SADBAPIConnection, record: SAConnectionPoolEntry) -> None:
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
        if "FOR UPDATE" in statement.upper() and "account_recovery_credential" in statement:
            entered.set()

    def recover() -> dict[str, object]:
        state.deadline = min(overall_deadline, monotonic() + 15)
        try:
            return service.recover(h.code, original)
        except BaseException:
            stopping.set()
            raise

    event.listen(engine, "connect", remember)
    event.listen(engine, "before_cursor_execute", observe)
    event.listen(sessions, "before_commit", require_deadline)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        with h.v1.sessions.begin() as blocker:
            blocker.scalar(
                select(AccountRecoveryCredentialRow)
                .where(AccountRecoveryCredentialRow.user_id == h.pair.actor.user_id)
                .with_for_update()
            )
            first = pool.submit(recover)
            futures.append(first)
            assert entered.wait(2)
            second = pool.submit(recover)
            futures.append(second)
        done, pending = wait([first, second], timeout=12)
        assert not pending and len(done) == 2
        results = [future.result(timeout=1) for future in (first, second)]
        assert len({result["session_id"] for result in results}) == 1
        assert sorted(require_instance(result["replayed"], bool) for result in results) == [
            False,
            True,
        ]
        assert database_connection.execute(
            "SELECT count(*) FROM account.account_recovery_operation WHERE operation_id=%s",
            (operation_id,),
        ).fetchone() == (1,)
    finally:
        stopping.set()
        try:
            if any(not future.done() for future in futures):
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
            _, pending = wait(futures, timeout=6)
            assert not pending, "private recovery workers did not stop"
        finally:
            event.remove(engine, "connect", remember)
            event.remove(engine, "before_cursor_execute", observe)
            event.remove(sessions, "before_commit", require_deadline)
            pool.shutdown(wait=False, cancel_futures=True)
            engine.dispose()

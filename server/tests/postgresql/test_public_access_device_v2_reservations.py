"""UNEXECUTED PA2/v2 ID regressions on guarded disposable PostgreSQL fixtures.

All signatures, imports, SQL, tokens and assertions below are future actions.
Rate attempts deliberately commit on rejection; authority/receipt snapshots
exclude those counters. No source, crypto or database qualification is claimed.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
from autplay.application.portable_account import PortableAccountService
from autplay.application.public_access import PublicAccessError, PublicAccessService
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.profile_pairing import ProfilePairingError, canonical_sha256, sign_p1363
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from typed_assertions import require_instance

from .test_device_admission_s1b import _Clock, _Runtime, _web_actor
from .test_device_admission_v2 import AdmissionHarness, proof
from .test_device_portable_v2 import PortableHarness, original
from .test_device_profile_v2 import ENROLL_DOMAIN, ProfileHarness, signed
from .test_device_profile_v2 import profile as profile
from .test_device_profile_v2 import snapshot as profile_snapshot
from .test_portable_accounts import passport
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_public_access_pa2 import _request


@dataclass
class Registration:
    body: dict[str, Any]
    key: ec.EllipticCurvePrivateKey
    service: PublicAccessService

    def signed(self) -> dict[str, Any]:
        digest = canonical_sha256(
            self.body, omit=frozenset({"request_sha256", "device_signature_b64url"})
        )
        self.body["request_sha256"] = digest.hex()
        self.body["device_signature_b64url"] = sign_p1363(
            self.key, "AutPlay account registration v1\n", digest
        )
        return dict(self.body)

    def apply(self) -> tuple[dict[str, object], bool]:
        return self.service.redeem(self.signed(), "pa2-v2-synthetic-source")


@dataclass
class Command:
    body: dict[str, Any]
    key: ec.EllipticCurvePrivateKey
    signer: Callable[[dict[str, Any], ec.EllipticCurvePrivateKey], bytes]
    invoke: Callable[[bytes], Any]
    _signed_versions: dict[bytes, bytes] = field(default_factory=dict, init=False, repr=False)

    def wire(self) -> bytes:
        # Freeze each finally formed command once. ECDSA may choose a new nonce;
        # an exact replay must instead reuse the complete original signature.
        # Sign a separate tree because the enrollment helper fills proof members.
        canonical = rfc8785.dumps(self.body)
        if canonical not in self._signed_versions:
            self._signed_versions[canonical] = self.signer(json.loads(canonical), self.key)
        return self._signed_versions[canonical]

    def apply(self) -> tuple[dict[str, Any], bool | None]:
        result = self.invoke(self.wire())
        return result if isinstance(result, tuple) else (result, None)


@dataclass
class Harness:
    profile: ProfileHarness
    pa2: PublicAccessService
    admission: AdmissionHarness
    grants: list[object]

    def create_body(self) -> dict[str, Any]:
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "account_display_name": "PA2 synthetic friend",
            "expires_in_seconds": 600,
        }

    def registration(self) -> Registration:
        invitation, replayed = self.pa2.create_invitation(self.profile.owner, self.create_body())
        assert replayed is False
        key = ec.generate_private_key(ec.SECP256R1())
        body = dict(_request(invitation, key))
        body["next_refresh_token_sha256"] = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        return Registration(body, key, self.pa2)


@pytest.fixture
def pa2_v2(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Harness:
    from .controlled_clock import control_database_clock

    control_database_clock(
        monkeypatch,
        lambda: profile.clock["now"],
        "autplay.application.device_admission_service_v2",
        "autplay.application.device_portable_service_v2",
        "autplay.application.public_access",
    )
    # OWNER authority must remain genuinely live at the database's clock too.
    database_connection.execute(
        "UPDATE account.user_session SET issued_at=clock_timestamp(),"
        "last_rotated_at=clock_timestamp(),expires_at=clock_timestamp()+interval '90 days' "
        "WHERE session_id=%s",
        (profile.owner.session_id,),
    )
    database_connection.commit()
    monkeypatch.setattr("autplay.application.public_access._now", lambda: profile.clock["now"])
    monkeypatch.setattr(
        "autplay.application.device_admission_service_v2._now", lambda: profile.clock["now"]
    )
    monkeypatch.setattr(
        "autplay.application.device_portable_service_v2._now", lambda: profile.clock["now"]
    )
    monkeypatch.setattr(profile.identity, "_device_v2_enabled", lambda: profile.gate["enabled"])
    runtime = _Runtime(profile.identity, profile.identity._sessions, _Clock(profile.clock["now"]))
    actor = _web_actor(runtime, database_connection, user_id=profile.owner.user_id)
    admission = AdmissionHarness(
        runtime,
        DeviceAdmissionServiceV2(
            runtime.sessions,
            identity=profile.identity,
            enabled=lambda family: profile.gate["enabled"],
        ),
        profile.gate,
        profile.platform,
        actor,
    )
    pa2 = PublicAccessService(
        runtime.sessions,
        require_instance(profile.identity._access, Hs256AccessTokenCodec),
        profile.identity._access_ttl,
        b"pa2-v2-reservations-synthetic-source-secret-32bytes",
    )
    grants: list[object] = []
    issue = Hs256AccessTokenCodec.issue

    def counted(codec: Hs256AccessTokenCodec, *args: Any, **kwargs: Any) -> str:
        grants.append(args)
        return issue(codec, *args, **kwargs)

    monkeypatch.setattr(Hs256AccessTokenCodec, "issue", counted)
    return Harness(profile, pa2, admission, grants)


def snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    authority = profile_snapshot(connection)
    pa2 = tuple(
        connection.execute(statement).fetchall()
        for statement in (
            "SELECT * FROM account.account_invitation ORDER BY invitation_id",
            "SELECT * FROM account.account_registration_receipt ORDER BY registration_id",
            "SELECT * FROM account.account_provisioning_link ORDER BY user_id",
            "SELECT * FROM account.account_provisioning_operation_receipt ORDER BY operation_id",
        )
    )
    connection.commit()
    return authority + pa2


def denied(
    h: Harness,
    connection: Connection[Any],
    action: Callable[[], Any],
    error: type[Exception] | tuple[type[Exception], ...],
    code: str,
) -> None:
    before, grants = snapshot(connection), len(h.grants)
    with pytest.raises(error, match=code):
        action()
    assert len(h.grants) == grants
    assert snapshot(connection) == before


def portable(h: Harness) -> PortableHarness:
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    legacy = PortableAccountService(
        h.profile.identity._sessions,
        require_instance(h.profile.identity._access, Hs256AccessTokenCodec),
        h.profile.identity._access_ttl,
        b"pa2-portable-v2-synthetic-source-secret-32bytes",
    )
    service = DevicePortableServiceV2(
        legacy, identity=h.profile.identity, enabled=lambda: True, portable_enabled=lambda: True
    )
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


def modern(h: Harness, family: str) -> Command:
    if family == "enrollment":
        body, key, _ = h.profile.enrollment()
        return Command(
            body,
            key,
            lambda body, key: signed(body, key, ENROLL_DOMAIN),
            h.profile.service.enrollment_exchange,
        )
    if family == "portable":
        runtime = portable(h)
        body, _, _ = runtime.command()
        return Command(
            body, runtime.root, lambda body, key: original(key, body), runtime.service.register
        )
    a = h.admission.submit()
    if family == "admission":
        h.admission.approve(a)
        return Command(
            h.admission.exchange(a),
            a.key,
            lambda body, key: proof(body, key, "admission.exchange"),
            lambda raw: h.admission.service.admission_exchange(
                raw, a.body["request_id"], a.created["poll_bearer"]
            ),
        )
    h.admission.bind(a)
    challenge = h.admission.service.trusted_challenge(
        proof(h.admission.challenge_body(a), a.key, "trusted.challenge")
    )
    return Command(
        h.admission.trusted_body(a, challenge),
        a.key,
        lambda body, key: proof(body, key, "trusted.exchange"),
        h.admission.service.trusted_exchange,
    )


@pytest.mark.parametrize(
    "family,v2_field",
    [
        ("admission", "exchange_id"),
        ("admission", "binding_commit_id"),
        ("trusted", "exchange_id"),
        ("trusted", "binding_commit_id"),
        ("enrollment", "exchange_id"),
        ("enrollment", "binding_commit_id"),
        ("portable", "binding_commit_id"),
    ],
)
@pytest.mark.parametrize("first", ["pa2", "v2"])
@pytest.mark.parametrize("pa2_field", ["registration_id", "binding_commit_id"])
def test_legitimate_different_key_commits_reserve_ids_in_both_orders_and_replay_own_receipts(
    pa2_v2: Harness,
    database_connection: Connection[Any],
    family: str,
    first: str,
    pa2_field: str,
    v2_field: str,
) -> None:
    h, registration = pa2_v2, pa2_v2.registration()
    command = modern(h, family)
    field = v2_field
    reserved = command.body[field]
    # The PA2 key is independently generated; a platform-conflict refusal cannot
    # stand in for an identifier guard. ROOT/device evidence is separate in portable.
    if family == "portable":
        assert (
            registration.body["device_public_key_spki_b64"]
            != command.body["device_public_key_spki_b64"]
        )
    else:
        assert (
            registration.body["device_key_thumbprint_sha256"]
            != command.body["device_key_thumbprint_sha256"]
        )
    if first == "v2":
        original_wire = command.wire()
        winner, _ = command.apply()
        registration.body[pa2_field] = reserved
        denied(
            h, database_connection, registration.apply, PublicAccessError, "registration_conflict"
        )
        before = snapshot(database_connection)
        assert command.wire() == original_wire
        replay, replayed = command.apply()
        assert replayed is True or family == "portable"
        assert replay["session_id"] == winner["session_id"]
        assert snapshot(database_connection) == before
        # Same invitation, independent key and valid proof do succeed once only
        # the conflicting identifier is replaced. No invalid-proof-only fixture.
        registration.body[pa2_field] = str(uuid4())
        assert registration.apply()[1] is False
    else:
        winner, _ = registration.apply()
        command.body[field] = registration.body[pa2_field]
        denied(
            h,
            database_connection,
            command.apply,
            (ProfilePairingError, DeviceCompatibilityError),
            "operation_conflict",
        )
        before = snapshot(database_connection)
        replay, replayed = registration.apply()  # fresh legitimate P1363 proof
        assert replayed is True and replay["session_id"] == winner["session_id"]
        assert replay["access_token"] != winner["access_token"]
        assert snapshot(database_connection) == before
        changed = dict(registration.body)
        registration.body["device_name"] = "Changed but legitimately signed PA2 retry"
        denied(
            h, database_connection, registration.apply, PublicAccessError, "registration_conflict"
        )
        registration.body = changed
        command.body[field] = str(uuid4())
        assert command.apply()[0]["session_id"] != winner["session_id"]


@pytest.mark.parametrize("family", ["admission", "trusted", "enrollment", "portable"])
@pytest.mark.parametrize("expired", [False, True])
def test_historical_pa2_binding_collision_blocks_reissues_before_grant_or_terminal_effects(
    pa2_v2: Harness,
    database_connection: Connection[Any],
    family: str,
    expired: bool,
) -> None:
    h, registration = pa2_v2, pa2_v2.registration()
    old_result, _ = registration.apply()
    command = modern(h, family)
    original_wire = command.wire()
    new_result, _ = command.apply()
    old_body = dict(registration.body)
    registration.body["binding_commit_id"] = command.body["binding_commit_id"]
    historical = registration.signed()
    # Simulate a legacy receipt retained from before bidirectional guards, using
    # a genuinely committed PA2 account/session and a matching valid original.
    # Never rewrite or disable v2 evidence/immutability for this fixture.
    database_connection.execute(
        "UPDATE account.account_registration_receipt SET binding_commit_id=%s,request_sha256=%s "
        "WHERE registration_id=%s",
        (
            UUID(historical["binding_commit_id"]),
            bytes.fromhex(historical["request_sha256"]),
            UUID(historical["registration_id"]),
        ),
    )
    database_connection.commit()
    if expired:
        h.profile.clock["now"] += timedelta(days=90, seconds=301)
    denied(h, database_connection, registration.apply, PublicAccessError, "registration_conflict")
    assert command.wire() == original_wire
    denied(
        h,
        database_connection,
        command.apply,
        (ProfilePairingError, DeviceCompatibilityError),
        "operation_conflict",
    )
    registration.body = old_body
    restored = registration.signed()
    database_connection.execute(
        "UPDATE account.account_registration_receipt SET binding_commit_id=%s,request_sha256=%s "
        "WHERE registration_id=%s",
        (
            UUID(restored["binding_commit_id"]),
            bytes.fromhex(restored["request_sha256"]),
            UUID(restored["registration_id"]),
        ),
    )
    database_connection.commit()
    if expired:
        denied(
            h,
            database_connection,
            registration.apply,
            PublicAccessError,
            "public_access_unavailable",
        )
    else:
        assert registration.apply()[0]["session_id"] == old_result["session_id"]
        assert command.apply()[0]["session_id"] == new_result["session_id"]


@pytest.mark.parametrize("first", ["pa2", "v2"])
@pytest.mark.parametrize("action", ["CREATE", "CANCEL", "DISABLE"])
@pytest.mark.parametrize("v2_field", ["exchange_id", "binding_commit_id"])
def test_owner_nonissuer_operation_cannot_poison_a_v2_receipt(
    pa2_v2: Harness,
    database_connection: Connection[Any],
    first: str,
    action: str,
    v2_field: str,
) -> None:
    h = pa2_v2
    body = (
        h.create_body()
        if action == "CREATE"
        else {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "reason_code": "USER_REQUESTED",
        }
    )
    target = h.registration() if action != "CREATE" else None
    target_result = target.apply()[0] if action == "DISABLE" and target is not None else None

    def owner_action() -> Any:
        if action == "CREATE":
            return h.pa2.create_invitation(h.profile.owner, body)
        assert target is not None
        if action == "CANCEL":
            return h.pa2.cancel_invitation(
                h.profile.owner, UUID(target.body["invitation_id"]), body
            )
        assert target_result is not None
        return h.pa2.disable_account(h.profile.owner, UUID(str(target_result["user_id"])), body)

    command = modern(h, "admission")
    if first == "v2":
        winner, _ = command.apply()
        body["operation_id"] = command.body[v2_field]
        denied(h, database_connection, owner_action, PublicAccessError, "operation_conflict")
        assert command.apply()[0]["session_id"] == winner["session_id"]
        body["operation_id"] = str(uuid4())
        owner_action()  # prove the live OWNER and target authorize this command
    else:
        winner = owner_action()
        command.body[v2_field] = body["operation_id"]
        denied(h, database_connection, command.apply, ProfilePairingError, "operation_conflict")
        before, grants = snapshot(database_connection), len(h.grants)
        replay = owner_action()
        if action == "CREATE":
            assert replay[1] is True and "invitation_secret" not in replay[0]
            assert replay[0]["invitation_id"] == winner[0]["invitation_id"]
        else:
            assert replay == winner
        assert len(h.grants) == grants and snapshot(database_connection) == before
        command.body[v2_field] = str(uuid4())
        command.apply()


@pytest.mark.parametrize("family", ["submit", "challenge"])
@pytest.mark.parametrize(
    "reserved", ["registration_id", "binding_commit_id", "invitation_id", "owner_operation"]
)
def test_new_admission_and_trusted_ceremony_ids_respect_pa2_reservations(
    pa2_v2: Harness,
    database_connection: Connection[Any],
    family: str,
    reserved: str,
) -> None:
    h, registration = pa2_v2, pa2_v2.registration()
    registration.apply()
    identifier = registration.body.get(reserved)
    if reserved == "owner_operation":
        body = h.create_body()
        h.pa2.create_invitation(h.profile.owner, body)
        identifier = body["operation_id"]
    assert isinstance(identifier, str)
    if family == "submit":
        body, key = h.admission.submit_body()
        field, operation = "request_id", "admission.submit"

        def invoke(raw: bytes) -> object:
            return h.admission.service.admission_submit(raw)
    else:
        a = h.admission.submit()
        h.admission.bind(a)
        body, key = h.admission.challenge_body(a), a.key
        field, operation = "challenge_request_id", "trusted.challenge"
        invoke = h.admission.service.trusted_challenge
    body[field] = identifier

    def call() -> object:
        return invoke(proof(body, key, operation))

    denied(h, database_connection, call, ProfilePairingError, "operation_conflict")
    body[field] = str(uuid4())
    assert call()  # same real proof, key and current authority with a fresh ID


@pytest.mark.parametrize("pa2_field", ["registration_id", "binding_commit_id"])
def test_generated_portable_challenge_and_pa2_invitation_ids_obey_the_same_guard(
    pa2_v2: Harness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    pa2_field: str,
) -> None:
    h, registration = pa2_v2, pa2_v2.registration()
    registration.apply()
    runtime = portable(h)
    challenge_body = rfc8785.dumps(
        {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": h.profile.platform,
            "account": runtime.document,
        }
    )
    with monkeypatch.context() as local:
        local.setattr(
            "autplay.application.device_portable_service_v2.uuid4",
            lambda: UUID(registration.body[pa2_field]),
        )
        denied(
            h,
            database_connection,
            lambda: runtime.service.register_challenge(challenge_body),
            ProfilePairingError,
            "operation_conflict",
        )
    # The unmodified public challenge path works with normal generated IDs.
    challenge = runtime.service.register_challenge(challenge_body)
    candidate = h.registration()
    before = snapshot(database_connection)
    candidate.body[pa2_field] = require_instance(challenge["challenge"], dict)["challenge_id"]
    denied(h, database_connection, candidate.apply, PublicAccessError, "registration_conflict")
    assert snapshot(database_connection) == before
    candidate.body[pa2_field] = str(uuid4())
    assert candidate.apply()[1] is False
    owner_body = h.create_body()
    with monkeypatch.context() as local:
        local.setattr(
            "autplay.application.public_access.uuid4",
            lambda: UUID(require_instance(challenge["challenge"], dict)["challenge_id"]),
        )
        denied(
            h,
            database_connection,
            lambda: h.pa2.create_invitation(h.profile.owner, owner_body),
            PublicAccessError,
            "operation_conflict",
        )
    assert h.pa2.create_invitation(h.profile.owner, owner_body)[1] is False

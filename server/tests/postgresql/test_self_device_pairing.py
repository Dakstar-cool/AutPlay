"""Real PostgreSQL proof, account isolation and recoverable two-phone enrollment."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.self_device_pairing import (
    SqlAlchemySelfPairingRepository,
    SqlAlchemySelfPairingUnitOfWorkFactory,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.profile_pairing import ProfilePairingService
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_spki,
    sign_p1363,
)
from autplay.domain.self_device_pairing import SelfDevicePairing, SelfPairingError
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_profile_pairing_m5b import _exchange_request


@dataclass
class PairingHarness:
    engine: Engine
    service: SelfDevicePairingService
    actor: Principal
    identity: dict[str, Any]
    ceremony_id: UUID
    rendezvous: str
    poll_secret: str
    key: ec.EllipticCurvePrivateKey

    def request(self, kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        document = {
            "contract_version": "v1",
            "schema_version": 1,
            "ceremony_id": str(self.ceremony_id),
            "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **self.identity,
            **fields,
        }
        document.pop("device_signature_b64url", None)
        document.pop("request_sha256", None)
        digest = canonical_sha256(document)
        document["request_sha256"] = digest.hex()
        if kind in {"claim", "poll", "exchange"}:
            document["device_signature_b64url"] = sign_p1363(
                self.key,
                f"autplay:self-device-pairing:{kind}:v1\n",
                digest,
            )
        return document

    def start(self) -> dict[str, Any]:
        return self.request(
            "start",
            {
                "operation_id": str(uuid4()),
                "rendezvous_secret_sha256": hashlib.sha256(self.rendezvous.encode()).hexdigest(),
            },
        )

    def claim(self) -> dict[str, Any]:
        spki = public_spki(self.key)
        return self.request(
            "claim",
            {
                "claim_id": str(uuid4()),
                "poll_secret_sha256": hashlib.sha256(self.poll_secret.encode()).hexdigest(),
                "device_public_key_spki_b64": base64.b64encode(spki).decode(),
                "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
                "device_name": "New phone",
                "platform": "ANDROID",
                "app_version": "fixture-1",
            },
        )

    def approve(self, claimed: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "decision",
            {
                "operation_id": str(uuid4()),
                "action": "APPROVE",
                "expected_revision": claimed["revision"],
                "claim_id": claimed["claim_id"],
                "claim_request_sha256": claimed["claim_request_sha256"],
                "comparison_code": claimed["comparison_code"],
            },
        )

    def ready(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self.service.start(self.actor, self.start())
        claim = self.claim()
        claimed = self.service.claim(self.rendezvous, claim)
        approved = self.service.decide(self.actor, self.approve(claimed))
        exchange = self.request(
            "exchange",
            {
                "exchange_id": str(uuid4()),
                "binding_commit_id": str(uuid4()),
                "claim_id": claim["claim_id"],
                "claim_request_sha256": claim["request_sha256"],
                "approval_operation_id": approved["approval_operation_id"],
                "confirmed_account_id": str(self.actor.user_id),
                "next_refresh_token_sha256": hashlib.sha256(
                    secrets.token_urlsafe(32).encode()
                ).hexdigest(),
            },
        )
        return claim, exchange


@pytest.fixture
def pair(database_url: str) -> Iterator[PairingHarness]:
    engine = create_engine(
        database_url, pool_pre_ping=True, connect_args={"options": "-c statement_timeout=5000"}
    )
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime.now(UTC)
    server_id, user_id, device_id, session_id = (uuid4() for _ in range(4))
    identity_key = public_spki(ec.generate_private_key(ec.SECP256R1()))
    source_key = public_spki(ec.generate_private_key(ec.SECP256R1()))
    with sessions.begin() as session:
        session.add(UserAccountRow(user_id=user_id, display_name="Invited user", role="USER"))
        session.add(
            ServerInstanceRow(
                server_instance_id=server_id,
                identity_epoch=1,
                identity_public_key_spki=identity_key,
                identity_thumbprint_sha256=hashlib.sha256(identity_key).digest(),
                label_hint="Fixture",
                api_origin="https://api.test.invalid",
                stream_origin="https://stream.test.invalid",
                capability_revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            DeviceRow(
                device_id=device_id,
                user_id=user_id,
                device_name="Existing phone",
                platform="ANDROID",
                app_version="fixture-1",
                public_key=source_key,
                public_key_thumbprint_sha256=hashlib.sha256(source_key).digest(),
            )
        )
        session.flush()
        session.add(
            UserSessionRow(
                session_id=session_id,
                user_id=user_id,
                device_id=device_id,
                refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                issued_at=now,
                expires_at=now + timedelta(days=90),
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
        )
    codec = Hs256AccessTokenCodec(
        b"self-pairing-test-secret-at-least-32-bytes", issuer="test", audience="test"
    )
    service = SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(sessions),
        codec,
        timedelta(minutes=10),
        b"self-pairing-test-source-secret-distinct-32bytes",
    )
    try:
        yield PairingHarness(
            engine,
            service,
            Principal(user_id, device_id, session_id, AccountRole.USER),
            {
                "expected_server_instance_id": str(server_id),
                "expected_identity_epoch": 1,
                "expected_identity_thumbprint_sha256": hashlib.sha256(identity_key).hexdigest(),
                "expected_api_origin": "https://api.test.invalid",
                "expected_stream_origin": "https://stream.test.invalid",
            },
            uuid4(),
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
            ec.generate_private_key(ec.SECP256R1()),
        )
    finally:
        engine.dispose()


def test_user_requires_both_confirmations_and_keeps_existing_device(pair: PairingHarness) -> None:
    start = pair.start()
    assert pair.service.start(pair.actor, start) == pair.service.start(pair.actor, start)
    claim = pair.claim()
    claimed = pair.service.claim(pair.rendezvous, claim)
    assert "account_id" not in claimed
    assert pair.service.claim(pair.rendezvous, claim) == claimed
    approval = pair.approve(claimed)
    wrong = pair.request("decision", {**approval, "comparison_code": "000000000001"})
    with pytest.raises(SelfPairingError):
        pair.service.decide(pair.actor, wrong)
    approved = pair.service.decide(pair.actor, approval)
    assert pair.service.decide(pair.actor, approval) == approved
    exchange = pair.request(
        "exchange",
        {
            "exchange_id": str(uuid4()),
            "binding_commit_id": str(uuid4()),
            "claim_id": claim["claim_id"],
            "claim_request_sha256": claim["request_sha256"],
            "approval_operation_id": approved["approval_operation_id"],
            "confirmed_account_id": str(uuid4()),
            "next_refresh_token_sha256": "ab" * 32,
        },
    )
    with pytest.raises(SelfPairingError):
        pair.service.exchange(pair.poll_secret, exchange)
    exchange = pair.request(
        "exchange", {**exchange, "confirmed_account_id": str(pair.actor.user_id)}
    )
    result = pair.service.exchange(pair.poll_secret, exchange)
    assert result["user_id"] == str(pair.actor.user_id) and result["replayed"] is False
    with pair.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM account.device WHERE revoked_at IS NULL"))
            == 2
        )
        assert connection.scalar(text("SELECT count(*) FROM account.trusted_device_key")) == 0
        assert (
            connection.scalar(
                text("SELECT revoked_at FROM account.user_session WHERE session_id=:id"),
                {"id": pair.actor.session_id},
            )
            is None
        )


def test_every_active_role_self_pairs_without_spending_recovery_code(
    pair: PairingHarness,
) -> None:
    now = datetime.now(UTC)
    verifier = b"r" * 32
    with Session(pair.engine) as session, session.begin():
        session.add(
            UserAccountRow(
                user_id=uuid4(),
                display_name="Remaining owner",
                role=AccountRole.OWNER.value,
            )
        )
        session.add(
            AccountRecoveryCredentialRow(
                user_id=pair.actor.user_id,
                server_instance_id=UUID(pair.identity["expected_server_instance_id"]),
                identity_epoch=1,
                identity_thumbprint_sha256=bytes.fromhex(
                    pair.identity["expected_identity_thumbprint_sha256"]
                ),
                generation=1,
                verifier_sha256=verifier,
                created_at=now,
                updated_at=now,
            )
        )

    for role in (AccountRole.OWNER, AccountRole.ADMIN, AccountRole.USER):
        with Session(pair.engine) as session, session.begin():
            account = session.get(UserAccountRow, pair.actor.user_id)
            assert account is not None
            account.role = role.value
        pair.actor = replace(pair.actor, role=role)
        pair.ceremony_id = uuid4()
        pair.rendezvous = secrets.token_urlsafe(32)
        pair.poll_secret = secrets.token_urlsafe(32)
        pair.key = ec.generate_private_key(ec.SECP256R1())

        _, exchange = pair.ready()
        result = pair.service.exchange(pair.poll_secret, exchange)
        assert result["user_id"] == str(pair.actor.user_id)
        with Session(pair.engine) as session:
            credential = session.get(AccountRecoveryCredentialRow, pair.actor.user_id)
            assert credential is not None
            assert credential.generation == 1
            assert credential.verifier_sha256 == verifier
            assert (
                session.scalar(
                    text(
                        "SELECT count(*) FROM account.account_recovery_operation "
                        "WHERE user_id=:user"
                    ),
                    {"user": pair.actor.user_id},
                )
                == 0
            )


def test_concurrent_exchange_replays_one_binding_and_survives_source_logout(
    pair: PairingHarness,
) -> None:
    _, exchange = pair.ready()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: pair.service.exchange(pair.poll_secret, exchange), range(2))
        )
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["session_id"] == results[1]["session_id"]
    with pair.engine.begin() as connection:
        connection.execute(
            text("UPDATE account.user_session SET revoked_at=now() WHERE session_id=:id"),
            {"id": pair.actor.session_id},
        )
    replay = pair.service.exchange(pair.poll_secret, exchange)
    assert replay["device_id"] == results[0]["device_id"] and replay["replayed"]
    changed = pair.request("exchange", {**exchange, "binding_commit_id": str(uuid4())})
    with pytest.raises(SelfPairingError, match="operation_conflict"):
        pair.service.exchange(pair.poll_secret, changed)
    with pair.engine.begin() as connection:
        connection.execute(
            text("UPDATE account.user_session SET revoked_at=now() WHERE session_id=:id"),
            {"id": UUID(replay["session_id"])},
        )
    with pytest.raises(SelfPairingError):
        pair.service.exchange(pair.poll_secret, exchange)


@pytest.mark.parametrize("change", ["source_revoked", "generation_changed", "disabled"])
def test_approval_does_not_survive_authority_revocation(pair: PairingHarness, change: str) -> None:
    _, exchange = pair.ready()
    with pair.engine.begin() as connection:
        if change == "source_revoked":
            connection.execute(
                text("UPDATE account.device SET revoked_at=now() WHERE device_id=:id"),
                {"id": pair.actor.device_id},
            )
        elif change == "generation_changed":
            connection.execute(
                text("UPDATE account.user_account SET authority_generation=authority_generation+1")
            )
        else:
            connection.execute(text("UPDATE account.user_account SET status='DISABLED'"))
    with pytest.raises(SelfPairingError):
        pair.service.exchange(pair.poll_secret, exchange)
    with pair.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.device")) == 1


def test_source_refresh_rotation_preserves_pending_approval_authority(pair: PairingHarness) -> None:
    pair.service.start(pair.actor, pair.start())
    claimed = pair.service.claim(pair.rendezvous, pair.claim())
    successor = uuid4()
    with Session(pair.engine) as session, session.begin():
        parent = session.get(UserSessionRow, pair.actor.session_id)
        assert parent is not None
        parent.revoked_at = datetime.now(UTC)
        session.add(
            UserSessionRow(
                session_id=successor,
                user_id=parent.user_id,
                device_id=parent.device_id,
                refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                issued_at=parent.revoked_at,
                expires_at=parent.expires_at,
                family_id=parent.family_id,
                generation=1,
                session_mode="V2",
            )
        )
    actor = Principal(pair.actor.user_id, pair.actor.device_id, successor, AccountRole.USER)
    assert pair.service.decide(actor, pair.approve(claimed))["state"] == "APPROVED"


def test_committed_exchange_cannot_replay_after_account_generation_changes(
    pair: PairingHarness,
) -> None:
    _, exchange = pair.ready()
    pair.service.exchange(pair.poll_secret, exchange)
    with pair.engine.begin() as connection:
        connection.execute(
            text("UPDATE account.user_account SET authority_generation=authority_generation+1")
        )
    with pytest.raises(SelfPairingError):
        pair.service.exchange(pair.poll_secret, exchange)


def test_cleanup_preserves_unexpired_exchange_receipt_and_active_devices(
    pair: PairingHarness,
) -> None:
    _, exchange = pair.ready()
    pair.service.exchange(pair.poll_secret, exchange)
    first_id = pair.ceremony_id
    pair.ceremony_id = uuid4()
    pair.service.start(pair.actor, pair.start())
    sessions = sessionmaker(pair.engine, class_=Session, expire_on_commit=False)
    with sessions.begin() as session:
        repository = SqlAlchemySelfPairingRepository(session)
        assert repository.cleanup(datetime.now(UTC) + timedelta(days=2), limit=10) == 1
    with pair.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.device")) == 2
        assert (
            connection.scalar(text("SELECT ceremony_id FROM account.self_device_pairing"))
            == first_id
        )
    assert pair.service.exchange(pair.poll_secret, exchange)["replayed"] is True


def test_wrong_actor_secret_or_key_cannot_claim_or_observe(pair: PairingHarness) -> None:
    pair.service.start(pair.actor, pair.start())
    other = Principal(uuid4(), pair.actor.device_id, pair.actor.session_id, AccountRole.OWNER)
    with pytest.raises(SelfPairingError):
        pair.service.status(other, pair.ceremony_id)
    claim = pair.claim()
    with pytest.raises(SelfPairingError):
        pair.service.claim(secrets.token_urlsafe(32), claim)
    tampered = {**claim, "device_name": "Tampered"}
    with pytest.raises(SelfPairingError, match="self_pairing_request_invalid"):
        pair.service.claim(pair.rendezvous, tampered)
    assert pair.service.claim(pair.rendezvous, claim)["state"] == "CLAIMED"
    pair.key = ec.generate_private_key(ec.SECP256R1())
    poll = pair.request(
        "poll", {"claim_id": claim["claim_id"], "claim_request_sha256": claim["request_sha256"]}
    )
    with pytest.raises(SelfPairingError):
        pair.service.poll(pair.poll_secret, poll)


def test_cancelled_state_remains_terminal_after_ceremony_deadline(pair: PairingHarness) -> None:
    started = pair.service.start(pair.actor, pair.start())
    cancel = pair.request(
        "decision",
        {
            "operation_id": str(uuid4()),
            "action": "CANCEL",
            "expected_revision": started["revision"],
            "claim_id": None,
            "claim_request_sha256": None,
            "comparison_code": None,
        },
    )
    result = pair.service.decide(pair.actor, cancel)
    with pair.engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE account.self_device_pairing
            SET created_at=created_at-interval '16 minutes',
                expires_at=expires_at-interval '16 minutes'
        """)
        )
    assert pair.service.status(pair.actor, pair.ceremony_id)["state"] == "CANCELLED"
    assert pair.service.decide(pair.actor, cancel) == result


def test_failed_exchange_rolls_back_binding_and_can_retry(pair: PairingHarness) -> None:
    _, exchange = pair.ready()
    with pair.engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION audit.self_pairing_test_abort() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='self_pairing.exchanged' THEN
              RAISE EXCEPTION 'fixture abort'; END IF;
              RETURN NEW; END $$;
            CREATE TRIGGER self_pairing_test_abort BEFORE INSERT ON audit.audit_event
              FOR EACH ROW EXECUTE FUNCTION audit.self_pairing_test_abort();
        """)
        )
    with pytest.raises(DBAPIError, match="fixture abort"):
        pair.service.exchange(pair.poll_secret, exchange)
    with pair.engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.device")) == 1
        assert connection.scalar(text("SELECT count(*) FROM account.user_session")) == 1
        assert (
            connection.scalar(text("SELECT state FROM account.self_device_pairing")) == "APPROVED"
        )
        connection.execute(text("DROP TRIGGER self_pairing_test_abort ON audit.audit_event"))
        connection.execute(text("DROP FUNCTION audit.self_pairing_test_abort()"))
    assert pair.service.exchange(pair.poll_secret, exchange)["replayed"] is False


def test_rate_only_evidence_blocks_downgrade(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    pair.service.rate_gate("127.0.0.1")
    with pytest.raises(DBAPIError, match="Refusing to discard"):
        database_harness.downgrade(database_name, "0033_web_passkeys")


def test_m5_revocation_waits_without_deadlocking_self_exchange(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, exchange = pair.ready()
    target_id = uuid4()
    with pair.engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO account.device (device_id,user_id,device_name,platform,app_version)
            VALUES (:id,:user,'Other existing phone','ANDROID','fixture')
        """),
            {"id": target_id, "user": pair.actor.user_id},
        )
    m5 = ProfilePairingService(
        sessionmaker(pair.engine, class_=Session, expire_on_commit=False),
        private_key=ec.generate_private_key(ec.SECP256R1()),
        label_hint="Fixture",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=Hs256AccessTokenCodec(
            b"m5-race-test-secret-at-least-32-bytes", issuer="test", audience="test"
        ),
        access_ttl=timedelta(minutes=10),
    )
    account_locked, release = threading.Event(), threading.Event()
    original = SqlAlchemySelfPairingRepository.require_source

    def pause_before_source(
        self: SqlAlchemySelfPairingRepository, ceremony: SelfDevicePairing, now: datetime
    ) -> None:
        account_locked.set()
        assert release.wait(4)
        original(self, ceremony, now)

    monkeypatch.setattr(SqlAlchemySelfPairingRepository, "require_source", pause_before_source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        enrolling = pool.submit(pair.service.exchange, pair.poll_secret, exchange)
        assert account_locked.wait(2)
        revoking = pool.submit(m5.revoke_device, pair.actor, target_id, uuid4())
        try:
            deadline = time.monotonic() + 3
            blocked = False
            while time.monotonic() < deadline:
                with pair.engine.connect() as connection:
                    blocked = bool(
                        connection.scalar(
                            text("""
                        SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
                        AND wait_event_type='Lock' AND pid<>pg_backend_pid()
                    """)
                        )
                    )
                if blocked:
                    break
                threading.Event().wait(0.01)
            assert blocked
        finally:
            release.set()
        assert enrolling.result(timeout=5)["replayed"] is False
        assert revoking.result(timeout=5)["outcome"] == "APPLIED"


def test_self_pairing_uses_live_override_and_replays_below_lowered_limit(
    pair: PairingHarness,
) -> None:
    _, exchange = pair.ready()
    with pair.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO account.account_quota_override (user_id,revision,devices,updated_at) "
                "VALUES (:user,1,1,clock_timestamp())"
            ),
            {"user": pair.actor.user_id},
        )
    with pytest.raises(SelfPairingError, match="account_device_limit_reached"):
        pair.service.exchange(pair.poll_secret, exchange)
    with pair.engine.begin() as connection:
        connection.execute(text("UPDATE account.account_quota_override SET devices=2,revision=2"))
    first = pair.service.exchange(pair.poll_secret, exchange)
    with pair.engine.begin() as connection:
        connection.execute(text("UPDATE account.account_quota_override SET devices=1,revision=3"))
    replay = pair.service.exchange(pair.poll_secret, exchange)
    assert replay["device_id"] == first["device_id"] and replay["replayed"] is True
    with pair.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM account.device WHERE revoked_at IS NULL"))
            == 2
        )


def test_m5_and_self_pairing_cannot_both_take_last_device_slot(pair: PairingHarness) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    digest = hashlib.sha256(spki).digest()
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    with sessions.begin() as session:
        lock_resource_admission(session)
        account = session.get(UserAccountRow, pair.actor.user_id)
        assert account is not None
        account.role = "OWNER"
        row = session.get(ServerInstanceRow, UUID(pair.identity["expected_server_instance_id"]))
        assert row is not None
        row.identity_public_key_spki, row.identity_thumbprint_sha256 = spki, digest
        session.execute(text("UPDATE account.resource_quota_policy SET default_devices=2"))
    pair.actor = replace(pair.actor, role=AccountRole.OWNER)
    pair.identity["expected_identity_thumbprint_sha256"] = digest.hex()
    m5 = ProfilePairingService(
        sessions,
        private_key=key,
        label_hint="Fixture",
        api_origin="https://api.test.invalid",
        stream_origin="https://stream.test.invalid",
        access_tokens=Hs256AccessTokenCodec(
            b"m5-quota-race-test-secret-at-least-32bytes", issuer="test", audience="test"
        ),
        access_ttl=timedelta(minutes=10),
    )
    invitation = m5.issue_invitation(pair.actor, uuid4(), 300)
    m5_request, _, _ = _exchange_request(invitation)
    _, self_request = pair.ready()
    barrier = threading.Barrier(2)

    def exchange(kind: str) -> str:
        barrier.wait(timeout=3)
        try:
            if kind == "M5":
                m5.exchange(m5_request)
            else:
                pair.service.exchange(pair.poll_secret, self_request)
            return "BOUND"
        except (SelfPairingError, ProfilePairingError) as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(exchange, ["M5", "SELF"])) == [
            "BOUND",
            "account_device_limit_reached",
        ]
    with pair.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM account.device WHERE revoked_at IS NULL"))
            == 2
        )

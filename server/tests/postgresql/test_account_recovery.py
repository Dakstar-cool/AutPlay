"""Atomic one-use recovery, full authority withdrawal and exact retry on PostgreSQL."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_recovery import (
    AccountRecoveryCredentialRow,
    AccountRecoveryOperationRow,
)
from autplay.adapters.postgresql.models.profile_pairing import (
    DeviceAdmissionRow,
    DeviceKeyBlockRow,
    EnrollmentInvitationRow,
    TrustedDeviceKeyRow,
    TrustedDeviceReenrollmentChallengeRow,
)
from autplay.adapters.postgresql.models.public_access import AccountInvitationRow
from autplay.adapters.postgresql.models.resource_admission import ResourceAdmissionRow
from autplay.adapters.postgresql.models.self_device_pairing import SelfDevicePairingRow
from autplay.adapters.postgresql.models.web_admin import WebSessionInvitationRow, WebSessionRow
from autplay.adapters.postgresql.models.web_passkeys import WebPasskeyRow
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.account_recovery import (
    AccountRecoveryService,
    cleanup_expired_recovery_operations,
)
from autplay.domain.account_recovery import AccountRecoveryError, code_verifier, new_code
from autplay.domain.profile_pairing import canonical_sha256, public_spki, sign_p1363
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_self_device_pairing import PairingHarness
from .test_self_device_pairing import pair as pair


def required[T](value: T | None) -> T:
    assert value is not None
    return value


def service(pair: PairingHarness) -> AccountRecoveryService:
    return AccountRecoveryService(
        sessionmaker(pair.engine, class_=Session, expire_on_commit=False),
        Hs256AccessTokenCodec(
            b"recovery-test-access-secret-distinct-32bytes", issuer="test", audience="test"
        ),
        timedelta(minutes=10),
        b"recovery-test-source-secret-distinct-32bytes",
    )


def request(pair: PairingHarness, kind: str, **fields: Any) -> dict[str, Any]:
    body = {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **pair.identity,
        "account_id": str(pair.actor.user_id),
    }
    if kind != "configure":
        key = public_spki(pair.key)
        body.update(
            device_public_key_spki_b64=base64.b64encode(key).decode(),
            device_key_thumbprint_sha256=hashlib.sha256(key).hexdigest(),
            device_name="Recovered phone",
            platform="ANDROID",
            app_version="test-1",
        )
    body.update(fields)
    body.pop("request_sha256", None)
    body.pop("device_signature_b64url", None)
    digest = canonical_sha256(body)
    body["request_sha256"] = digest.hex()
    if kind != "configure":
        body["device_signature_b64url"] = sign_p1363(
            pair.key, f"autplay:account-recovery:{kind}:v1\n", digest
        )
    return body


def verifier(pair: PairingHarness, code: str) -> str:
    return code_verifier(
        UUID(pair.identity["expected_server_instance_id"]), pair.actor.user_id, code
    ).hex()


def configured(pair: PairingHarness) -> tuple[AccountRecoveryService, str, dict[str, Any]]:
    recovery, code = service(pair), new_code()
    recovery.configure(
        pair.actor,
        request(
            pair,
            "configure",
            expected_code_generation=0,
            next_code_verifier_sha256=verifier(pair, code),
        ),
    )
    commit = request(
        pair,
        "recover",
        expected_code_generation=1,
        next_code_verifier_sha256=verifier(pair, new_code()),
        next_refresh_token_sha256=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        binding_commit_id=str(uuid4()),
        confirmed_account_id=str(pair.actor.user_id),
    )
    return recovery, code, commit


def test_configure_compare_and_set_and_exact_replay(pair: PairingHarness) -> None:
    recovery, code = service(pair), new_code()
    assert recovery.status(pair.actor)["configured"] is False
    body = request(
        pair,
        "configure",
        expected_code_generation=0,
        next_code_verifier_sha256=verifier(pair, code),
    )
    assert recovery.configure(pair.actor, body)["code_generation"] == 1
    assert recovery.configure(pair.actor, body)["replayed"] is True
    changed = request(
        pair, "configure", **{**body, "next_code_verifier_sha256": verifier(pair, new_code())}
    )
    with pytest.raises(AccountRecoveryError, match="operation_conflict"):
        recovery.configure(pair.actor, changed)
    stale = request(
        pair,
        "configure",
        expected_code_generation=0,
        next_code_verifier_sha256=verifier(pair, new_code()),
    )
    with pytest.raises(AccountRecoveryError, match="recovery_generation_conflict"):
        recovery.configure(pair.actor, stale)
    recovery.configure(
        pair.actor,
        request(
            pair,
            "configure",
            expected_code_generation=1,
            next_code_verifier_sha256=verifier(pair, new_code()),
        ),
    )
    with pytest.raises(AccountRecoveryError, match="recovery_operation_superseded"):
        recovery.configure(pair.actor, body)


def seed_authority(pair: PairingHarness) -> None:
    now, user_id = datetime.now(UTC), pair.actor.user_id
    server_id = UUID(pair.identity["expected_server_instance_id"])
    key, browser_id = public_spki(pair.key), uuid4()
    digest = hashlib.sha256(key).digest()
    pair.service.start(pair.actor, pair.start())
    with Session(pair.engine) as session, session.begin():
        session.add(
            WebPasskeyRow(
                passkey_id=uuid4(),
                server_instance_id=server_id,
                user_id=user_id,
                user_handle=b"h" * 32,
                credential_id=b"fixture",
                public_key=key,
                sign_count=0,
                backup_eligible=False,
                backed_up=False,
                label="Test passkey",
                created_at=now,
            )
        )
        session.add(
            WebSessionRow(
                web_session_id=browser_id,
                family_id=browser_id,
                server_instance_id=server_id,
                user_id=user_id,
                token_generation=0,
                token_sha256=b"w" * 32,
                csrf_sha256=b"c" * 32,
                issued_at=now,
                token_issued_at=now,
                last_activity_at=now,
                idle_expires_at=now + timedelta(minutes=5),
                absolute_expires_at=now + timedelta(hours=1),
            )
        )
        session.add(
            TrustedDeviceKeyRow(
                user_id=user_id,
                device_key_thumbprint_sha256=digest,
                device_public_key_spki=key,
                approved_request_id=uuid4(),
                key_reference=uuid4(),
                revision=1,
                created_at=now,
            )
        )
        session.add(
            DeviceKeyBlockRow(
                user_id=user_id, device_key_thumbprint_sha256=b"b" * 32, blocked_at=now
            )
        )
        session.add(
            TrustedDeviceReenrollmentChallengeRow(
                challenge_id=uuid4(),
                user_id=user_id,
                device_key_thumbprint_sha256=digest,
                request_sha256=b"r" * 32,
                client_nonce_sha256=b"n" * 32,
                challenge_hash=b"h" * 32,
                created_at=now,
                expires_at=now + timedelta(minutes=1),
            )
        )
        session.add(
            EnrollmentInvitationRow(
                invitation_id=uuid4(),
                server_instance_id=server_id,
                user_id=user_id,
                issued_by_user_id=user_id,
                invitation_secret_hash=b"e" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.add(
            WebSessionInvitationRow(
                invitation_id=uuid4(),
                server_instance_id=server_id,
                user_id=user_id,
                issuer_kind="OWNER",
                secret_sha256=b"i" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.add(
            AccountInvitationRow(
                invitation_id=uuid4(),
                issued_by_user_id=user_id,
                display_name="Invitee",
                secret_sha256=b"a" * 32,
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.add(
            ResourceAdmissionRow(
                operation_id=uuid4(),
                user_id=user_id,
                authority_generation=1,
                authority_kind="DEVICE_SESSION",
                device_id=pair.actor.device_id,
                session_family_id=pair.actor.session_id,
                session_mode="V2",
                kind="PLAYBACK",
                resource_type="PLAY_INSTANCE",
                resource_id=uuid4(),
                request_sha256=b"p" * 32,
                state="WAITING",
                generation=0,
                created_at=now,
                updated_at=now,
                enqueued_at=now,
                waiting_until=now + timedelta(minutes=1),
                attachment_revision=0,
            )
        )
        session.flush()
        for state in ("PENDING", "APPROVED"):
            identifier = uuid4()
            session.add(
                DeviceAdmissionRow(
                    request_id=identifier,
                    request_sha256=b"d" * 32,
                    server_instance_id=server_id,
                    identity_epoch=1,
                    identity_thumbprint_sha256=bytes.fromhex(
                        pair.identity["expected_identity_thumbprint_sha256"]
                    ),
                    device_public_key_spki=key,
                    device_key_thumbprint_sha256=digest,
                    nickname="Other device",
                    platform="ANDROID",
                    app_version="test",
                    api_major=1,
                    requested_at=now,
                    state=state,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now,
                    review_locator_hash=hashlib.sha256(identifier.bytes).digest(),
                    poll_bearer_hash=b"p" * 32,
                    review_web_session_id=browser_id,
                    approved_user_id=user_id if state == "APPROVED" else None,
                )
            )


def test_recovery_replaces_all_old_authority_and_preserves_account(pair: PairingHarness) -> None:
    recovery, code, commit = configured(pair)
    seed_authority(pair)
    preview = recovery.preview(code, request(pair, "preview"))
    assert preview["account_id"] == str(pair.actor.user_id) and preview["confirmation_required"]
    result = recovery.recover(code, commit)
    again = recovery.recover(code, commit)
    assert again["replayed"] and again["device_id"] == result["device_id"]
    assert again["session_id"] == result["session_id"]
    assert "refresh_token" not in result and "code" not in result
    with Session(pair.engine) as session:
        account = session.get(UserAccountRow, pair.actor.user_id)
        assert account is not None and account.status == "ACTIVE" and account.deleted_at is None
        assert account.authority_generation == 2 and account.display_name == "Invited user"
        assert session.scalar(select(func.count()).select_from(DeviceRow)) == 2
        assert (
            session.scalar(
                select(func.count()).select_from(DeviceRow).where(DeviceRow.revoked_at.is_(None))
            )
            == 1
        )
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is not None
        assert required(session.scalar(select(WebSessionRow))).revoked_at is not None
        assert required(session.scalar(select(WebPasskeyRow))).revoked_at is not None
        assert required(session.scalar(select(TrustedDeviceKeyRow))).removed_at is not None
        assert required(session.scalar(select(TrustedDeviceKeyRow))).revision == 2
        assert (
            required(session.scalar(select(TrustedDeviceReenrollmentChallengeRow))).consumed_at
            is not None
        )
        assert required(session.scalar(select(DeviceKeyBlockRow))).unblocked_at is None
        assert session.scalar(select(EnrollmentInvitationRow.cancelled_at)) is not None
        assert session.scalar(select(WebSessionInvitationRow.cancelled_at)) is not None
        assert session.scalar(select(AccountInvitationRow.cancelled_at)) is not None
        assert set(session.scalars(select(DeviceAdmissionRow.state))) == {"CANCELLED"}
        assert session.scalar(select(SelfDevicePairingRow.state)) == "CANCELLED"
        assert session.scalar(select(ResourceAdmissionRow.state)) == "EXPIRED"
        assert session.scalar(select(AccountRecoveryCredentialRow.generation)) == 2
    with pytest.raises(AccountRecoveryError):
        recovery.status(pair.actor)
    with pytest.raises(AccountRecoveryError):
        recovery.recover(code, request(pair, "recover", **{**commit, "operation_id": str(uuid4())}))


def test_simultaneous_use_consumes_one_generation(pair: PairingHarness) -> None:
    recovery, code, commit = configured(pair)
    other = request(
        pair,
        "recover",
        **{**commit, "operation_id": str(uuid4()), "binding_commit_id": str(uuid4())},
    )

    def consume(body: dict[str, Any]) -> dict[str, Any] | str:
        try:
            return recovery.recover(code, body)
        except AccountRecoveryError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, [commit, other]))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(DeviceRow)) == 2
        assert required(session.get(UserAccountRow, pair.actor.user_id)).authority_generation == 2


def test_failed_replacement_rolls_back_revocation_and_code(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery, code, commit = configured(pair)

    def fail(*args: Any) -> None:
        raise AccountRecoveryError("fixture_capacity_failure")

    monkeypatch.setattr("autplay.application.account_recovery.require_device_capacity", fail)
    with pytest.raises(AccountRecoveryError, match="fixture_capacity_failure"):
        recovery.recover(code, commit)
    with Session(pair.engine) as session:
        assert required(session.get(DeviceRow, pair.actor.device_id)).revoked_at is None
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is None
        assert required(session.get(UserAccountRow, pair.actor.user_id)).authority_generation == 1
        assert session.scalar(select(AccountRecoveryCredentialRow.generation)) == 1
        assert session.scalar(select(func.count()).select_from(AccountRecoveryOperationRow)) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_code",
        "foreign_epoch",
        "bad_signature",
        "disabled",
        "deleted",
        "bound_key",
        "blocked_key",
    ],
)
def test_invalid_authority_cannot_consume_code(pair: PairingHarness, mutation: str) -> None:
    recovery, code, commit = configured(pair)
    if mutation == "wrong_code":
        code = new_code()
    elif mutation == "foreign_epoch":
        commit = request(pair, "recover", **{**commit, "expected_identity_epoch": 2})
    elif mutation == "bad_signature":
        commit["device_signature_b64url"] = "A" * 86
    elif mutation in {"disabled", "deleted"}:
        with Session(pair.engine) as session, session.begin():
            account = session.get(UserAccountRow, pair.actor.user_id)
            assert account is not None
            if mutation == "disabled":
                account.status = "DISABLED"
            else:
                account.deleted_at = datetime.now(UTC)
    else:
        with Session(pair.engine) as session, session.begin():
            digest = hashlib.sha256(public_spki(pair.key)).digest()
            if mutation == "bound_key":
                device = session.get(DeviceRow, pair.actor.device_id)
                assert device is not None
                device.public_key, device.public_key_thumbprint_sha256 = (
                    public_spki(pair.key),
                    digest,
                )
            else:
                session.add(
                    DeviceKeyBlockRow(
                        user_id=pair.actor.user_id, device_key_thumbprint_sha256=digest
                    )
                )
    with pytest.raises(AccountRecoveryError):
        recovery.recover(code, commit)
    with Session(pair.engine) as session:
        assert session.scalar(select(AccountRecoveryCredentialRow.generation)) == 1


def test_replay_denied_after_result_revocation(pair: PairingHarness) -> None:
    recovery, code, commit = configured(pair)
    result = recovery.recover(code, commit)
    with Session(pair.engine) as session, session.begin():
        required(session.get(DeviceRow, UUID(result["device_id"]))).revoked_at = datetime.now(UTC)
    with pytest.raises(AccountRecoveryError):
        recovery.recover(code, commit)


def test_lost_reply_after_receipt_expiry_recovers_only_with_result_refresh_proof(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    recovery, code = service(pair), new_code()
    recovery.configure(
        pair.actor,
        request(
            pair,
            "configure",
            expected_code_generation=0,
            next_code_verifier_sha256=verifier(pair, code),
        ),
    )
    refresh = secrets.token_urlsafe(32)
    commit = request(
        pair,
        "recover",
        expected_code_generation=1,
        next_code_verifier_sha256=verifier(pair, new_code()),
        next_refresh_token_sha256=hashlib.sha256(refresh.encode("ascii")).hexdigest(),
        binding_commit_id=str(uuid4()),
        confirmed_account_id=str(pair.actor.user_id),
    )
    original_receipt = recovery._new_receipt

    def expired_receipt(*args: Any, **kwargs: Any) -> AccountRecoveryOperationRow:
        receipt = original_receipt(*args, **kwargs)
        receipt.created_at -= timedelta(days=2)
        receipt.expires_at -= timedelta(days=2)
        return receipt

    monkeypatch.setattr(recovery, "_new_receipt", expired_receipt)
    committed = recovery.recover(code, commit)
    with pytest.raises(AccountRecoveryError):
        recovery.recover(code, commit)
    assert cleanup_expired_recovery_operations(recovery.sessions) == 0
    with pytest.raises(AccountRecoveryError):
        recovery.recover_outcome(secrets.token_urlsafe(32), commit)

    recovered = recovery.recover_outcome(refresh, commit)
    assert recovered["outcome_recovered"] is True
    assert recovered["replayed"] is True
    assert recovered["device_id"] == committed["device_id"]
    assert recovered["session_id"] == committed["session_id"]

    with Session(pair.engine) as session, session.begin():
        required(
            session.get(UserSessionRow, UUID(committed["session_id"]))
        ).revoked_at = datetime.now(UTC)
    assert cleanup_expired_recovery_operations(recovery.sessions, limit=1) == 1


def test_sql_receipts_and_generation_cannot_be_rewritten(pair: PairingHarness) -> None:
    configured(pair)
    for statement in (
        "UPDATE account.account_recovery_operation SET expires_at=expires_at+interval '1 second'",
        "DELETE FROM account.account_recovery_operation",
        "UPDATE account.account_recovery_credential SET generation=generation+2",
        "DELETE FROM account.account_recovery_credential",
    ):
        with pytest.raises(DBAPIError), pair.engine.begin() as connection:
            connection.execute(text(statement))


def test_recovery_downgrade_empty_roundtrip_and_refuses_evidence(
    database_harness: DatabaseHarness,
    database_name: str,
    pair: PairingHarness,
) -> None:
    database_harness.downgrade(database_name, "0050_metadata_execution")
    database_harness.upgrade(database_name)
    configured(pair)
    with pytest.raises(DBAPIError, match="Refusing to discard account recovery evidence"):
        database_harness.downgrade(database_name, "0050_metadata_execution")


def test_downgrade_waits_for_inflight_credential_before_checking_evidence(
    database_harness: DatabaseHarness,
    database_name: str,
    pair: PairingHarness,
) -> None:
    # This regression observes 0051's lock, not an earlier lock from newer downgrades.
    database_harness.downgrade(database_name, "0051_account_recovery")
    with Session(pair.engine) as session:
        now = datetime.now(UTC)
        session.add(
            AccountRecoveryCredentialRow(
                user_id=pair.actor.user_id,
                server_instance_id=UUID(pair.identity["expected_server_instance_id"]),
                identity_epoch=1,
                identity_thumbprint_sha256=bytes.fromhex(
                    pair.identity["expected_identity_thumbprint_sha256"]
                ),
                generation=1,
                verifier_sha256=bytes.fromhex(verifier(pair, new_code())),
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                database_harness.downgrade, database_name, "0050_metadata_execution"
            )
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with pair.engine.connect() as connection:
                        waiting = connection.scalar(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted "
                                "AND mode='AccessExclusiveLock' AND "
                                "relation='account.account_recovery_credential'::regclass)"
                            )
                        )
                    if waiting:
                        break
                    time.sleep(0.02)
                assert waiting and not future.done()
            finally:
                session.commit()
            with pytest.raises(DBAPIError, match="Refusing to discard account recovery evidence"):
                future.result(timeout=5)
    with Session(pair.engine) as session:
        assert session.get(AccountRecoveryCredentialRow, pair.actor.user_id) is not None


def test_cleanup_deletes_only_expired_receipts(pair: PairingHarness) -> None:
    recovery, _, _ = configured(pair)
    assert cleanup_expired_recovery_operations(recovery.sessions) == 0
    with Session(pair.engine) as session, session.begin():
        existing = required(session.scalar(select(AccountRecoveryOperationRow)))
        session.add(
            AccountRecoveryOperationRow(
                operation_id=uuid4(),
                kind="CONFIGURE",
                user_id=existing.user_id,
                server_instance_id=existing.server_instance_id,
                identity_epoch=existing.identity_epoch,
                request_sha256=b"z" * 32,
                actor_device_id=existing.actor_device_id,
                actor_family_id=existing.actor_family_id,
                previous_generation=0,
                result_generation=1,
                authority_generation=1,
                next_verifier_sha256=b"z" * 32,
                created_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(days=1, seconds=1),
            )
        )
    assert cleanup_expired_recovery_operations(recovery.sessions, limit=1) == 1
    assert recovery.status(pair.actor)["code_generation"] == 1

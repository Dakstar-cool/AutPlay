"""Real PostgreSQL deletion suspension, cancellation, loss and ownership races."""

from __future__ import annotations

import base64
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionRequestRow
from autplay.adapters.postgresql.models.account_recovery import AccountRecoveryCredentialRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.public_access import AccountProvisioningLinkRow
from autplay.adapters.postgresql.models.social import (
    FriendshipRow,
    PresenceSettingsRow,
    ProfileStatisticsSettingsRow,
)
from autplay.adapters.postgresql.models.web_admin import WebSessionRow
from autplay.adapters.postgresql.models.web_passkeys import WebPasskeyRow
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.account_recovery import cleanup_expired_recovery_operations
from autplay.application.public_access import PublicAccessService
from autplay.domain.account_deletion import AccountDeletionError
from autplay.domain.account_recovery import AccountRecoveryError, new_code
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import canonical_sha256, public_spki, sign_p1363

from . import test_self_device_pairing as pairing_fixtures
from .conftest import DatabaseHarness
from .test_account_recovery import configured, required, seed_authority, verifier
from .test_account_recovery import request as recovery_request
from .test_self_device_pairing import PairingHarness as BasePairingHarness

base_pair = pairing_fixtures.pair


@dataclass
class PairingHarness(BasePairingHarness):
    deletion_ledger: FilesystemDeletionLedger


@pytest.fixture
def pair(base_pair: BasePairingHarness, tmp_path: Path) -> PairingHarness:
    ledger = FilesystemDeletionLedger(tmp_path / "deletion.sqlite3", b"d" * 32, "fixture-v1")
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=2))
    return PairingHarness(**vars(base_pair), deletion_ledger=ledger)


def request(
    pair: PairingHarness, kind: str, key: ec.EllipticCurvePrivateKey, **extra: Any
) -> dict[str, Any]:
    public = public_spki(key)
    body = {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        **pair.identity,
        "account_id": str(pair.actor.user_id),
        "device_public_key_spki_b64": base64.b64encode(public).decode(),
        "device_key_thumbprint_sha256": hashlib.sha256(public).hexdigest(),
        "device_name": "Cancellation phone",
        "platform": "ANDROID",
        "app_version": "test-1",
        **extra,
    }
    body.pop("request_sha256", None)
    body.pop("device_signature_b64url", None)
    digest = canonical_sha256(body)
    body["request_sha256"] = digest.hex()
    body["device_signature_b64url"] = sign_p1363(
        key, f"autplay:account-deletion:{kind}:v1\n", digest
    )
    return body


def prepared(
    pair: PairingHarness,
) -> tuple[AccountDeletionService, str, ec.EllipticCurvePrivateKey, dict[str, Any]]:
    recovery, code, _ = configured(pair)
    source_key = ec.generate_private_key(ec.SECP256R1())
    with Session(pair.engine) as session, session.begin():
        device = required(session.get(DeviceRow, pair.actor.device_id))
        device.public_key = public_spki(source_key)
        device.public_key_thumbprint_sha256 = hashlib.sha256(device.public_key).digest()
        device.device_key_generation += 1
    body = request(
        pair,
        "request",
        source_key,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        expected_authority_generation=1,
    )
    return AccountDeletionService(recovery, pair.deletion_ledger), code, source_key, body


def cancel_request(pair: PairingHarness, deletion: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return request(
        pair,
        "cancel",
        pair.key,
        deletion_request_id=deletion["deletion_request_id"],
        expected_revision=1,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        next_code_verifier_sha256=verifier(pair, new_code()),
        next_refresh_token_sha256=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        binding_commit_id=str(uuid4()),
        **extra,
    )


def test_request_revokes_authority_preserves_durable_social_data_and_replays_after_loss(
    pair: PairingHarness,
) -> None:
    service, code, _, body = prepared(pair)
    seed_authority(pair)
    other = uuid4()
    with Session(pair.engine) as session, session.begin():
        session.add(UserAccountRow(user_id=other, display_name="Unaffected", role="USER"))
        session.flush()
        low, high = sorted((other, pair.actor.user_id))
        session.add(FriendshipRow(lower_user_id=low, higher_user_id=high))
        session.add(
            PresenceSettingsRow(user_id=pair.actor.user_id, friend_presence_visibility_enabled=True)
        )
        session.add(
            ProfileStatisticsSettingsRow(
                user_id=pair.actor.user_id, friends_can_view_statistics=True
            )
        )
    result = service.request(pair.actor, code, body)
    assert result == {**service.request_receipt(code, body), "replayed": False}
    start = datetime.fromisoformat(result["requested_at"])
    assert datetime.fromisoformat(result["cancel_before"]) - start == timedelta(days=30)
    with Session(pair.engine) as session:
        account = required(session.get(UserAccountRow, pair.actor.user_id))
        assert (account.status, account.authority_generation, account.display_name) == (
            "DELETION_PENDING",
            2,
            "Invited user",
        )
        assert account.deleted_at is None
        assert required(session.get(UserAccountRow, other)).status == "ACTIVE"
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is not None
        assert required(session.scalar(select(WebSessionRow))).revoked_at is not None
        assert required(session.scalar(select(WebPasskeyRow))).revoked_at is not None
        assert session.get(FriendshipRow, (low, high)) is not None
        assert required(
            session.get(PresenceSettingsRow, pair.actor.user_id)
        ).friend_presence_visibility_enabled
        assert required(
            session.get(ProfileStatisticsSettingsRow, pair.actor.user_id)
        ).friends_can_view_statistics
        assert (
            required(session.get(AccountRecoveryCredentialRow, pair.actor.user_id)).generation == 1
        )
    with pytest.raises(AccountRecoveryError):
        service.recovery.preview(code, recovery_request(pair, "preview"))
    with pytest.raises(AccountDeletionError):
        service.request(pair.actor, code, body)


def test_explicit_cancellation_creates_only_one_new_binding_and_rotates_code(
    pair: PairingHarness,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    preview = service.preview(code, request(pair, "preview", pair.key))
    assert preview["deletion_request_id"] == deletion["deletion_request_id"]
    assert preview["confirmation_required"] and preview["account_label"]
    cancel = cancel_request(pair, deletion)
    result = service.cancel(code, cancel)
    again = service.cancel(code, cancel)
    assert result["session_id"] == again["session_id"] and again["replayed"]
    with Session(pair.engine) as session:
        assert required(session.get(UserAccountRow, pair.actor.user_id)).status == "ACTIVE"
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is not None
        assert required(session.get(DeviceRow, pair.actor.device_id)).revoked_at is not None
        assert (
            session.scalar(
                select(func.count()).select_from(DeviceRow).where(DeviceRow.revoked_at.is_(None))
            )
            == 1
        )
        assert (
            required(session.get(AccountRecoveryCredentialRow, pair.actor.user_id)).generation == 2
        )
        row = required(
            session.get(AccountDeletionRequestRow, UUID(deletion["deletion_request_id"]))
        )
        assert row.state == "CANCELLED" and row.revision == 2
    with pytest.raises(AccountDeletionError):
        service.cancel(code, cancel_request(pair, deletion))
    with pytest.raises(AccountRecoveryError):
        service.recovery.status(pair.actor)


@pytest.mark.parametrize("role", [AccountRole.OWNER, AccountRole.ADMIN, AccountRole.USER])
def test_all_roles_can_request_except_last_active_owner(
    pair: PairingHarness, role: AccountRole
) -> None:
    service, code, _, body = prepared(pair)
    with Session(pair.engine) as session, session.begin():
        required(session.get(UserAccountRow, pair.actor.user_id)).role = role.value
    actor = replace(pair.actor, role=role)
    if role is AccountRole.OWNER:
        with pytest.raises(AccountDeletionError, match="last_owner_required"):
            service.request(actor, code, body)
        assert service.status(actor)["reason"] == "last_owner_required"
        with Session(pair.engine) as session, session.begin():
            session.add(
                UserAccountRow(
                    user_id=uuid4(), display_name="Disabled owner", role="OWNER", status="DISABLED"
                )
            )
        with pytest.raises(AccountDeletionError, match="last_owner_required"):
            service.request(actor, code, body)
        with Session(pair.engine) as session, session.begin():
            session.add(
                UserAccountRow(user_id=uuid4(), display_name="Remaining owner", role="OWNER")
            )
    assert service.request(actor, code, body)["state"] == "PENDING"


def test_sql_owner_guard_serializes_two_simultaneous_retirements(pair: PairingHarness) -> None:
    identifiers = [uuid4(), uuid4()]
    with Session(pair.engine) as session, session.begin():
        for identifier in identifiers:
            session.add(UserAccountRow(user_id=identifier, display_name="Owner", role="OWNER"))

    gate = Barrier(2)

    def retire(identifier: UUID) -> str:
        gate.wait(timeout=5)
        try:
            with Session(pair.engine) as session, session.begin():
                session.execute(
                    text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:id"),
                    {"id": identifier},
                )
            return "retired"
        except DBAPIError as error:
            assert "last_owner_required" in str(error)
            return "protected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(retire, identifiers)) == ["protected", "retired"]
    with Session(pair.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserAccountRow)
                .where(UserAccountRow.role == "OWNER", UserAccountRow.status == "ACTIVE")
            )
            == 1
        )


def test_owner_guard_rejects_stale_repeatable_read_snapshot(pair: PairingHarness) -> None:
    identifiers = [uuid4(), uuid4()]
    with Session(pair.engine) as session, session.begin():
        for identifier in identifiers:
            session.add(UserAccountRow(user_id=identifier, display_name="Owner", role="OWNER"))
    statement = text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:id")
    count = text("SELECT count(*) FROM account.user_account WHERE role='OWNER' AND status='ACTIVE'")
    with (
        pair.engine.connect().execution_options(isolation_level="REPEATABLE READ") as stale,
        stale.begin(),
    ):
        assert stale.scalar(count) == 2
        with pair.engine.begin() as first:
            first.execute(statement, {"id": identifiers[0]})
        with pytest.raises(DBAPIError) as failure:
            stale.execute(statement, {"id": identifiers[1]})
        assert getattr(failure.value.orig, "sqlstate", None) == "40001"
    with pair.engine.connect() as connection:
        assert connection.scalar(count) == 1


def test_two_requests_cannot_extend_deadline_or_branch_intent(pair: PairingHarness) -> None:
    service, code, key, first = prepared(pair)
    second = request(pair, "request", key, **{**first, "operation_id": str(uuid4())})

    gate = Barrier(2)

    def submit(body: dict[str, Any]) -> str:
        gate.wait(timeout=5)
        try:
            return str(service.request(pair.actor, code, body)["deletion_request_id"])
        except AccountDeletionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (first, second)))
    assert results.count("denied") == 1
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(AccountDeletionRequestRow)) == 1


def test_admin_disable_veto_cannot_be_undone_by_cancellation(pair: PairingHarness) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)
    now = datetime.now(UTC)
    with Session(pair.engine) as session, session.begin():
        session.add(UserAccountRow(user_id=owner.user_id, display_name="Owner", role="OWNER"))
        session.flush()
        session.add(
            DeviceRow(
                device_id=owner.device_id,
                user_id=owner.user_id,
                device_name="Owner phone",
                platform="ANDROID",
                app_version="test",
            )
        )
        session.flush()
        session.add(
            UserSessionRow(
                session_id=owner.session_id,
                user_id=owner.user_id,
                device_id=owner.device_id,
                refresh_token_hash=hashlib.sha256(secrets.token_bytes(32)).digest(),
                issued_at=now,
                expires_at=now + timedelta(days=1),
            )
        )
    assert isinstance(service.recovery.access, Hs256AccessTokenCodec)
    provisioning = PublicAccessService(
        service.sessions,
        service.recovery.access,
        service.recovery.ttl,
        b"deletion-provisioning-source-secret-32bytes",
    )
    invitation, _ = provisioning.create_invitation(
        owner,
        {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "account_display_name": "Invited user",
            "expires_in_seconds": 600,
        },
    )
    with Session(pair.engine) as session, session.begin():
        session.add(
            AccountProvisioningLinkRow(
                user_id=pair.actor.user_id,
                invitation_id=UUID(str(invitation["invitation_id"])),
                issued_by_user_id=owner.user_id,
                created_at=now,
            )
        )
    items = provisioning.list_accounts(owner, 10)["items"]
    assert isinstance(items, list)
    assert items[0]["status"] == "DELETION_PENDING"
    command = {
        "contract_version": "v1",
        "schema_version": 1,
        "operation_id": str(uuid4()),
        "reason_code": "ACCESS_ENDED",
    }
    result = provisioning.disable_account(owner, pair.actor.user_id, command)
    assert result["outcome"] == "APPLIED" and result["terminal_state"] == "DISABLED"
    assert provisioning.disable_account(owner, pair.actor.user_id, command) == result

    with pytest.raises(AccountDeletionError):
        service.cancel(code, cancel_request(pair, deletion))
    with Session(pair.engine) as session:
        assert required(session.get(UserAccountRow, pair.actor.user_id)).status == "DISABLED"
        assert required(session.scalar(select(AccountDeletionRequestRow))).state == "PENDING"


def test_wrong_proof_rolls_back_cancellation_and_keeps_all_old_authority_revoked(
    pair: PairingHarness,
) -> None:
    service, code, _, body = prepared(pair)
    with pytest.raises(AccountRecoveryError):
        service.request(pair.actor, new_code(), body)
    with Session(pair.engine) as session:
        assert required(session.get(UserAccountRow, pair.actor.user_id)).status == "ACTIVE"
    deletion = service.request(pair.actor, code, body)
    with pytest.raises(AccountRecoveryError):
        service.cancel(new_code(), cancel_request(pair, deletion))
    with Session(pair.engine) as session:
        assert (
            required(session.get(UserAccountRow, pair.actor.user_id)).status == "DELETION_PENDING"
        )
        assert required(session.scalar(select(AccountDeletionRequestRow))).state == "PENDING"
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is not None
        assert (
            required(session.get(AccountRecoveryCredentialRow, pair.actor.user_id)).generation == 1
        )


def test_deadline_is_strict_and_immutable(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    deadline = datetime.fromisoformat(deletion["cancel_before"])
    # Time is sampled from PostgreSQL by production; inject the exact boundary, not a sleep.
    monkeypatch.setattr(
        "autplay.application.account_deletion.database_now", lambda session: deadline
    )
    with pytest.raises(AccountDeletionError):
        service.cancel(code, cancel_request(pair, deletion))
    with (
        pytest.raises(DBAPIError, match="not monotonic"),
        Session(pair.engine) as session,
        session.begin(),
    ):
        row = required(session.scalar(select(AccountDeletionRequestRow)))
        row.cancel_before += timedelta(days=1)
    with (
        pytest.raises(DBAPIError, match="cannot be discarded"),
        Session(pair.engine) as session,
        session.begin(),
    ):
        session.delete(required(session.scalar(select(AccountDeletionRequestRow))))


def test_invalid_purpose_and_changed_receipt_cannot_grant_access(pair: PairingHarness) -> None:
    service, code, key, body = prepared(pair)
    service.request(pair.actor, code, body)
    changed = request(pair, "request", key, **{**body, "device_name": "Changed"})
    with pytest.raises(AccountDeletionError):
        service.request_receipt(code, changed)
    preview = request(pair, "preview", pair.key)
    preview["device_signature_b64url"] = sign_p1363(
        pair.key, "autplay:account-recovery:preview:v1\n", bytes.fromhex(preview["request_sha256"])
    )
    with pytest.raises(AccountDeletionError):
        service.preview(code, preview)


def test_expired_cancel_receipt_is_retained_without_reopening_cancelled_intent(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    next_code = new_code()
    original_receipt = service.recovery._new_receipt

    def expired_receipt(*args: Any, **kwargs: Any) -> Any:
        receipt = original_receipt(*args, **kwargs)
        receipt.created_at -= timedelta(days=2)
        receipt.expires_at -= timedelta(days=2)
        return receipt

    # The deletion row retains its exact cancellation receipt even after its login result expires.
    monkeypatch.setattr(service.recovery, "_new_receipt", expired_receipt)
    cancel = request(
        pair,
        "cancel",
        pair.key,
        **{
            **cancel_request(pair, deletion),
            "next_code_verifier_sha256": verifier(pair, next_code),
        },
    )
    service.cancel(code, cancel)
    assert cleanup_expired_recovery_operations(service.sessions) == 0
    fresh_key = ec.generate_private_key(ec.SECP256R1())
    reused = request(
        pair,
        "cancel",
        fresh_key,
        **{
            **{
                k: v
                for k, v in cancel.items()
                if k not in {"device_public_key_spki_b64", "device_key_thumbprint_sha256"}
            },
            "expected_code_generation": 2,
            "next_code_verifier_sha256": verifier(pair, new_code()),
            "next_refresh_token_sha256": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            "binding_commit_id": str(uuid4()),
        },
    )
    with pytest.raises(AccountRecoveryError, match="operation_conflict"):
        service.cancel(next_code, reused)
    with Session(pair.engine) as session:
        assert required(session.scalar(select(AccountDeletionRequestRow))).state == "CANCELLED"
        assert (
            session.scalar(
                select(func.count()).select_from(DeviceRow).where(DeviceRow.revoked_at.is_(None))
            )
            == 1
        )


def test_lost_cancel_reply_after_receipt_expiry_requires_result_refresh_proof(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    next_code = new_code()
    refresh = secrets.token_urlsafe(32)
    original_receipt = service.recovery._new_receipt

    def expired_receipt(*args: Any, **kwargs: Any) -> Any:
        receipt = original_receipt(*args, **kwargs)
        receipt.created_at -= timedelta(days=2)
        receipt.expires_at -= timedelta(days=2)
        return receipt

    monkeypatch.setattr(service.recovery, "_new_receipt", expired_receipt)
    cancel = request(
        pair,
        "cancel",
        pair.key,
        deletion_request_id=deletion["deletion_request_id"],
        expected_revision=1,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        next_code_verifier_sha256=verifier(pair, next_code),
        next_refresh_token_sha256=hashlib.sha256(refresh.encode("ascii")).hexdigest(),
        binding_commit_id=str(uuid4()),
    )
    committed = service.cancel(code, cancel)
    with pytest.raises(AccountRecoveryError):
        service.cancel(code, cancel)
    assert cleanup_expired_recovery_operations(service.sessions) == 0
    with pytest.raises(AccountDeletionError):
        service.cancel_outcome(secrets.token_urlsafe(32), cancel)

    recovered = service.cancel_outcome(refresh, cancel)
    assert recovered["outcome_recovered"] is True
    assert recovered["replayed"] is True
    assert recovered["device_id"] == committed["device_id"]
    assert recovered["session_id"] == committed["session_id"]

    with Session(pair.engine) as session, session.begin():
        required(
            session.get(UserSessionRow, UUID(committed["session_id"]))
        ).revoked_at = datetime.now(UTC)
    assert cleanup_expired_recovery_operations(service.sessions, limit=1) == 1


def test_pending_insert_and_null_purge_time_cannot_bypass_lifecycle(pair: PairingHarness) -> None:
    with (
        pytest.raises(DBAPIError, match="lifecycle and account disagree"),
        Session(pair.engine) as session,
        session.begin(),
    ):
        session.add(
            UserAccountRow(
                user_id=uuid4(), display_name="Invalid", role="USER", status="DELETION_PENDING"
            )
        )
    service, code, _, body = prepared(pair)
    service.request(pair.actor, code, body)
    with (
        pytest.raises(DBAPIError, match="deletion_request_state_check"),
        Session(pair.engine) as session,
        session.begin(),
    ):
        row = required(session.scalar(select(AccountDeletionRequestRow)))
        row.state, row.revision = "PURGING", 2


def test_stale_identity_cannot_advertise_usable_cancellation_proof(pair: PairingHarness) -> None:
    service, _, _, _ = prepared(pair)
    assert service.status(pair.actor)["can_request"]
    with Session(pair.engine) as session, session.begin():
        identity = required(session.scalar(select(ServerInstanceRow)))
        identity.identity_epoch += 1
    status = service.status(pair.actor)
    assert not status["can_request"] and status["reason"] == "recovery_setup_required"


def test_deletion_downgrade_roundtrip_and_refuses_pending_evidence(
    database_harness: DatabaseHarness, database_name: str, pair: PairingHarness
) -> None:
    database_harness.downgrade(database_name, "0051_account_recovery")
    database_harness.upgrade(database_name)
    service, code, _, body = prepared(pair)
    service.request(pair.actor, code, body)
    with pytest.raises(DBAPIError, match="Refusing to discard account deletion evidence"):
        database_harness.downgrade(database_name, "0051_account_recovery")
    assert service.request_receipt(code, body)["state"] == "PENDING"


def test_two_cancellations_cannot_create_two_bindings(pair: PairingHarness) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    gate = Barrier(2)

    def cancel(_: int) -> str:
        command = cancel_request(pair, deletion)
        gate.wait(timeout=5)
        try:
            return str(service.cancel(code, command)["session_id"])
        except AccountDeletionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cancel, (1, 2)))
    assert results.count("denied") == 1
    with Session(pair.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UserSessionRow)
                .where(UserSessionRow.revoked_at.is_(None))
            )
            == 1
        )

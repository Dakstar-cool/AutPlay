"""Historical absence is not a negative; sealed requests remain vetoed across restores."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.models import UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionRequestRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.deletion_request_evidence import enforce_request_restore_guard
from autplay.application.privacy_deletion import PrivacyDeletionService
from autplay.domain.account_deletion import AccountDeletionError
from autplay.domain.account_recovery import new_code, requested_at
from autplay.domain.privacy_deletion import DeletionEvidenceError
from autplay.domain.profile_pairing import iso8601
from autplay.entrypoints import privacy_admin
from autplay.runtime.settings import WorkerSettings
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_account_deletion import PairingHarness, cancel_request, prepared, request
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_account_recovery import request as recovery_request
from .test_account_recovery import required, verifier


@contextmanager
def restored_copy(pair: PairingHarness, harness: DatabaseHarness) -> Iterator[PairingHarness]:
    original = required(pair.engine.url.database)
    pair.engine.dispose()
    copied = harness.create_database(template=original)
    engine = create_engine(harness.database_url(copied))
    try:
        yield replace(pair, engine=engine)
    finally:
        engine.dispose()
        harness.drop_database(copied)


def abort_commit(pair: PairingHarness, action: str) -> None:
    assert action in {"INSERT", "UPDATE"}
    with pair.engine.begin() as connection:
        connection.execute(
            text(f"""
        CREATE FUNCTION account.test_deletion_abort() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'deletion commit fixture abort'; END $$;
        CREATE CONSTRAINT TRIGGER test_deletion_abort AFTER {action}
        ON account.account_deletion_request DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION account.test_deletion_abort();
        """)
        )


def remove_abort(pair: PairingHarness) -> None:
    with pair.engine.begin() as connection:
        connection.execute(
            text("""
        DROP TRIGGER test_deletion_abort ON account.account_deletion_request;
        DROP FUNCTION account.test_deletion_abort();
        """)
        )


def expired(pair: PairingHarness) -> tuple[AccountDeletionService, str, dict[str, object]]:
    service, code, key, body = prepared(pair)
    body = request(
        pair,
        "request",
        key,
        **{
            name: value
            for name, value in body.items()
            if name not in {"requested_at", "request_sha256", "device_signature_b64url"}
        },
        requested_at=iso8601(datetime.now(UTC) - timedelta(minutes=3)),
    )
    return service, code, body


def test_expired_never_submitted_request_has_durable_exact_negative_and_no_authority_change(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, code, body = expired(pair)
    result = service.request_resolve(code, body)
    assert result["state"] == "NOT_ACCEPTED" and not result["replayed"]
    assert result == {**service.request_resolve(code, body), "replayed": False}
    with Session(pair.engine) as session:
        account = required(session.get(UserAccountRow, pair.actor.user_id))
        assert (account.status, account.authority_generation) == ("ACTIVE", 1)
        assert required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at is None
        assert session.scalar(select(AccountDeletionRequestRow)) is None
    monkeypatch.setattr("autplay.application.account_deletion.require_fresh", lambda *_: None)
    with pytest.raises(AccountDeletionError, match="operation_conflict"):
        service.request(pair.actor, code, body)
    with pytest.raises(AccountDeletionError):
        service.request_resolve(new_code(), body)
    enforce_request_restore_guard(service.sessions, pair.deletion_ledger)


@pytest.mark.parametrize("offset", [-120, 0, 3600])
def test_fresh_boundary_and_future_request_cannot_be_sealed(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
) -> None:
    service, code, key, body = prepared(pair)
    now = datetime.now(UTC)
    body = request(
        pair,
        "request",
        key,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        expected_authority_generation=1,
        requested_at=iso8601(now + timedelta(seconds=offset)),
    )
    monkeypatch.setattr("autplay.application.account_deletion.database_now", lambda _: now)
    with pytest.raises(AccountDeletionError, match="deletion_resolution_not_ready"):
        service.request_resolve(code, body)
    assert pair.deletion_ledger.request_read() == ()
    if offset == 0:
        assert service.request(pair.actor, code, body)["state"] == "PENDING"


def test_accepted_positive_survives_expiry_and_cancellation_with_original_code(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    service.cancel(code, cancel_request(pair, deletion))
    monkeypatch.setattr(
        "autplay.application.account_deletion.database_now",
        lambda _: datetime.now(UTC) + timedelta(days=2),
    )
    result = service.request_resolve(code, body)
    assert result["state"] == "CANCELLED" and result["replayed"]
    assert service.request_receipt(code, body) == result


def test_pre_acceptance_backup_cannot_turn_historical_acceptance_into_negative(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
) -> None:
    service, code, _, body = prepared(pair)
    with restored_copy(pair, database_harness) as restored:
        service.request(pair.actor, code, body)
        restored_service = AccountDeletionService(
            service.recovery.__class__(
                sessionmaker(restored.engine, expire_on_commit=False),
                service.recovery.access,
                service.recovery.ttl,
                service.recovery.secret,
            ),
            pair.deletion_ledger,
        )
        with pytest.raises(DeletionEvidenceError):
            restored_service.request_resolve(code, body)
        with pytest.raises(DeletionEvidenceError):
            enforce_request_restore_guard(restored_service.sessions, pair.deletion_ledger)
        with Session(restored.engine) as session:
            assert required(session.get(UserAccountRow, restored.actor.user_id)).status == "ACTIVE"


def test_failed_acceptance_commit_retains_barrier_and_exact_fresh_retry_preserves_acceptance_time(
    pair: PairingHarness,
) -> None:
    service, code, _, body = prepared(pair)
    abort_commit(pair, "INSERT")
    with pytest.raises(DBAPIError, match="deletion commit fixture abort"):
        service.request(pair.actor, code, body)
    (evidence,) = pair.deletion_ledger.request_read()
    with pytest.raises(DeletionEvidenceError):
        service.request_resolve(code, body)
    remove_abort(pair)
    result = service.request(pair.actor, code, body)
    assert datetime.fromisoformat(result["requested_at"]) == evidence.decided_at
    assert pair.deletion_ledger.request_read() == (evidence,)
    enforce_request_restore_guard(service.sessions, pair.deletion_ledger)


def test_failed_cancellation_commit_blocks_receipt_preview_and_live_purge_until_exact_retry(
    pair: PairingHarness,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    cancel = cancel_request(pair, deletion)
    abort_commit(pair, "UPDATE")
    with pytest.raises(DBAPIError, match="deletion commit fixture abort"):
        service.cancel(code, cancel)
    (evidence,) = pair.deletion_ledger.request_read()
    assert evidence.cancel_operation_id == UUID(cancel["operation_id"])
    with Session(pair.engine) as session:
        assert (
            required(session.get(UserAccountRow, pair.actor.user_id)).status == "DELETION_PENDING"
        )
        assert (
            required(
                session.get(AccountDeletionRequestRow, UUID(deletion["deletion_request_id"]))
            ).state
            == "PENDING"
        )
    privacy = PrivacyDeletionService(service.sessions, pair.deletion_ledger)
    for action in (
        lambda: service.request_receipt(code, body),
        lambda: service.request_resolve(code, body),
        lambda: service.preview(code, request(pair, "preview", pair.key)),
        lambda: privacy.purge(pair.actor.user_id, UUID(deletion["deletion_request_id"])),
        lambda: enforce_request_restore_guard(service.sessions, pair.deletion_ledger),
    ):
        with pytest.raises(DeletionEvidenceError):
            action()
    remove_abort(pair)
    service.cancel(code, cancel)
    assert service.request_receipt(code, body)["state"] == "CANCELLED"
    assert pair.deletion_ledger.request_read() == (evidence,)


def test_pending_backup_cannot_undo_cancellation_or_authorize_purge(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
) -> None:
    service, code, _, body = prepared(pair)
    deletion = service.request(pair.actor, code, body)
    with restored_copy(pair, database_harness) as restored:
        service.cancel(code, cancel_request(pair, deletion))
        privacy = PrivacyDeletionService(
            sessionmaker(restored.engine, expire_on_commit=False), pair.deletion_ledger
        )
        with pytest.raises(DeletionEvidenceError):
            privacy.purge(pair.actor.user_id, UUID(deletion["deletion_request_id"]))
        with pytest.raises(DeletionEvidenceError):
            privacy.restore_guard()


def test_parallel_exact_negative_replies_keep_one_immutable_decision(pair: PairingHarness) -> None:
    service, code, body = expired(pair)
    barrier = Barrier(2)

    def resolve() -> dict[str, object]:
        barrier.wait(timeout=5)
        return service.request_resolve(code, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: resolve(), range(2)))
    assert {result["state"] for result in results} == {"NOT_ACCEPTED"}
    assert {result["replayed"] for result in results} == {True, False}
    assert len(pair.deletion_ledger.request_read()) == 1


def test_sealed_lost_reply_remains_queryable_with_original_code_after_code_rotation(
    pair: PairingHarness,
) -> None:
    service, code, body = expired(pair)
    first = service.request_resolve(code, body)
    rotated_code = new_code()
    service.recovery.configure(
        pair.actor,
        recovery_request(
            pair,
            "configure",
            expected_code_generation=1,
            next_code_verifier_sha256=verifier(pair, rotated_code),
        ),
    )
    assert service.request_resolve(code, body) == {**first, "replayed": True}
    with pytest.raises(AccountDeletionError):
        service.request_resolve(rotated_code, body)


def test_sealed_lost_reply_remains_queryable_after_identity_rotation(pair: PairingHarness) -> None:
    service, code, body = expired(pair)
    first = service.request_resolve(code, body)
    with Session(pair.engine) as session, session.begin():
        identity = required(session.scalar(select(ServerInstanceRow)))
        identity.identity_epoch += 1
        identity.identity_thumbprint_sha256 = b"n" * 32
    assert service.request_resolve(code, body) == {**first, "replayed": True}
    with pytest.raises(AccountDeletionError):
        service.request_resolve(new_code(), body)


def test_sealed_lost_reply_remains_queryable_after_later_account_purge(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, code, key, _ = prepared(pair)
    body = request(
        pair,
        "request",
        key,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        expected_authority_generation=1,
        requested_at=iso8601(datetime.now(UTC) - timedelta(minutes=3)),
    )
    first = service.request_resolve(code, body)
    later = request(
        pair,
        "request",
        key,
        confirmed_account_id=str(pair.actor.user_id),
        expected_code_generation=1,
        expected_authority_generation=1,
    )
    accepted_at = datetime.now(UTC) - timedelta(days=31)
    monkeypatch.setattr("autplay.application.account_deletion.database_now", lambda _: accepted_at)
    monkeypatch.setattr("autplay.application.account_deletion.require_fresh", lambda *_: None)
    operation = UUID(service.request(pair.actor, code, later)["deletion_request_id"])
    privacy = PrivacyDeletionService(service.sessions, pair.deletion_ledger)
    privacy.purge(pair.actor.user_id, operation)
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
    assert service.request_resolve(code, body) == {**first, "replayed": True}
    with pytest.raises(AccountDeletionError):
        service.request_resolve(new_code(), body)


def test_offline_cutover_provisioning_uses_postgresql_clock(
    pair: PairingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "operator-cutover.sqlite3"
    settings = WorkerSettings(
        database_url=SecretStr(pair.engine.url.render_as_string(hide_password=False)),
        privacy_ledger_path=path,
        privacy_ledger_key=SecretStr("operator-fixture-key-at-least-32-bytes"),
        privacy_ledger_key_id="operator-v1",
    )
    monkeypatch.setattr(privacy_admin, "load_worker_settings", lambda: settings)
    monkeypatch.setattr(privacy_admin, "create_runtime_engine", lambda _: pair.engine)
    with pair.engine.connect() as connection:
        before = connection.scalar(text("SELECT clock_timestamp()"))
    assert privacy_admin.main(["initialize-ledger"]) == 0
    assert capsys.readouterr().out == '{"initialized":true}\n'
    with pair.engine.connect() as connection:
        after = connection.scalar(text("SELECT clock_timestamp()"))
    ledger = FilesystemDeletionLedger(
        path, b"operator-fixture-key-at-least-32-bytes", "operator-v1"
    )
    assert before <= ledger.request_coverage_started_at() <= after


def test_restore_without_any_owner_or_request_still_cannot_erase_attempt_evidence(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
) -> None:
    service, code, _, body = prepared(pair)
    service.request(pair.actor, code, body)
    empty = database_harness.create_database()
    engine = create_engine(database_harness.database_url(empty))
    try:
        database_harness.upgrade(empty)
        with pytest.raises(DeletionEvidenceError):
            enforce_request_restore_guard(
                sessionmaker(engine, expire_on_commit=False), pair.deletion_ledger
            )
    finally:
        engine.dispose()
        database_harness.drop_database(empty)


@pytest.mark.parametrize("seconds_after_cutover", [-1, 0, 120])
def test_legacy_and_old_acceptors_future_window_cannot_receive_negative(
    pair: PairingHarness,
    tmp_path: Path,
    seconds_after_cutover: int,
) -> None:
    service, code, body = expired(pair)
    ledger = FilesystemDeletionLedger(tmp_path / "cutover.sqlite3", b"c" * 32, "cutover-v1")
    ledger.initialize(
        coverage_started_at=requested_at(body) - timedelta(seconds=seconds_after_cutover)
    )
    service = AccountDeletionService(service.recovery, ledger)
    with pytest.raises(DeletionEvidenceError):
        service.request_resolve(code, body)
    assert ledger.request_read() == ()


@pytest.mark.parametrize("age", [0, 120, 240, 241])
def test_cutover_readiness_keeps_client_from_detaching_during_ambiguous_time_window(
    pair: PairingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    age: int,
) -> None:
    service, _, _, _ = prepared(pair)
    now = datetime.now(UTC)
    ledger = FilesystemDeletionLedger(tmp_path / "warming.sqlite3", b"w" * 32, "warm-v1")
    ledger.initialize(coverage_started_at=now - timedelta(seconds=age))
    service = AccountDeletionService(service.recovery, ledger)
    monkeypatch.setattr("autplay.application.account_deletion.database_now", lambda _: now)
    status = service.status(pair.actor)
    assert status["can_request"] == (age > 240)
    assert status["reason"] == (None if age > 240 else "deletion_initializing")

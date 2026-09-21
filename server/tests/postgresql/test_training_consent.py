"""Real account-policy concurrency, rollback, exact replay and deletion evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.training_consent import (
    TrainingConsentOperationRow,
    TrainingConsentRow,
)
from autplay.application.training_consent import TrainingConsentError, TrainingConsentService
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_account_deletion import PairingHarness as DeletionPairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_self_device_pairing import PairingHarness as BasePairingHarness


@dataclass
class PairingHarness(DeletionPairingHarness):
    consent_ledger: FilesystemTrainingConsentLedger


@pytest.fixture
def pair(base_pair: BasePairingHarness, tmp_path: Path) -> PairingHarness:
    deletion_ledger = FilesystemDeletionLedger(
        tmp_path / "deletion.sqlite3", b"d" * 32, "fixture-v1"
    )
    deletion_ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=2))
    deletion_pair = DeletionPairingHarness(**vars(base_pair), deletion_ledger=deletion_ledger)
    ledger = FilesystemTrainingConsentLedger(tmp_path / "consent.sqlite3", b"s" * 32, "fixture-v1")
    ledger.initialize()
    return PairingHarness(**vars(deletion_pair), consent_ledger=ledger)


def service(pair: PairingHarness) -> TrainingConsentService:
    return TrainingConsentService(
        sessionmaker(pair.engine, expire_on_commit=False), pair.consent_ledger
    )


def command(pair: PairingHarness, decision: str, revision: int = 0) -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "account_id": str(pair.actor.user_id),
        "decision": decision,
        "expected_revision": revision,
        "policy_version": 1,
    }


def test_default_private_decision_replay_does_not_restore_old_grant(pair: PairingHarness) -> None:
    policy = service(pair)
    assert policy.get(pair.actor)["decision"] == "UNKNOWN"
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0
    grant = command(pair, "GRANTED")
    assert policy.decide(pair.actor, grant)["revision"] == 1
    assert policy.decide(pair.actor, grant)["revision"] == 1
    policy.decide(pair.actor, command(pair, "WITHDRAWN", 0))
    replay = policy.decide(pair.actor, grant)
    assert (
        replay["decision"],
        replay["revision"],
        replay["applied_decision"],
        replay["applied_revision"],
    ) == ("WITHDRAWN", 2, "GRANTED", 1)
    assert policy.get(pair.actor)["decision"] == "WITHDRAWN"
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentOperationRow)) == 2
    with pytest.raises(TrainingConsentError, match="consent_revision_conflict"):
        policy.decide(pair.actor, command(pair, "GRANTED", 1))
    assert policy.decide(pair.actor, command(pair, "GRANTED", 2))["revision"] == 3
    with Session(pair.engine) as session, session.begin():
        with pytest.raises(TrainingConsentError, match="training_consent_required"):
            policy.require_granted(session, pair.actor.user_id, 1)
        policy.require_granted(session, pair.actor.user_id, 3)


def test_refusal_from_another_device_never_reprompts_or_changes_personal_data(
    pair: PairingHarness,
) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "DENIED"))
    assert policy.get(pair.actor)["decision"] == "DENIED"
    from .test_social_s1c import _device_principal

    with Session(pair.engine) as session, session.begin():
        second = _device_principal(session, pair.actor.user_id, "Second", policy._now(session))
    second = replace(second, role=pair.actor.role)
    assert policy.get(second)["revision"] == 1
    assert policy.get(second)["decision"] == "DENIED"
    assert policy.decide(second, command(pair, "GRANTED", 1))["revision"] == 2
    assert policy.get(pair.actor)["decision"] == "GRANTED"


@pytest.mark.parametrize("fault", ["account", "device", "session", "role", "wrong_account"])
def test_current_actor_is_rechecked_under_account_lock(pair: PairingHarness, fault: str) -> None:
    principal = pair.actor
    with Session(pair.engine) as session, session.begin():
        if fault == "account":
            session.execute(
                text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:u"),
                {"u": principal.user_id},
            )
        elif fault == "device":
            row = session.get(DeviceRow, principal.device_id)
            assert row is not None
            row.revoked_at = service(pair)._now(session)
        elif fault == "session":
            row2 = session.get(UserSessionRow, principal.session_id)
            assert row2 is not None
            row2.revoked_at = service(pair)._now(session)
        elif fault == "role":
            session.execute(
                text("UPDATE account.user_account SET role='ADMIN' WHERE user_id=:u"),
                {"u": principal.user_id},
            )
    body = command(pair, "GRANTED")
    if fault == "wrong_account":
        body["account_id"] = str(uuid4())
    with pytest.raises(TrainingConsentError):
        service(pair).decide(principal, body)
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0


def test_grant_refusal_race_finishes_private_and_old_revision_stays_invalid(
    pair: PairingHarness,
) -> None:
    barrier = Barrier(2)
    policy = service(pair)

    def run(decision: str) -> str:
        barrier.wait(timeout=5)
        try:
            return str(policy.decide(pair.actor, command(pair, decision))["decision"])
        except TrainingConsentError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["GRANTED", "WITHDRAWN"]))
    assert "WITHDRAWN" in results
    assert policy.get(pair.actor)["decision"] == "WITHDRAWN"
    with Session(pair.engine) as session, session.begin(), pytest.raises(TrainingConsentError):
        policy.require_granted(session, pair.actor.user_id, 1)


def test_changed_replay_is_rejected_and_receipts_are_immutable(pair: PairingHarness) -> None:
    policy = service(pair)
    body = command(pair, "DENIED")
    policy.decide(pair.actor, body)
    with pytest.raises(TrainingConsentError, match="operation_conflict"):
        policy.decide(pair.actor, {**body, "decision": "GRANTED", "expected_revision": 1})
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_consent_operation_immutable"),
    ):
        session.execute(text("DELETE FROM account.training_consent_operation"))


def test_transaction_failure_does_not_leave_a_policy_or_success_receipt(
    pair: PairingHarness,
) -> None:
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text("""CREATE FUNCTION account.test_consent_abort()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'consent fixture abort'; END $$;
        CREATE TRIGGER test_consent_abort BEFORE INSERT ON account.training_consent_operation
        FOR EACH ROW EXECUTE FUNCTION account.test_consent_abort();""")
        )
    with pytest.raises(DBAPIError, match="consent fixture abort"):
        service(pair).decide(pair.actor, command(pair, "GRANTED"))
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0


def test_final_purge_removes_policy_and_exact_receipts(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_privacy_purge import overdue

    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    request_id = overdue(pair, monkeypatch)
    with Session(pair.engine) as session, session.begin():
        removed = session.scalar(
            text("SELECT account.purge_account(:u,:r)"),
            {"u": pair.actor.user_id, "r": request_id},
        )
        assert isinstance(removed, int) and removed > 0
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0
        assert session.scalar(select(func.count()).select_from(TrainingConsentOperationRow)) == 0


def test_repeatable_read_snapshot_cannot_authorize_after_withdrawal(pair: PairingHarness) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    with (
        pair.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        Session(connection) as session,
        session.begin(),
    ):
        assert session.scalar(select(TrainingConsentRow.revision)) == 1
        policy.decide(pair.actor, command(pair, "WITHDRAWN", 1))
        with pytest.raises(DBAPIError) as failure:
            policy.require_granted(session, pair.actor.user_id, 1)
        assert getattr(failure.value.orig, "sqlstate", None) == "40001"


def test_downgrade_cannot_destroy_a_concurrently_committed_decision(
    pair: PairingHarness, database_harness: DatabaseHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_name = pair.engine.url.database
    assert database_name is not None
    database_harness.downgrade(database_name, "0054_training_consent")
    flushed, release = Event(), Event()
    original = TrainingConsentService._result

    def pause(cls: type[TrainingConsentService], *args: Any) -> dict[str, object]:
        result = original(*args)
        flushed.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(TrainingConsentService, "_result", classmethod(pause))
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(service(pair).decide, pair.actor, command(pair, "DENIED"))
        assert flushed.wait(5)
        downgrade = executor.submit(database_harness.downgrade, database_name, "0053_privacy_purge")
        try:
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline:
                with pair.engine.connect() as connection:
                    blocked = bool(
                        connection.scalar(
                            text("""SELECT EXISTS(SELECT 1 FROM pg_locks
                     WHERE relation IN ('account.training_consent'::regclass,
                       'account.user_account'::regclass)
                     AND mode='AccessExclusiveLock' AND NOT granted)""")
                        )
                    )
                if blocked:
                    break
                sleep(0.02)
            assert blocked
        finally:
            release.set()
        assert writer.result(timeout=5)["decision"] == "DENIED"
        with pytest.raises(DBAPIError, match="training_consent_evidence_retained"):
            downgrade.result(timeout=10)
    assert service(pair).get(pair.actor)["decision"] == "DENIED"

"""Actual closed-database clones prove independence from consent backup generations."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from threading import Event
from time import monotonic, sleep
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models.training_consent import (
    TrainingConsentOperationRow,
    TrainingConsentRow,
)
from autplay.adapters.postgresql.models.training_work import TrainingCleanupClaimRow, TrainingRunRow
from autplay.application.training_consent import MAX_REVISION, TrainingConsentError
from autplay.application.training_work import TrainingWorkError
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .conftest import DatabaseHarness
from .test_training_consent import PairingHarness, command, service
from .test_training_consent import base_pair as base_pair
from .test_training_consent import pair as pair
from .test_training_work import PairingHarness as WorkPairingHarness
from .test_training_work import hashes, registered, registry


def work_pair(pair: PairingHarness) -> WorkPairingHarness:
    """The two consent fixtures have the same deletion and ledger fields."""
    return cast(WorkPairingHarness, pair)


@contextmanager
def restored_copy(pair: PairingHarness, harness: DatabaseHarness) -> Iterator[PairingHarness]:
    original = pair.engine.url.database
    assert original is not None
    pair.engine.dispose()
    copied = harness.create_database(template=original)
    engine = create_engine(harness.database_url(copied))
    try:
        yield replace(pair, engine=engine)
    finally:
        engine.dispose()
        harness.drop_database(copied)


def abort_receipt(pair: PairingHarness) -> None:
    with pair.engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION account.test_restore_abort()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'private commit fixture abort'; END $$;
        CREATE TRIGGER test_restore_abort BEFORE INSERT ON account.training_consent_operation
        FOR EACH ROW EXECUTE FUNCTION account.test_restore_abort();""")
        )


def remove_abort(pair: PairingHarness) -> None:
    with pair.engine.begin() as connection:
        connection.execute(
            text("""DROP TRIGGER test_restore_abort ON account.training_consent_operation;
        DROP FUNCTION account.test_restore_abort();""")
        )


@pytest.mark.parametrize("phase", ["PREPARING", "READY", "RUNNING"])
def test_old_backup_cannot_restore_grant_run_or_checkpoint_authority(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
    phase: str,
) -> None:
    _, run = registered(work_pair(pair), phase)
    with restored_copy(pair, database_harness) as restored:
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
        restored_policy, restored_work = service(restored), registry(work_pair(restored))
        for action in (
            lambda: restored_policy.get(restored.actor),
            lambda: restored_work.register(
                uuid4(), source_sha256="a" * 64, participants={pair.actor.user_id: 1}
            ),
            lambda: restored_work.ready(run, "b" * 64),
            lambda: restored_work.start(run),
            lambda: restored_work.check_running(run),
            lambda: restored_work.publish(run, uuid4(), hashes()),
        ):
            with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
                action()
        assert restored_policy.restore_guard() == 1
        assert restored_policy.restore_guard() == 0
        assert restored_policy.get(restored.actor)["decision"] == "WITHDRAWN"
        with Session(restored.engine) as session:
            row = session.get(TrainingRunRow, run)
            assert row is not None and row.phase == "INVALIDATED"
            claim = session.get(TrainingCleanupClaimRow, run)
            assert claim is not None and claim.phase == "PENDING"
            assert (
                session.scalar(
                    select(TrainingConsentOperationRow.operation_id).where(
                        TrainingConsentOperationRow.applied_decision == "WITHDRAWN"
                    )
                )
                is None
            )  # System reconciliation never fabricates a user receipt.
        grant = restored_policy.decide(restored.actor, command(restored, "GRANTED", 2))
        assert grant["revision"] == 3
        with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
            restored_work.start(run)
        restored_work.register(
            uuid4(), source_sha256="a" * 64, participants={pair.actor.user_id: 3}
        )


def test_failed_private_commit_still_blocks_work_and_exact_retry_uses_original_evidence(
    pair: PairingHarness,
) -> None:
    work, run = registered(work_pair(pair))
    policy = service(pair)
    original_grant = next(iter(pair.consent_ledger.read().operations))
    abort_receipt(pair)
    private = command(pair, "WITHDRAWN", 1)
    with pytest.raises(DBAPIError, match="private commit fixture abort"):
        policy.decide(pair.actor, private)
    intent = pair.consent_ledger.read().operations[UUID(str(private["operation_id"]))]
    with Session(pair.engine) as session:
        row = session.get(TrainingRunRow, run)
        assert row is not None and row.phase == "RUNNING"
        assert session.get(TrainingConsentOperationRow, intent.operation_id) is None
        original = session.get(TrainingConsentOperationRow, original_grant)
        assert original is not None and original.applied_decision == "GRANTED"
    with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
        work.check_running(run)
    with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
        policy.get(pair.actor)
    with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
        policy.decide(pair.actor, {**command(pair, "GRANTED"), "operation_id": str(original_grant)})
    remove_abort(pair)
    result = policy.decide(pair.actor, private)
    assert result["decision"] == "WITHDRAWN" and result["revision"] == 2
    assert policy.decide(pair.actor, private) == result
    assert len(pair.consent_ledger.read().operations) == 2
    with Session(pair.engine) as session:
        receipt = session.get(TrainingConsentOperationRow, intent.operation_id)
        assert receipt is not None and receipt.created_at == intent.changed_at


def test_independent_sequence_refuses_same_revision_grant_from_another_restore_branch(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with restored_copy(pair, database_harness) as branch:
        old_branch_grant = command(pair, "GRANTED", 2)
        service(pair).decide(pair.actor, old_branch_grant)
        with restored_copy(pair, database_harness) as old_granted_copy:
            new_branch_grant = command(branch, "GRANTED", 2)
            assert service(branch).decide(branch.actor, new_branch_grant)["revision"] == 3
            # Both DB branches say GRANTED revision 3. Only the latest full intent may authorize.
            with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
                service(old_granted_copy).get(old_granted_copy.actor)
            with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
                service(old_granted_copy).decide(old_granted_copy.actor, old_branch_grant)
            assert service(old_granted_copy).restore_guard() == 1
            assert service(old_granted_copy).get(old_granted_copy.actor)["revision"] == 4
            # The new system barrier also fences the other branch's grant.
            with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
                service(branch).get(branch.actor)


def test_published_model_and_exact_publication_replay_survive_private_restore_reconciliation(
    pair: PairingHarness,
    database_harness: DatabaseHarness,
) -> None:
    work, run = registered(work_pair(pair))
    publication = uuid4()
    work.publish(run, publication, hashes())
    with restored_copy(pair, database_harness) as restored:
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
        assert service(restored).restore_guard() == 1
        restored_work = registry(work_pair(restored))
        assert restored_work.is_published(run, hashes())
        assert restored_work.publish(run, publication, hashes()).phase == "PUBLISHED"
        with Session(restored.engine) as session:
            claim = session.get(TrainingCleanupClaimRow, run)
            assert claim is not None and claim.phase == "PENDING"
        # Serving a completed publication does not require the live consent ledger.
        renamed = pair.consent_ledger.path.with_suffix(".offline")
        pair.consent_ledger.path.rename(renamed)
        try:
            assert restored_work.is_published(run, hashes())
            assert restored_work.publish(run, publication, hashes()).phase == "PUBLISHED"
        finally:
            renamed.rename(pair.consent_ledger.path)


def test_startup_barrier_supersedes_uncommitted_grant_and_requires_new_explicit_choice(
    pair: PairingHarness,
) -> None:
    abort_receipt(pair)
    grant = command(pair, "GRANTED")
    with pytest.raises(DBAPIError, match="private commit fixture abort"):
        service(pair).decide(pair.actor, grant)
    remove_abort(pair)
    assert service(pair).restore_guard() == 1
    assert service(pair).get(pair.actor)["decision"] == "DENIED"
    with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
        service(pair).decide(pair.actor, grant)
    assert service(pair).decide(pair.actor, command(pair, "GRANTED", 1))["decision"] == "GRANTED"


def test_terminal_private_policy_gets_durable_barrier_without_revision_overflow(
    pair: PairingHarness,
) -> None:
    abort_receipt(pair)
    with pytest.raises(DBAPIError, match="private commit fixture abort"):
        service(pair).decide(pair.actor, command(pair, "GRANTED"))
    remove_abort(pair)
    with pair.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO account.training_consent"
                "(user_id,decision,revision,policy_version,changed_at) "
                "VALUES(:u,'DENIED',:r,1,clock_timestamp())"
            ),
            {"u": pair.actor.user_id, "r": MAX_REVISION},
        )
    assert service(pair).restore_guard() == 1
    assert service(pair).restore_guard() == 0
    assert service(pair).get(pair.actor)["revision"] == MAX_REVISION
    with Session(pair.engine) as session:
        row = session.get(TrainingConsentRow, pair.actor.user_id)
        assert row is not None and row.decision == "DENIED"
    latest = pair.consent_ledger.read().latest[pair.consent_ledger.owner_tag(pair.actor.user_id)]
    assert latest.actor_tag is None and latest.decision == "WITHDRAWN"
    assert latest.revision == MAX_REVISION


def test_waiting_batch_rechecks_independent_private_intent_after_pg_rollback(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autplay.domain.training_consent import TrainingConsentIntent

    work, run = registered(work_pair(pair))
    abort_receipt(pair)
    persisted, release = Event(), Event()
    original = pair.consent_ledger.record

    def pause(item: TrainingConsentIntent) -> TrainingConsentIntent:
        result = original(item)
        persisted.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(pair.consent_ledger, "record", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(service(pair).decide, pair.actor, command(pair, "WITHDRAWN", 1))
        assert persisted.wait(5)
        reader = pool.submit(work.check_running, run)
        try:
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline:
                with pair.engine.connect() as connection:
                    blocked = bool(
                        connection.scalar(
                            text("""SELECT EXISTS(
                    SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                     AND wait_event='transactionid' AND query LIKE '%account.user_account%')""")
                        )
                    )
                if blocked:
                    break
                sleep(0.01)
            assert blocked
        finally:
            release.set()
        with pytest.raises(DBAPIError, match="private commit fixture abort"):
            writer.result(timeout=5)
        with pytest.raises(TrainingConsentError, match="training_consent_restore_attention"):
            reader.result(timeout=5)


def test_restore_guard_locks_all_affected_runs_before_first_policy_mutation(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work, first_run = registered(work_pair(pair))
    second_run = uuid4()
    work.register(second_run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(second_run, "b" * 64)
    work.start(second_run)
    abort_receipt(pair)
    with pytest.raises(DBAPIError, match="private commit fixture abort"):
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    remove_abort(pair)
    policy = service(pair)
    held, release = Event(), Event()
    original = policy._now

    def pause(session: Session) -> datetime:
        now = original(session)
        held.set()
        assert release.wait(10)
        return now

    monkeypatch.setattr(policy, "_now", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        guard = pool.submit(policy.restore_guard)
        assert held.wait(5)
        try:
            for run in (first_run, second_run):
                with pytest.raises(DBAPIError) as failure, pair.engine.begin() as connection:
                    connection.execute(
                        text(
                            "SELECT run_id FROM ml.training_run WHERE run_id=:r FOR UPDATE NOWAIT"
                        ),
                        {"r": run},
                    )
                assert getattr(failure.value.orig, "sqlstate", None) == "55P03"
        finally:
            release.set()
        assert guard.result(timeout=5) == 1
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.check_running(second_run)

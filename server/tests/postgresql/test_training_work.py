"""Publication/withdrawal linearization and retained current-authority evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import TypedDict, cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.models.account import UserAccountRow
from autplay.adapters.postgresql.models.training_consent import TrainingConsentRow
from autplay.adapters.postgresql.models.training_work import (
    TrainingCleanupClaimRow,
    TrainingParticipantRow,
    TrainingPublicationRevocationRow,
    TrainingRunRow,
)
from autplay.adapters.postgresql.training_execution import PostgresTrainingExecutionRepository
from autplay.adapters.postgresql.training_publication import (
    PostgresTrainingPublicationAuthority,
)
from autplay.application.training_consent import (
    MAX_REVISION,
    TrainingConsentError,
    TrainingConsentService,
)
from autplay.application.training_work import (
    PUBLICATION_HASH_KEYS,
    TrainingWorkError,
    TrainingWorkService,
)
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence
from autplay.domain.training_execution import TrainingExecutionTicket
from autplay.domain.vault import Sha256Digest
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


@pytest.fixture(name="pair")
def training_pair(base_pair: BasePairingHarness, tmp_path: Path) -> PairingHarness:
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


def required[T](value: T | None) -> T:
    assert value is not None
    return value


class BoundInputs(TypedDict):
    source_sha256: str
    dataset_sha256: str
    lineage_key_id: str
    owner_tokens: tuple[str, ...]


def registry(pair: PairingHarness) -> TrainingWorkService:
    return TrainingWorkService(
        sessionmaker(pair.engine, expire_on_commit=False),
        server_instance_id=UUID(pair.identity["expected_server_instance_id"]),
        identity_epoch=pair.identity["expected_identity_epoch"],
        lineage_key_id="fixture-v1",
        lineage_key=b"k" * 32,
        consent_ledger=pair.consent_ledger,
    )


def registered(pair: PairingHarness, phase: str = "RUNNING") -> tuple[TrainingWorkService, UUID]:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    if phase in {"READY", "RUNNING"}:
        work.ready(run, "b" * 64)
    if phase == "RUNNING":
        work.start(run)
        from hashlib import sha256
        from hmac import new as new_hmac

        provenance = work.authorize_input(
            run,
            source_sha256="a" * 64,
            dataset_sha256="b" * 64,
            lineage_key_id="fixture-v1",
            owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
        )
        work.seal_checkpoint(provenance, "c" * 64)
    return work, run


def hashes() -> dict[str, str]:
    return {key: "c" * 64 for key in PUBLICATION_HASH_KEYS}


def test_registration_is_current_and_exact_membership_is_sealed(pair: PairingHarness) -> None:
    work, run = registry(pair), uuid4()
    with pytest.raises(TrainingConsentError, match="training_consent_required"):
        work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work, run = registered(pair, "PREPARING")
    assert (
        work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1}).revision
        == 1
    )
    with pytest.raises(TrainingWorkError, match="training_operation_conflict"):
        work.register(run, source_sha256="d" * 64, participants={pair.actor.user_id: 1})
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_participant_evidence_retained"),
    ):
        session.execute(
            text("INSERT INTO ml.training_participant VALUES(:run,:owner,1,:tag)"),
            {"run": run, "owner": pair.actor.user_id, "tag": b"z" * 32},
        )
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingParticipantRow)) == 1


def test_incomplete_initial_registration_rolls_back_at_commit(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    now = datetime.now(UTC)
    with (
        pytest.raises(DBAPIError, match="training_participant_set_incomplete"),
        Session(pair.engine) as session,
        session.begin(),
    ):
        session.add(
            TrainingRunRow(
                run_id=uuid4(),
                server_instance_id=UUID(pair.identity["expected_server_instance_id"]),
                identity_epoch=pair.identity["expected_identity_epoch"],
                lineage_key_id="fixture-v1",
                source_sha256=b"a" * 32,
                request_sha256=b"r" * 32,
                participant_count=1,
                phase="PREPARING",
                revision=1,
                created_at=now,
                changed_at=now,
            )
        )
        session.flush()
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingRunRow)) == 0


@pytest.mark.parametrize("phase", ["PREPARING", "READY", "RUNNING"])
def test_withdrawal_terminalizes_work_and_regrant_never_revives_it(
    pair: PairingHarness, phase: str
) -> None:
    work, run = registered(pair, phase)
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with Session(pair.engine) as session:
        assert required(session.get(TrainingRunRow, run)).phase == "INVALIDATED"
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"
    service(pair).decide(pair.actor, command(pair, "GRANTED", 2))
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.check_running(run)
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.publish(run, uuid4(), hashes())
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_run_transition_rejected"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_run SET phase='RUNNING',revision=revision+1 WHERE run_id=:run"
            ),
            {"run": run},
        )


def test_redundant_grant_revision_also_invalidates_and_queues_cleanup(pair: PairingHarness) -> None:
    work, run = registered(pair)
    service(pair).decide(pair.actor, command(pair, "GRANTED", 1))
    with Session(pair.engine) as session:
        assert required(session.get(TrainingRunRow, run)).phase == "INVALIDATED"
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.check_running(run)


def test_publication_exact_replay_and_serving_survive_later_withdrawal(
    pair: PairingHarness,
) -> None:
    work, run = registered(pair)
    operation, publication = uuid4(), hashes()
    assert not work.is_published(run, publication)
    result = work.publish(run, operation, publication)
    assert result.phase == "PUBLISHED"
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    assert work.publish(run, operation, publication) == result
    assert work.is_published(run, publication)
    altered = {**publication, "artifact_sha256": "d" * 64}
    assert not work.is_published(run, altered)
    with pytest.raises(TrainingWorkError, match="training_operation_conflict"):
        work.publish(run, operation, altered)
    with Session(pair.engine) as session:
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"
        assert required(session.get(TrainingRunRow, run)).phase == "PUBLISHED"


def test_publication_operation_cannot_be_reused_for_another_run(pair: PairingHarness) -> None:
    work, first = registered(pair)
    second = uuid4()
    work.register(second, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(second, "b" * 64)
    work.start(second)
    from hashlib import sha256
    from hmac import new as new_hmac

    provenance = work.authorize_input(
        second,
        source_sha256="a" * 64,
        dataset_sha256="b" * 64,
        lineage_key_id="fixture-v1",
        owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    )
    work.seal_checkpoint(provenance, "c" * 64)
    operation = uuid4()
    work.publish(first, operation, hashes())
    with pytest.raises(TrainingWorkError, match="training_operation_conflict"):
        work.publish(second, operation, hashes())
    work.check_running(second)


def test_publish_withdrawal_race_has_one_serialized_winner(pair: PairingHarness) -> None:
    work, run = registered(pair)
    barrier = Barrier(2)

    def publish() -> bool:
        barrier.wait(timeout=5)
        try:
            work.publish(run, uuid4(), hashes())
            return True
        except TrainingWorkError, TrainingConsentError:
            return False

    def withdraw() -> None:
        barrier.wait(timeout=5)
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication, refusal = pool.submit(publish), pool.submit(withdraw)
        published = publication.result(timeout=10)
        refusal.result(timeout=10)
    with Session(pair.engine) as session:
        run_row = required(session.get(TrainingRunRow, run))
        assert run_row.phase == ("PUBLISHED" if published else "INVALIDATED")
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"
    assert work.is_published(run, hashes()) is published


def test_stale_repeatable_read_cannot_publish_after_withdrawal(pair: PairingHarness) -> None:
    work, run = registered(pair)
    with Session(pair.engine) as session, session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        assert required(session.get(TrainingRunRow, run)).phase == "RUNNING"
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
        with pytest.raises(TrainingWorkError, match="training_work_isolation_required"):
            work._locked(session, run)


def test_account_suspension_invalidates_and_purge_waits_for_actual_cleanup(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, run = registered(pair)
    from .test_privacy_purge import overdue

    request = overdue(pair, monkeypatch)
    with Session(pair.engine) as session:
        assert required(session.get(TrainingRunRow, run)).phase == "INVALIDATED"
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_participant_evidence_retained"),
    ):
        session.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": request},
        )
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is not None
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_cleanup_evidence_required"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_cleanup_claim SET phase='COMPLETE',"
                "completed_at=clock_timestamp() WHERE run_id=:run"
            ),
            {"run": run},
        )


def test_terminal_consent_revision_is_reserved_for_private_decision(pair: PairingHarness) -> None:
    with Session(pair.engine) as session, session.begin():
        session.add(
            TrainingConsentRow(
                user_id=pair.actor.user_id,
                decision="GRANTED",
                revision=MAX_REVISION - 1,
                policy_version=1,
                changed_at=datetime.now(UTC),
            )
        )
    with pytest.raises(TrainingConsentError, match="consent_revision_conflict"):
        service(pair).decide(pair.actor, command(pair, "GRANTED", MAX_REVISION - 1))
    assert (
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", MAX_REVISION - 1))["revision"]
        == MAX_REVISION
    )


def test_training_work_empty_adjacent_cycle(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0054_training_consent")
    database_harness.upgrade(empty_database_name, "0055_training_work")
    database_harness.downgrade(empty_database_name, "0054_training_consent")
    database_harness.upgrade(empty_database_name, "0055_training_work")


def test_repeatable_read_withdrawal_cannot_miss_a_new_registration(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    with Session(pair.engine) as stale, stale.begin():
        stale.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        assert stale.scalar(select(func.count()).select_from(TrainingParticipantRow)) == 0
        work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
        with pytest.raises(DBAPIError, match="training_consent_isolation_required"):
            stale.execute(
                text(
                    "UPDATE account.training_consent SET decision='WITHDRAWN',"
                    "revision=revision+1,changed_at=clock_timestamp() WHERE user_id=:owner"
                ),
                {"owner": pair.actor.user_id},
            )
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with Session(pair.engine) as session:
        assert required(session.get(TrainingRunRow, run)).phase == "INVALIDATED"
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"


def test_serving_requires_current_identity_and_matching_lineage_key_scope(
    pair: PairingHarness,
) -> None:
    from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow

    work, run = registered(pair)
    work.publish(run, uuid4(), hashes())
    other_key = TrainingWorkService(
        sessionmaker(pair.engine),
        server_instance_id=UUID(pair.identity["expected_server_instance_id"]),
        identity_epoch=pair.identity["expected_identity_epoch"],
        lineage_key_id="other-key",
        lineage_key=b"k" * 32,
        consent_ledger=pair.consent_ledger,
    )
    assert not other_key.is_published(run, hashes())
    with Session(pair.engine) as session, session.begin():
        identity = session.get(
            ServerInstanceRow, UUID(pair.identity["expected_server_instance_id"])
        )
        assert identity is not None
        identity.identity_epoch += 1
    assert not work.is_published(run, hashes())


def test_repeatable_read_suspension_cannot_miss_new_registration(pair: PairingHarness) -> None:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    with Session(pair.engine) as stale, stale.begin():
        stale.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        assert stale.scalar(select(func.count()).select_from(TrainingParticipantRow)) == 0
        work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
        with pytest.raises(DBAPIError, match="training_invalidation_isolation_required"):
            stale.execute(
                text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:owner"),
                {"owner": pair.actor.user_id},
            )
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:owner"),
            {"owner": pair.actor.user_id},
        )
    with Session(pair.engine) as session:
        assert required(session.get(TrainingRunRow, run)).phase == "INVALIDATED"
        assert required(session.get(TrainingCleanupClaimRow, run)).phase == "PENDING"


def test_actual_training_input_binding_is_exact_and_start_is_atomic(pair: PairingHarness) -> None:
    from hashlib import sha256
    from hmac import new as new_hmac

    work, run = registered(pair, "READY")
    inputs: BoundInputs = {
        "source_sha256": "a" * 64,
        "dataset_sha256": "b" * 64,
        "lineage_key_id": "fixture-v1",
        "owner_tokens": (new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    }
    for change in (
        {"source_sha256": "d" * 64},
        {"dataset_sha256": "d" * 64},
        {"lineage_key_id": "other"},
        {"owner_tokens": ("d" * 64,)},
    ):
        with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
            work.authorize_input(run, **cast(BoundInputs, {**inputs, **change}))
        with Session(pair.engine) as session:
            assert required(session.get(TrainingRunRow, run)).phase == "READY"
    provenance = work.authorize_input(run, **inputs)
    from dataclasses import replace

    assert provenance.run_id == run
    assert len(str(provenance.document()).encode("utf-8")) < 512
    work.check_candidate(provenance)
    wrong = replace(provenance, binding_sha256="d" * 64)
    with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
        work.check_candidate(wrong)
    with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
        work.publish(run, uuid4(), hashes(), input_provenance=wrong)
    work.check_input(run, **inputs)
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.check_input(run, **inputs)
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        work.check_candidate(provenance)


def test_compact_publication_receipt_replay_survives_withdrawal_without_reauthorizing(
    pair: PairingHarness,
) -> None:
    from dataclasses import replace
    from hashlib import sha256
    from hmac import new as new_hmac

    work, run = registered(pair, "READY")
    provenance = work.authorize_input(
        run,
        source_sha256="a" * 64,
        dataset_sha256="b" * 64,
        lineage_key_id="fixture-v1",
        owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    )
    operation = uuid4()
    work.seal_checkpoint(provenance, "c" * 64)
    work.publish(run, operation, hashes(), input_provenance=provenance)
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    assert work.is_published(run, hashes(), input_provenance=provenance)
    assert work.publish(run, operation, hashes(), input_provenance=provenance).phase == "PUBLISHED"
    wrong = replace(provenance, binding_sha256="d" * 64)
    assert not work.is_published(run, hashes(), input_provenance=wrong)
    with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
        work.publish(run, operation, hashes(), input_provenance=wrong)


@pytest.mark.usefixtures("internal_io_budget")
def test_final_privacy_purge_irreversibly_revokes_published_serving_authority(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    from hashlib import sha256
    from hmac import new as new_hmac

    work, run = registered(pair)
    provenance = work.authorize_input(
        run,
        source_sha256="a" * 64,
        dataset_sha256="b" * 64,
        lineage_key_id="fixture-v1",
        owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    )
    publication = hashes()
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.execute(
            text(
                "UPDATE account.internal_io_policy SET workload_version=3,"
                "server_instance_id=:server,identity_epoch=:epoch WHERE singleton_id=1"
            ),
            {
                "server": pair.identity["expected_server_instance_id"],
                "epoch": pair.identity["expected_identity_epoch"],
            },
        )
    execution = uuid4()
    ticket = TrainingExecutionTicket(
        execution,
        run,
        str((tmp_path / "input" / str(execution)).absolute()),
        str((tmp_path / "output" / str(execution)).absolute()),
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        Sha256Digest(b"i" * 32),
        Sha256Digest(b"r" * 32),
        1024,
        2048,
    )
    executions = PostgresTrainingExecutionRepository(sessions, work)
    executions.prepare(ticket)
    work.publish(run, uuid4(), publication, input_provenance=provenance)
    independent = PostgresTrainingPublicationAuthority(sessions)
    assert work.is_published(run, publication, input_provenance=provenance)
    assert independent.is_published(run, publication, input_provenance=provenance)

    from .test_privacy_purge import overdue

    deletion_request = overdue(pair, monkeypatch)
    executions.begin_cleanup(
        ticket,
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"e" * 32),
        retain_checkpoint=False,
        checkpoint_manifest_sha256=None,
    )
    executions.confirm(ticket, "f" * 64)
    with sessions.begin() as session:
        claim = required(session.get(TrainingCleanupClaimRow, run))
        assert claim.phase == "COMPLETE"
        session.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": deletion_request},
        )

    assert not work.is_published(run, publication, input_provenance=provenance)
    assert not independent.is_published(run, publication, input_provenance=provenance)
    with sessions.begin() as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
        assert session.scalar(select(func.count()).select_from(TrainingParticipantRow)) == 0
        tombstone = required(
            session.scalar(
                select(TrainingPublicationRevocationRow).where(
                    TrainingPublicationRevocationRow.run_id == run
                )
            )
        )
        assert tombstone.deletion_request_id == deletion_request
    for statement in (
        "UPDATE ml.training_publication_revocation SET revoked_at=clock_timestamp()",
        "DELETE FROM ml.training_publication_revocation",
    ):
        with (
            sessions.begin() as session,
            pytest.raises(DBAPIError, match="training_publication_revocation_immutable"),
        ):
            session.execute(text(statement))
    with pytest.raises(DBAPIError, match="training_publication_revocation_evidence_retained"):
        database_harness.downgrade(database_name, "0057_training_execution")


def test_checkpoint_seal_is_exact_retained_and_required_before_publication(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    from hashlib import sha256
    from hmac import new as new_hmac

    work, run = registered(pair, "READY")
    provenance = work.authorize_input(
        run,
        source_sha256="a" * 64,
        dataset_sha256="b" * 64,
        lineage_key_id="fixture-v1",
        owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    )
    with pytest.raises(TrainingWorkError, match="training_checkpoint_seal_required"):
        work.publish(run, uuid4(), hashes(), input_provenance=provenance)
    work.seal_checkpoint(provenance, "c" * 64)
    work.seal_checkpoint(provenance, "c" * 64)
    work.check_checkpoint(provenance, "c" * 64)
    with pytest.raises(TrainingWorkError, match="training_checkpoint_conflict"):
        work.seal_checkpoint(provenance, "d" * 64)
    with pytest.raises(TrainingWorkError, match="training_checkpoint_seal_required"):
        work.check_checkpoint(provenance, "d" * 64)
    for statement in (
        "UPDATE ml.training_checkpoint SET manifest_sha256=decode(repeat('d',64),'hex')",
        "DELETE FROM ml.training_checkpoint",
    ):
        with (
            Session(pair.engine) as session,
            session.begin(),
            pytest.raises(DBAPIError, match="training_checkpoint_evidence_retained"),
        ):
            session.execute(text(statement))
    with pytest.raises(DBAPIError, match="training_checkpoint_evidence_retained"):
        database_harness.downgrade(database_name, "0055_training_work")


def test_checkpoint_empty_adjacent_migration_cycle(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    database_harness.downgrade(empty_database_name, "0055_training_work")
    database_harness.upgrade(empty_database_name)

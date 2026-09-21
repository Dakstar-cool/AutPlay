"""Measured shared-training capacity remains charged until exact process-tree exit."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.postgresql import training_execution as execution_adapter
from autplay.adapters.postgresql.internal_io import internal_io_usage
from autplay.adapters.postgresql.models.training_work import (
    TrainingCleanupClaimRow,
    TrainingExecutionRow,
    TrainingRunRow,
)
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.training_execution import (
    PostgresTrainingExecutionRepository,
    TrainingExecutionStatus,
)
from autplay.application.training_work import TrainingWorkError
from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.domain.training_execution import TrainingExecutionTicket
from autplay.domain.vault import Sha256Digest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_discovery_authority_clock import _blocked_by
from .test_training_work import PairingHarness, command, registry, service
from .test_training_work import base_pair as base_pair
from .test_training_work import training_pair as training_pair

IDENTITY = ProcessIdentity(4242, b"p" * 32)
CLEANUP_SHA256 = "c" * 64


def close(
    repository: PostgresTrainingExecutionRepository,
    ticket: TrainingExecutionTicket,
    proof: ProcessExitEvidence,
) -> TrainingExecutionStatus:
    repository.begin_cleanup(
        ticket,
        proof,
        retain_checkpoint=False,
        checkpoint_manifest_sha256=None,
    )
    return repository.confirm(ticket, CLEANUP_SHA256)


def ready(
    pair: PairingHarness, *, configure_training: bool = True
) -> tuple[PostgresTrainingExecutionRepository, TrainingExecutionTicket]:
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(run, "b" * 64)
    if configure_training:
        with Session(pair.engine) as session, session.begin():
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
        str((Path.cwd() / "training-input" / str(execution)).absolute()),
        str((Path.cwd() / "training-output" / str(execution)).absolute()),
        "1",
        "1",
        "1",
        "1",
        "1",
        "1",
        Sha256Digest(b"j" * 32),
        Sha256Digest(b"i" * 32),
        1024,
        2048,
    )
    return (
        PostgresTrainingExecutionRepository(
            sessionmaker(pair.engine, expire_on_commit=False), work
        ),
        ticket,
    )


@pytest.mark.usefixtures("internal_io_budget")
def test_training_capacity_survives_withdrawal_until_exact_exit(pair: PairingHarness) -> None:
    repository, ticket = ready(pair)
    assert repository.prepare(ticket).state.value == "PREPARED"
    running = repository.start(ticket, IDENTITY)
    assert 0 < running.authorized_seconds <= 5
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 1

    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        repository.renew(ticket, IDENTITY)
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 1

    wrong = ProcessIdentity(4243, b"q" * 32)
    with pytest.raises(ResourceAdmissionError, match="training_execution_stale"):
        repository.begin_cleanup(
            ticket,
            ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, wrong),
            retain_checkpoint=False,
            checkpoint_manifest_sha256=None,
        )
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY),
    )
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
        claim = session.get(TrainingCleanupClaimRow, ticket.run_id)
        assert claim is not None and claim.phase == "COMPLETE"


@pytest.mark.usefixtures("internal_io_budget")
def test_payload_free_inventory_can_renew_before_input_binding(pair: PairingHarness) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    repository.start(ticket, IDENTITY)
    renewed = repository.renew(ticket, IDENTITY)
    assert 0 < renewed.authorized_seconds <= 5
    with Session(pair.engine) as session:
        run = session.get(TrainingRunRow, ticket.run_id)
        assert run is not None and run.phase == "READY"
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY),
    )


@pytest.mark.usefixtures("internal_io_budget")
def test_later_withdrawal_closes_claim_from_retained_input_cleanup_evidence(
    pair: PairingHarness,
) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32),
    )
    service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
    with Session(pair.engine) as session:
        claim = session.get(TrainingCleanupClaimRow, ticket.run_id)
        assert claim is not None and claim.phase == "COMPLETE"


@pytest.mark.usefixtures("internal_io_budget")
def test_withdrawal_and_exact_cleanup_race_always_complete_the_claim(
    pair: PairingHarness,
) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    repository.start(ticket, IDENTITY)
    barrier = Barrier(2)

    def withdraw() -> object:
        barrier.wait(timeout=10)
        return service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))

    def finish() -> object:
        barrier.wait(timeout=10)
        return close(
            repository,
            ticket,
            ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        withdrawn = pool.submit(withdraw)
        closed = pool.submit(finish)
        withdrawn.result(timeout=15)
        closed.result(timeout=15)

    with Session(pair.engine) as session:
        claim = session.get(TrainingCleanupClaimRow, ticket.run_id)
        assert claim is not None and claim.phase == "COMPLETE"
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_training_and_existing_workers_share_atomic_global_capacity(pair: PairingHarness) -> None:
    repository, ticket = ready(pair)
    with Session(pair.engine) as session, session.begin():
        session.execute(text("UPDATE account.internal_io_policy SET active_limit=1"))
    repository.prepare(ticket)
    execution = uuid4()
    other = MaintenanceTicket(execution, uuid4(), None, execution, MaintenanceAction.INVENTORY)
    with pytest.raises(ResourceAdmissionError, match="internal_io_busy"):
        PostgresProviderMaintenanceRepository(
            sessionmaker(pair.engine, expire_on_commit=False)
        ).prepare(other)
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32),
    )


@pytest.mark.usefixtures("internal_io_budget")
def test_training_and_existing_worker_cannot_race_past_one_global_slot(
    pair: PairingHarness,
) -> None:
    repository, ticket = ready(pair)
    with Session(pair.engine) as session, session.begin():
        session.execute(text("UPDATE account.internal_io_policy SET active_limit=1"))
    execution = uuid4()
    other = MaintenanceTicket(execution, uuid4(), None, execution, MaintenanceAction.INVENTORY)
    maintenance = PostgresProviderMaintenanceRepository(
        sessionmaker(pair.engine, expire_on_commit=False)
    )
    barrier = Barrier(2)

    def enter(kind: str) -> str:
        barrier.wait(timeout=5)
        try:
            if kind == "training":
                repository.prepare(ticket)
            else:
                maintenance.prepare(other)
        except ResourceAdmissionError as error:
            return error.code
        return "ADMITTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(enter, ("training", "maintenance"))) == [
            "ADMITTED",
            "internal_io_busy",
        ]


@pytest.mark.usefixtures("internal_io_budget")
def test_identity_lock_precedes_admission_in_opposing_real_transactions(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, ticket = ready(pair)
    admission_called = Event()

    def observed_admission(session: Session) -> None:
        admission_called.set()
        lock_resource_admission(session)

    monkeypatch.setattr(execution_adapter, "lock_resource_admission", observed_admission)
    with Session(pair.engine) as holder, holder.begin(), ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute(
            select(text("1"))
            .select_from(text("account.server_instance"))
            .where(text("server_instance_id=:server"))
            .with_for_update(),
            {"server": pair.identity["expected_server_instance_id"]},
        )
        pid = holder.scalar(select(text("pg_backend_pid()")))
        assert isinstance(pid, int)
        future = pool.submit(repository.prepare, ticket)
        until = monotonic() + 5
        with Session(pair.engine) as observer:
            while not _blocked_by(observer, pid):
                assert monotonic() < until
                sleep(0.01)
        assert not admission_called.is_set()
        holder.commit()
        assert future.result(timeout=5).ticket == ticket
    with Session(pair.engine) as session:
        trigger_names = tuple(
            session.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid='ml.training_execution'::regclass "
                    "AND NOT tgisinternal ORDER BY tgname"
                )
            )
        )
    assert trigger_names == (
        "a_training_execution_identity",
        "m_training_io_admission",
        "training_execution_guard",
        "z_training_publication_seal_guard",
    )


@pytest.mark.usefixtures("internal_io_budget")
def test_sql_guards_keep_training_identity_and_history_immutable(pair: PairingHarness) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="Training execution ownership is immutable"),
    ):
        row = session.get(TrainingExecutionRow, ticket.execution_id)
        assert row is not None
        row.input_bytes += 1
        session.flush()
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32),
    )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="cannot be deleted"),
    ):
        row = session.scalar(
            select(TrainingExecutionRow).where(
                TrainingExecutionRow.execution_id == ticket.execution_id
            )
        )
        assert row is not None
        session.delete(row)
        session.flush()


@pytest.mark.usefixtures("internal_io_budget")
def test_direct_sql_cannot_store_null_running_or_closure_evidence(pair: PairingHarness) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_execution_child_check"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='RUNNING',child_pid=7,started_at=now() "
                "WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id},
        )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="Training execution ownership is immutable"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='CLOSED',closed_at=now(),"
                "closure_kind='NOT_STARTED' WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id},
        )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="Training execution ownership is immutable"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='CLOSED',cleanup_started_at=now(),"
                "closed_at=now(),"
                "input_cleaned_at=now(),input_cleanup_sha256=:cleanup,"
                "closure_kind='NOT_STARTED',closure_evidence_sha256=:evidence "
                "WHERE execution_id=:execution"
            ),
            {
                "execution": ticket.execution_id,
                "cleanup": bytes.fromhex(CLEANUP_SHA256),
                "evidence": b"e" * 32,
            },
        )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_execution_transition_required"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='STOPPING',cleanup_started_at=now(),"
                "retain_checkpoint=false "
                "WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id},
        )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_execution_transition_required"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='STOPPING',cleanup_started_at=now(),"
                "retain_checkpoint=false,closure_kind='NOT_STARTED',"
                "closure_evidence_sha256=:evidence WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id, "evidence": b"e" * 32},
        )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_execution_stale"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET state='RUNNING',child_pid=7,"
                "child_identity_sha256=:identity,started_at=now(),"
                "heartbeat_at=now()+interval '10 minutes',"
                "io_deadline_at=now()+interval '10 minutes 5 seconds' "
                "WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id, "identity": b"x" * 32},
        )


@pytest.mark.parametrize("phase", ["start", "renew"])
@pytest.mark.usefixtures("internal_io_budget")
def test_identity_rotation_blocks_sql_authority_but_not_exact_exit(
    pair: PairingHarness, phase: str
) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    if phase == "renew":
        repository.start(ticket, IDENTITY)
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text(
                "UPDATE account.server_instance SET identity_epoch=identity_epoch+1 "
                "WHERE server_instance_id=:server"
            ),
            {"server": pair.identity["expected_server_instance_id"]},
        )
    statement = (
        "UPDATE ml.training_execution SET state='RUNNING',child_pid=4242,"
        "child_identity_sha256=:identity,started_at=now(),heartbeat_at=now(),"
        "io_deadline_at=now()+interval '5 seconds' WHERE execution_id=:execution"
        if phase == "start"
        else "UPDATE ml.training_execution SET heartbeat_at=now(),"
        "io_deadline_at=now()+interval '5 seconds' WHERE execution_id=:execution"
    )
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_identity_changed"),
    ):
        session.execute(
            text(statement),
            {"execution": ticket.execution_id, "identity": IDENTITY.identity_sha256},
        )
    proof = (
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)
        if phase == "start"
        else ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, IDENTITY)
    )
    assert close(repository, ticket, proof).state.value == "CLOSED"


@pytest.mark.usefixtures("internal_io_budget")
def test_version_two_measurement_cannot_admit_training(pair: PairingHarness) -> None:
    repository, ticket = ready(pair, configure_training=False)
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text("UPDATE account.internal_io_policy SET workload_version=2 WHERE singleton_id=1")
        )
    with pytest.raises(ResourceAdmissionError, match="training_budget_unconfigured"):
        repository.prepare(ticket)


@pytest.mark.usefixtures("internal_io_budget")
def test_downgrade_refuses_retained_training_execution_history(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    close(
        repository,
        ticket,
        ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32),
    )
    with pytest.raises(DBAPIError, match="Refusing to discard training execution"):
        database_harness.downgrade(database_name, "0056_training_checkpoint")


@pytest.mark.usefixtures("internal_io_budget")
def test_publication_seal_is_immutable_and_blocks_evidence_destroying_downgrade(
    pair: PairingHarness, database_harness: DatabaseHarness, database_name: str
) -> None:
    repository, ticket = ready(pair)
    repository.prepare(ticket)
    repository.start(ticket, IDENTITY)
    repository.bind_checkpoint(ticket, IDENTITY, device="2", inode="3")
    repository.begin_cleanup(
        ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY),
        retain_checkpoint=True,
        checkpoint_manifest_sha256="a" * 64,
        checkpoint_weights_sha256="b" * 64,
        checkpoint_optimizer_steps=1,
        checkpoint_device_type="cpu",
        publication_seal_sha256="c" * 64,
    )

    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_publication_seal_immutable"),
    ):
        session.execute(
            text(
                "UPDATE ml.training_execution SET publication_seal_sha256=:seal "
                "WHERE execution_id=:execution"
            ),
            {"execution": ticket.execution_id, "seal": b"d" * 32},
        )

    with pytest.raises(DBAPIError, match="training_publication_seal_evidence_retained"):
        database_harness.downgrade(database_name, "0058_training_privacy_fence")

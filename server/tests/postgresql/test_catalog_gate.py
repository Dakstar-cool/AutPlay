"""Real shared gate, authority, migration, and contained one-shot process proofs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from autplay.adapters.child_process import catalog_child_launch
from autplay.adapters.filesystem.vault_process import ChildLaunch
from autplay.adapters.postgresql.catalog_execution import (
    CatalogStatus,
    PostgresCatalogExecutionRepository,
)
from autplay.adapters.postgresql.models import CatalogExecutionRow, MetadataProviderGateRow
from autplay.domain.auth import Principal
from autplay.domain.catalog_execution import CatalogExecutionTicket
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ExitKind, ProcessExitEvidence
from autplay.ports.track_metadata import MetadataProviderError
from autplay.runtime.catalog_io import CatalogProcessCoordinator
from process_tree_support import process_tree_factory, wait_marker
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .conftest import DatabaseHarness
from .test_ingest_cleanup import IDENTITY
from .test_metadata_execution import MetadataFixture, metadata_budget, metadata_work

__all__ = ["metadata_budget", "metadata_work"]


def _closed() -> ProcessExitEvidence:
    return ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"c" * 32, 0, IDENTITY)


def _unstarted() -> ProcessExitEvidence:
    return ProcessExitEvidence(ExitKind.NOT_STARTED, b"n" * 32)


def _wait_ready(item: MetadataFixture) -> None:
    with item.sessions() as session:
        gate = session.get(MetadataProviderGateRow, 1)
        now = session.scalar(select(func.clock_timestamp()))
        assert gate is not None and isinstance(now, datetime)
        delay = (gate.next_request_at - now).total_seconds()
    sleep(max(0, delay) + 0.015)


@pytest.mark.usefixtures("metadata_budget")
def test_worker_and_two_catalog_owners_race_for_same_slot(metadata_work: MetadataFixture) -> None:
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions)
    tickets = [repo.plan(item.principal, uuid4()) for _ in range(2)]
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    barrier = Barrier(3)

    def enter(index: int) -> bool:
        barrier.wait(timeout=5)
        if index == 2:
            return item.repository.provider_begin(running, uuid4())
        try:
            repo.prepare(tickets[index])
            return True
        except ResourceAdmissionError as error:
            assert error.code == "metadata_provider_busy"
            return False

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(enter, range(3))) == 1


def test_exact_owner_idempotent_close_and_cooldown(metadata_work: MetadataFixture) -> None:
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions)
    ticket = repo.plan(item.principal, uuid4())
    prepared = repo.prepare(ticket)
    assert repo.prepare(ticket) == prepared
    with pytest.raises(ResourceAdmissionError, match="stale"):
        repo.confirm(replace(ticket, owner_run_id=uuid4()), _unstarted())
    repo.start(ticket, IDENTITY)
    repo.confirm(ticket, _closed())
    next_ticket = repo.plan(item.principal, uuid4())
    with pytest.raises(ResourceAdmissionError, match="busy"):
        repo.prepare(next_ticket)
    with item.sessions() as session:
        row = session.get(CatalogExecutionRow, ticket.execution_id)
        gate = session.get(MetadataProviderGateRow, 1)
        assert row is not None and gate is not None and row.closed_at is not None
        assert (gate.next_request_at - row.closed_at).total_seconds() >= 1.1
    _wait_ready(item)
    repo.prepare(next_ticket)
    assert repo.confirm(ticket, _closed()).state == ExecutionState.CLOSED
    with item.sessions() as session:
        gate = session.get(MetadataProviderGateRow, 1)
        assert gate is not None and gate.catalog_execution_id == next_ticket.execution_id
    with pytest.raises(ResourceAdmissionError, match="conflict"):
        repo.confirm(ticket, replace(_closed(), evidence_sha256=b"z" * 32))
    repo.confirm(next_ticket, _unstarted())


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.usefixtures("metadata_budget")
def test_expired_catalog_retains_slot_until_exact_exit(
    metadata_work: MetadataFixture,
    started: bool,
) -> None:
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions, lifetime=timedelta(seconds=0.2))
    ticket = repo.plan(item.principal, uuid4())
    repo.prepare(ticket)
    if started:
        repo.start(ticket, IDENTITY)
    sleep(0.25)
    with pytest.raises(ResourceAdmissionError, match="stale"):
        repo.start(ticket, IDENTITY)
    with pytest.raises(ResourceAdmissionError, match="busy"):
        repo.prepare(repo.plan(item.principal, uuid4()))
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    assert not item.repository.provider_begin(running, uuid4())
    with pytest.raises(DBAPIError), item.sessions.begin() as session:
        session.execute(
            text(
                "UPDATE library.metadata_provider_gate SET "
                "catalog_execution_id=NULL,request_id=NULL"
            )
        )
    repo.confirm(ticket, _closed() if started else _unstarted())
    assert not item.repository.provider_begin(running, uuid4())
    _wait_ready(item)
    assert item.repository.provider_begin(running, uuid4())


@pytest.mark.parametrize("kind", ["session", "device", "account"])
def test_current_authority_required_and_exit_still_closes(
    metadata_work: MetadataFixture,
    kind: str,
) -> None:
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions)
    ticket = repo.plan(item.principal, uuid4())
    repo.prepare(ticket)
    repo.start(ticket, IDENTITY)
    statement, identity = {
        "session": (
            "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=:id",
            item.principal.session_id,
        ),
        "device": (
            "UPDATE account.device SET revoked_at=clock_timestamp() WHERE device_id=:id",
            item.principal.device_id,
        ),
        "account": (
            "UPDATE account.user_account SET authority_generation=authority_generation+1 "
            "WHERE user_id=:id",
            item.principal.user_id,
        ),
    }[kind]
    with item.sessions.begin() as session:
        session.execute(text(statement), {"id": identity})
    with pytest.raises(ResourceAdmissionError, match="unauthorized"):
        repo.renew(ticket, IDENTITY)
    with pytest.raises(ResourceAdmissionError, match="unauthorized"):
        repo.require_current(ticket)
    assert repo.confirm(ticket, _closed()).state == ExecutionState.CLOSED


def test_foreign_session_and_locked_gate_are_bounded(metadata_work: MetadataFixture) -> None:
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions)
    with pytest.raises(ResourceAdmissionError, match="unauthorized"):
        repo.plan(replace(item.principal, device_id=uuid4()), uuid4())
    ticket = repo.plan(item.principal, uuid4())
    with item.sessions.begin() as held:
        held.get(MetadataProviderGateRow, 1, with_for_update=True)
        started = monotonic()
        with pytest.raises(ResourceAdmissionError, match="busy"):
            repo.prepare(ticket)
        assert monotonic() - started < 0.5


def _launch(mode: str = "reply", marker: Path | None = None) -> ChildLaunch:
    def launch() -> tuple[list[str], dict[str, str]]:
        arguments, environment = catalog_child_launch()
        environment["CATALOG_TEST_MODE"] = mode
        if marker is not None:
            environment["CATALOG_TEST_MARKER"] = str(marker)
        return [
            arguments[0],
            "-I",
            str(Path(__file__).parents[1] / "fixtures" / "catalog_request_child.py"),
        ], environment

    return launch


def test_actual_child_reply_outside_transaction_and_confirm_retry(
    metadata_work: MetadataFixture,
) -> None:
    item = metadata_work
    blocked, release = Event(), Event()

    class Repository(PostgresCatalogExecutionRepository):
        def confirm(
            self, ticket: CatalogExecutionTicket, proof: ProcessExitEvidence
        ) -> CatalogStatus:
            if not release.is_set():
                blocked.set()
                raise SQLAlchemyError("synthetic acknowledgement outage")
            return super().confirm(ticket, proof)

    repo = Repository(item.sessions)
    coordinator = CatalogProcessCoordinator(
        repo, tree_factory=process_tree_factory(), launch=_launch()
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                coordinator.http(item.principal).json,
                "https://musicbrainz.org/ws/2/artist?fmt=json",
            )
            assert blocked.wait(5)
            assert coordinator.pending() and not future.done()
            # The retained child has exited. Its DB acknowledgement is uncertain.
            # A separate transaction can lock the gate: no connection held across HTTP.
            with item.sessions.begin() as session:
                gate = session.scalar(select(MetadataProviderGateRow).with_for_update(nowait=True))
                assert gate is not None and gate.catalog_execution_id is not None
            with pytest.raises(ResourceAdmissionError, match="busy"):
                repo.prepare(repo.plan(item.principal, uuid4()))
            release.set()
            assert len(future.result(timeout=5)["value"]) == 150000
        assert not coordinator.pending()
    finally:
        release.set()
        assert coordinator.shutdown() == ()


def test_actual_hung_child_deadline_kills_and_reaps(
    metadata_work: MetadataFixture, tmp_path: Path
) -> None:
    item = metadata_work
    marker = tmp_path / "sent"
    repo = PostgresCatalogExecutionRepository(item.sessions, lifetime=timedelta(seconds=1.5))
    coordinator = CatalogProcessCoordinator(
        repo, tree_factory=process_tree_factory(), launch=_launch("hang", marker), maximum=1
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                coordinator.http(item.principal).json,
                "https://musicbrainz.org/ws/2/artist?fmt=json",
            )
            wait_marker(marker)
            start = monotonic()
            with pytest.raises(MetadataProviderError, match="metadata_provider_busy"):
                coordinator.http(item.principal).json(
                    "https://musicbrainz.org/ws/2/artist?fmt=json"
                )
            assert monotonic() - start < 0.1
            with item.sessions.begin() as session:
                gate = session.scalar(select(MetadataProviderGateRow).with_for_update(nowait=True))
                assert gate is not None and gate.catalog_execution_id is not None
            with pytest.raises(MetadataProviderError):
                future.result(timeout=5)
        assert coordinator.shutdown() == ()
        with item.sessions() as session:
            row = session.scalar(select(CatalogExecutionRow))
            gate = session.get(MetadataProviderGateRow, 1)
            assert row is not None and row.state == "CLOSED" and row.exit_code is not None
            assert gate is not None and gate.request_id is None
    finally:
        assert coordinator.shutdown() == ()


def test_catalog_history_blocks_downgrade(
    metadata_work: MetadataFixture,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    repo = PostgresCatalogExecutionRepository(metadata_work.sessions)
    ticket = repo.plan(metadata_work.principal, uuid4())
    repo.prepare(ticket)
    repo.confirm(ticket, _unstarted())
    with pytest.raises(DBAPIError, match="refusing to discard catalog"):
        database_harness.downgrade(database_name, "0063_social_public_id")


@pytest.mark.usefixtures("metadata_budget")
def test_migration_preserves_occupied_worker_and_spacing(
    metadata_work: MetadataFixture,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    item = metadata_work
    item.repository.prepare(item.ticket)
    running = item.repository.start(item.ticket, IDENTITY)
    assert item.repository.provider_begin(running, uuid4())
    with database_harness.connect(database_name) as connection:
        connection.execute(
            "UPDATE library.metadata_provider_gate "
            "SET next_request_at=clock_timestamp()+interval '1 hour'"
        )
        before = connection.execute(
            "SELECT execution_id,request_id,next_request_at FROM library.metadata_provider_gate"
        ).fetchone()
        connection.commit()
    database_harness.downgrade(database_name, "0063_social_public_id")
    database_harness.upgrade(database_name, "0064_metadata_catalog_gate")
    with database_harness.connect(database_name) as connection:
        assert (
            connection.execute(
                "SELECT execution_id,request_id,next_request_at FROM library.metadata_provider_gate"
            ).fetchone()
            == before
        )


@pytest.mark.parametrize("failure", ["plan", "current", "malformed", "provider"])
def test_infrastructure_and_protocol_errors_are_stable_and_do_not_publish(
    metadata_work: MetadataFixture,
    failure: str,
) -> None:
    item = metadata_work

    class Repository(PostgresCatalogExecutionRepository):
        def plan(self, principal: Principal, owner: UUID) -> CatalogExecutionTicket:
            if failure == "plan":
                raise SQLAlchemyError("synthetic plan outage")
            return super().plan(principal, owner)

        def require_current(self, ticket: CatalogExecutionTicket) -> None:
            if failure == "current":
                with item.sessions.begin() as session:
                    session.execute(
                        text(
                            "UPDATE account.user_session SET "
                            "revoked_at=clock_timestamp() WHERE session_id=:id"
                        ),
                        {"id": item.principal.session_id},
                    )
            super().require_current(ticket)

    repo = Repository(item.sessions)
    mode = "malformed" if failure == "malformed" else "error" if failure == "provider" else "reply"
    coordinator = CatalogProcessCoordinator(
        repo, tree_factory=process_tree_factory(), launch=_launch(mode)
    )
    try:
        with pytest.raises(MetadataProviderError):
            coordinator.http(item.principal).json("https://musicbrainz.org/ws/2/artist?fmt=json")
        assert coordinator.shutdown() == ()
        with item.sessions() as session:
            gate = session.get(MetadataProviderGateRow, 1)
            assert gate is not None and gate.request_id is None
    finally:
        assert coordinator.shutdown() == ()


def test_offline_drain_closes_catalog_only_after_host_proof(metadata_work: MetadataFixture) -> None:
    import os

    from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
    from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
    from autplay.domain.resource_execution import ProcessIdentity

    if os.name != "nt":
        pytest.skip("host absent-PID proof is Windows-specific")
    item = metadata_work
    repo = PostgresCatalogExecutionRepository(item.sessions)
    ticket = repo.plan(item.principal, uuid4())
    repo.prepare(ticket)
    repo.start(ticket, ProcessIdentity(4_000_000_000, b"o" * 32))
    report = PostgresOfflineExecutionDrain(
        item.sessions, OfflineProcessEvidenceProbe(None)
    ).close_restored_reservations()
    assert report.catalog_executions == report.checked_pids == 1
    with item.sessions() as session:
        row = session.get(CatalogExecutionRow, ticket.execution_id)
        gate = session.get(MetadataProviderGateRow, 1)
        assert row is not None and row.closure_kind == "SUPERVISOR_EXIT"
        assert gate is not None and gate.request_id is None
        assert row.closed_at is not None and gate.next_request_at > row.closed_at


def test_runtime_readiness_tracks_unconfirmed_expired_catalog(
    metadata_work: MetadataFixture,
    database_url: str,
) -> None:
    from autplay.entrypoints.catalog_composition import CatalogRuntime
    from autplay.runtime.settings import load_api_settings

    item = metadata_work
    runtime = CatalogRuntime(
        load_api_settings(
            overrides={
                "database_url": database_url,
                "auth_signing_secret": "a" * 32,
                "public_access_source_hmac_secret": "p" * 32,
            },
            environ={},
        ),
        tree_factory=process_tree_factory(),
    )
    try:
        assert runtime.check().ready
        repo = PostgresCatalogExecutionRepository(item.sessions, lifetime=timedelta(seconds=0.15))
        ticket = repo.plan(item.principal, uuid4())
        repo.prepare(ticket)
        sleep(0.2)
        assert runtime.check().code == "metadata_catalog_exit_unconfirmed"
        repo.confirm(ticket, _unstarted())
        assert runtime.check().ready
    finally:
        assert runtime.shutdown() == ()

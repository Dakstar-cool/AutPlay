"""Adjacent historical migration guards retain source data and actual bytes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.provider_scratch import PostgresProviderScratchRepository
from autplay.application.provider_scratch import ProviderScratchService, provider_scratch_id
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .conftest import DatabaseHarness
from .historical_guard_support import (
    PAYLOAD,
    REV39,
    REV40,
    REV41,
    REV42,
    HistoricalRevision,
    HistoricalStage,
    assert_exact_refusal,
    revision,
    seed_historical_source,
    source_snapshot,
    stage_without_scratch,
)


@pytest.mark.parametrize(
    ("head", "target", "stage", "message"),
    [
        (REV39, "0038_worker_resource_wait", None, "Refusing to discard Internet ingest lineage"),
        (REV40, REV39, "EXITED", "Refusing to discard provider staging ownership"),
    ],
)
def test_historical_ingest_and_staging_exact_refusal_preserve_source(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    head: HistoricalRevision,
    target: str,
    stage: HistoricalStage | None,
    message: str,
) -> None:
    database_harness.upgrade(empty_database_name, head)
    with database_harness.connect(empty_database_name) as connection:
        source = seed_historical_source(connection, head, stage=stage)
        connection.commit()
        before = source_snapshot(connection, source)
        stage_before = (
            connection.execute(
                "SELECT to_jsonb(p) FROM vault.provider_staging p WHERE execution_id=%s",
                (source.execution_id,),
            ).fetchone()
            if stage is not None
            else None
        )
    assert_exact_refusal(database_harness, empty_database_name, target, message)
    with database_harness.connect(empty_database_name) as connection:
        assert revision(connection) == head
        assert source_snapshot(connection, source) == before
        if stage is not None:
            assert (
                connection.execute(
                    "SELECT to_jsonb(p) FROM vault.provider_staging p WHERE execution_id=%s",
                    (source.execution_id,),
                ).fetchone()
                == stage_before
            )


def test_historical_scratch_downgrade_waits_then_preserves_committed_claim(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    database_harness.upgrade(empty_database_name, REV41)
    with database_harness.connect(empty_database_name) as connection:
        source = seed_historical_source(connection, REV41, stage="HANDED_OFF")
        connection.commit()
        before = source_snapshot(connection, source)
        provider_before = stage_without_scratch(connection, source)
    claim_id = provider_scratch_id(source.execution_id)
    with (
        database_harness.connect(empty_database_name) as blocker,
        database_harness.connect(empty_database_name) as observer,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        blocker.execute(
            "SELECT execution_id FROM vault.provider_staging WHERE execution_id=%s FOR UPDATE",
            (source.execution_id,),
        )
        blocker.execute(
            "UPDATE vault.provider_staging SET scratch_claim_id=%s,"
            "scratch_claimed_at=clock_timestamp(),updated_at=clock_timestamp() "
            "WHERE execution_id=%s",
            (claim_id, source.execution_id),
        )
        pid_row = blocker.execute("SELECT pg_backend_pid()").fetchone()
        assert pid_row is not None
        pending = pool.submit(
            assert_exact_refusal,
            database_harness,
            empty_database_name,
            REV40,
            "Refusing to discard provider scratch ownership",
        )
        try:
            deadline = monotonic() + 5
            while True:
                observer.execute("SELECT pg_stat_clear_snapshot()")
                waiter = observer.execute(
                    "SELECT l.pid FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                    "JOIN pg_class r ON r.oid=l.relation "
                    "JOIN pg_namespace n ON n.oid=r.relnamespace "
                    "WHERE l.locktype='relation' AND l.mode='AccessExclusiveLock' "
                    "AND NOT l.granted AND a.datname=current_database() "
                    "AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "AND n.nspname='vault' AND r.relname='provider_staging' "
                    "AND %s=ANY(pg_blocking_pids(l.pid))",
                    (pid_row[0],),
                ).fetchone()
                if waiter is not None:
                    assert waiter[0] != pid_row[0]
                    break
                assert not pending.done(), "Downgrade must wait for the uncommitted claim"
                assert monotonic() < deadline, "Expected ACCESS EXCLUSIVE table lock waiter"
                sleep(0.005)
            assert not pending.done(), "Observed downgrade remains blocked before claim commit"
            blocker.commit()
        finally:
            blocker.rollback()
        pending.result(timeout=10)
    with database_harness.connect(empty_database_name) as connection:
        assert revision(connection) == REV41
        assert source_snapshot(connection, source) == before
        # updated_at advances with the claim; every other provider fact is retained.
        after = stage_without_scratch(connection, source)
        assert {key: value for key, value in after.items() if key != "updated_at"} == {
            key: value for key, value in provider_before.items() if key != "updated_at"
        }
        assert connection.execute(
            "SELECT scratch_claim_id,scratch_claimed_at IS NOT NULL,scratch_retired_at "
            "FROM vault.provider_staging WHERE execution_id=%s",
            (source.execution_id,),
        ).fetchone() == (claim_id, True, None)
    engine = create_engine(database_harness.database_url(empty_database_name))
    try:
        repository = PostgresProviderScratchRepository(sessionmaker(engine))
        replayed = repository.claim(source.execution_id)
        assert replayed is not None
        assert replayed.claim_id == claim_id and not replayed.completed
        assert repository.claim(source.execution_id) == replayed
    finally:
        engine.dispose()


def test_historical_unclaimed_handoff_roundtrip_preserves_bytes_and_can_retire(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    tmp_path: Path,
) -> None:
    database_harness.upgrade(empty_database_name, REV41)
    with database_harness.connect(empty_database_name) as connection:
        source = seed_historical_source(connection, REV41, stage="HANDED_OFF")
        connection.commit()
        before = source_snapshot(connection, source)
        provider_before = stage_without_scratch(connection, source)
    # The provider adapter requires the legitimate initialized Vault directories.
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    staged = tmp_path / "staging" / f"provider-{source.execution_id.hex}"
    staged.write_bytes(PAYLOAD)
    file_identity = (staged.stat().st_dev, staged.stat().st_ino)
    workspace = storage.create_workspace(source.execution_id)
    (workspace / "audio.media").write_bytes(PAYLOAD)
    database_harness.downgrade(empty_database_name, REV40)
    with database_harness.connect(empty_database_name) as connection:
        assert revision(connection) == REV40
        assert source_snapshot(connection, source) == before
        assert stage_without_scratch(connection, source) == provider_before
        assert connection.execute(
            "SELECT state,upload_session_id FROM vault.provider_staging WHERE execution_id=%s",
            (source.execution_id,),
        ).fetchone() == ("HANDED_OFF", source.upload_id)
        assert connection.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema='vault' "
            "AND table_name='provider_staging' AND column_name LIKE 'scratch_%'",
        ).fetchone() == (0,)
    assert staged.read_bytes() == PAYLOAD
    assert (workspace / "audio.media").read_bytes() == PAYLOAD
    # Adjacent upgrade avoids creating newer guards or using newer authority models.
    database_harness.upgrade(empty_database_name, REV41)
    with database_harness.connect(empty_database_name) as connection:
        assert revision(connection) == REV41
        assert source_snapshot(connection, source) == before
        assert stage_without_scratch(connection, source) == provider_before
        assert connection.execute(
            "SELECT scratch_claim_id,scratch_claimed_at,scratch_retired_at "
            "FROM vault.provider_staging WHERE execution_id=%s",
            (source.execution_id,),
        ).fetchone() == (None, None, None)
    engine = create_engine(database_harness.database_url(empty_database_name))
    try:
        repository = PostgresProviderScratchRepository(sessionmaker(engine))
        assert repository.pending() == (source.execution_id,)
        assert ProviderScratchService(repository, storage).retire(source.execution_id)
        assert staged.read_bytes() == PAYLOAD
        assert (staged.stat().st_dev, staged.stat().st_ino) == file_identity
        assert not workspace.exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("closed", [False, True])
def test_historical_maintenance_specific_guard_preserves_run_and_closure(
    database_harness: DatabaseHarness,
    empty_database_name: str,
    closed: bool,
) -> None:
    database_harness.upgrade(empty_database_name, REV42)
    with database_harness.connect(empty_database_name) as connection:
        source = seed_historical_source(connection, REV42, stage="HANDED_OFF")
        claim_id = provider_scratch_id(source.execution_id)
        connection.execute(
            "UPDATE vault.provider_staging SET scratch_claim_id=%s,"
            "scratch_claimed_at=clock_timestamp(),updated_at=clock_timestamp() "
            "WHERE execution_id=%s",
            (claim_id, source.execution_id),
        )
        execution_id, owner_run_id = uuid4(), uuid4()
        connection.execute(
            "INSERT INTO vault.provider_maintenance(execution_id,owner_run_id,"
            "provider_execution_id,claim_id,action,singleton_id,state,created_at) "
            "VALUES(%s,%s,%s,%s,'SCRATCH',1,'PREPARED',clock_timestamp())",
            (execution_id, owner_run_id, source.execution_id, claim_id),
        )
        connection.execute(
            "UPDATE vault.provider_maintenance SET state='RUNNING',started_at=clock_timestamp(),"
            "child_pid=12345,child_identity_sha256=%s WHERE execution_id=%s",
            (b"i" * 32, execution_id),
        )
        if closed:
            connection.execute(
                "UPDATE vault.provider_maintenance SET state='CLOSED',closed_at=clock_timestamp(),"
                "closure_kind='PROCESS_EXIT',closure_evidence_sha256=%s,exit_code=0 "
                "WHERE execution_id=%s",
                (b"p" * 32, execution_id),
            )
        connection.commit()
        before = source_snapshot(connection, source)
        stage_before = connection.execute(
            "SELECT to_jsonb(p) FROM vault.provider_staging p WHERE execution_id=%s",
            (source.execution_id,),
        ).fetchone()
        run_before = connection.execute(
            "SELECT to_jsonb(m) FROM vault.provider_maintenance m WHERE execution_id=%s",
            (execution_id,),
        ).fetchone()
        assert connection.execute(
            "SELECT to_regclass('account.internal_io_policy')"
        ).fetchone() == (None,)
    assert_exact_refusal(
        database_harness,
        empty_database_name,
        REV41,
        "Refusing to discard maintenance ownership",
    )
    with database_harness.connect(empty_database_name) as connection:
        assert revision(connection) == REV42
        assert source_snapshot(connection, source) == before
        assert (
            connection.execute(
                "SELECT to_jsonb(p) FROM vault.provider_staging p WHERE execution_id=%s",
                (source.execution_id,),
            ).fetchone()
            == stage_before
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(m) FROM vault.provider_maintenance m WHERE execution_id=%s",
                (execution_id,),
            ).fetchone()
            == run_before
        )

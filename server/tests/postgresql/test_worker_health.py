from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.admin_views import PostgreSqlAdminViews
from autplay.adapters.postgresql.models.worker_health import WorkerHealthRow
from autplay.adapters.postgresql.worker_health import PostgreSqlWorkerHealth
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor
from autplay.runtime.worker_health import WorkerSample
from psycopg import Connection
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness


def _server(connection: Connection[object]) -> UUID:
    server_id = uuid4()
    connection.execute(
        """INSERT INTO account.server_instance (
        server_instance_id, identity_epoch, identity_public_key_spki, identity_thumbprint_sha256,
        label_hint, api_origin, stream_origin, capability_revision, created_at, updated_at)
        VALUES (%s, 1, %s, %s, 'Telemetry server', 'https://api.invalid',
        'https://stream.invalid', 1, now(), now())""",
        (server_id, b"s" * 65, sha256(server_id.bytes).digest()),
    )
    connection.commit()
    return server_id


def test_worker_readings_are_fresh_fenced_and_account_scoped(
    database_connection: Connection[object], database_url: str
) -> None:
    server_id = _server(database_connection)
    owners = [uuid4(), uuid4()]
    for owner in owners:
        database_connection.execute(
            "INSERT INTO account.user_account (user_id,display_name,role) "
            "VALUES (%s,'Owner','OWNER')",
            (owner,),
        )
    for owner, kind, state in (
        (owners[0], "vault.ingest", "QUEUED"),
        (owners[1], "vault.ingest", "QUEUED"),
        (owners[0], "gpu.enrichment", "QUEUED"),
    ):
        database_connection.execute(
            "INSERT INTO jobs.job (user_id,job_type,schema_version,state) VALUES (%s,%s,1,%s)",
            (owner, kind, state),
        )
    for owner, offset in ((owners[0], 300), (owners[0], -300), (owners[1], 300)):
        database_connection.execute(
            """INSERT INTO jobs.job
            (user_id,job_type,schema_version,state,lease_owner,lease_deadline,heartbeat_at)
            VALUES (%s,'vault.ingest',1,'RUNNING','test-cpu',
            now() + %s * interval '1 second',now())""",
            (owner, offset),
        )
    database_connection.execute(
        "INSERT INTO jobs.job (user_id,job_type,schema_version,state) "
        "VALUES (%s,'vault.ingest',2,'QUEUED')",
        (owners[0],),
    )
    database_connection.commit()
    actor = WebActor(server_id, owners[0], uuid4(), AccountRole.OWNER, 0)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        old = PostgreSqlWorkerHealth(sessions)
        old.publish(WorkerSample(65.5, 100, 1000, True))
        with sessions() as session:
            view = PostgreSqlAdminViews(session).dashboard(actor)
            assert view.worker_status == "HEALTHY"
            assert view.worker is not None and view.worker.fresh and view.worker.busy
            assert view.worker.cpu_percent == 65.5
            assert view.worker.queued_jobs == 1
            assert view.worker.active_jobs == 1
            assert 0 < view.worker.fresh_for_seconds <= 30
        new = PostgreSqlWorkerHealth(sessions)
        new.publish(WorkerSample(5.0, 80, 1000, False))
        old.publish(WorkerSample(90, 200, 1000, True))
        old.publish(WorkerSample(None, None, None, False), running=False)
        with sessions() as session:
            view = PostgreSqlAdminViews(session).dashboard(actor)
            assert view.worker is not None and view.worker.cpu_percent == 5
            assert view.worker.fresh and not view.worker.busy
        with sessions.begin() as session:
            session.execute(
                update(WorkerHealthRow).values(observed_at=func.now() - timedelta(seconds=31))
            )
        with sessions() as session:
            view = PostgreSqlAdminViews(session).dashboard(actor)
            assert view.worker_status == "UNAVAILABLE"
            assert view.worker is not None and not view.worker.fresh
            assert view.worker.cpu_percent is None and view.worker.memory_bytes is None
        new.publish(WorkerSample(2, 80, 1000, False), running=False)
        with sessions() as session:
            assert PostgreSqlAdminViews(session).dashboard(actor).worker_status == "UNAVAILABLE"
            assert session.scalar(select(func.count()).select_from(WorkerHealthRow)) == 1
        with pytest.raises(IntegrityError), sessions.begin() as session:
            session.execute(update(WorkerHealthRow).values(cpu_percent=101))
    finally:
        engine.dispose()


def test_lost_commit_acknowledgement_cannot_reclaim_a_newer_reporter(
    database_connection: Connection[object], database_url: str
) -> None:
    server_id = _server(database_connection)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine)

    class LostAcknowledgement:
        failed = False

        @contextmanager
        def begin(self) -> Iterator[Session]:
            with sessions.begin() as session:
                yield session
            if not self.failed:
                self.failed = True
                raise OperationalError("commit", None, RuntimeError("acknowledgement lost"))

    try:
        old = PostgreSqlWorkerHealth(LostAcknowledgement())  # type: ignore[arg-type]
        with pytest.raises(OperationalError):
            old.publish(WorkerSample(90, 20, 100, True))
        new = PostgreSqlWorkerHealth(sessions)
        new.publish(WorkerSample(10, 30, 100, False))
        old.publish(WorkerSample(90, 20, 100, True))
        old.publish(WorkerSample(None, None, None, False), running=False)
        with sessions() as session:
            row = session.get(WorkerHealthRow, server_id)
            assert row is not None and row.running and row.cpu_percent == 10
    finally:
        engine.dispose()


def test_missing_or_ambiguous_server_identity_does_not_publish(
    database_connection: Connection[object], database_url: str
) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine)
        sink = PostgreSqlWorkerHealth(sessions)
        sink.publish(WorkerSample(10, 20, 30, False))
        _server(database_connection)
        _server(database_connection)
        sink.publish(WorkerSample(10, 20, 30, False))
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(WorkerHealthRow)) == 0
    finally:
        engine.dispose()


def test_health_migration_round_trip_preserves_server_identity(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    with database_harness.connect(database_name) as connection:
        server_id = _server(connection)
    database_harness.downgrade(database_name, "0061_admission_device_name")
    database_harness.upgrade(database_name)
    with database_harness.connect(database_name) as connection:
        assert connection.execute(
            "SELECT server_instance_id FROM account.server_instance"
        ).fetchone() == (server_id,)
        assert connection.execute("SELECT count(*) FROM jobs.worker_health").fetchone() == (0,)


def test_vault_warning_reasons_are_real_counts(
    database_connection: Connection[object], database_url: str
) -> None:
    server_id = _server(database_connection)
    owner = uuid4()
    database_connection.execute(
        "INSERT INTO account.user_account (user_id,display_name,role) "
        "VALUES (%s,'Vault owner','OWNER')",
        (owner,),
    )
    object_id = uuid4()
    database_connection.execute(
        """INSERT INTO vault.vault_object
        (vault_object_id,sha256,byte_size,detected_mime_type,commit_status)
        VALUES (%s,%s,2048,'audio/flac','QUARANTINED')""",
        (object_id, b"v" * 32),
    )
    for state in ("MISSING", "CORRUPT", "QUARANTINED"):
        database_connection.execute(
            """INSERT INTO vault.vault_replica
            (vault_object_id,storage_backend,storage_key,replica_status)
            VALUES (%s,'FILESYSTEM',%s,%s)""",
            (object_id, f"private/{state}", state),
        )
    database_connection.commit()
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            view = PostgreSqlAdminViews(session).dashboard(
                WebActor(server_id, owner, uuid4(), AccountRole.OWNER, 0)
            )
            assert view.vault_status == "DEGRADED"
            assert view.vault is not None
            assert view.vault.quarantined_objects == 1 and view.vault.unhealthy_replicas == 3
            assert (
                view.vault.missing_replicas,
                view.vault.corrupt_replicas,
                view.vault.quarantined_replicas,
            ) == (1, 1, 1)
    finally:
        engine.dispose()

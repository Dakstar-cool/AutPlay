"""Real PostgreSQL + filesystem evidence for P06 CAS crash recovery."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.vault_uow import (
    SqlAlchemyVaultUnitOfWorkFactory,
    TransactionalIngestRepository,
)
from autplay.application.vault_ingest import IngestSession
from autplay.application.vault_reconciliation import ReconcileMode, VaultReconciliationService
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    OpaqueStorageKey,
    Sha256Digest,
    VaultLimits,
)
from psycopg import Connection, Cursor
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def test_concurrent_same_sha_resumes_every_commit_window_and_converges(
    database_connection: Connection[Any], database_url: str, tmp_path: Path
) -> None:
    payload = b"one immutable P06 audio representation"
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    recording_id, uploads = _seed_processing_uploads(
        database_connection, count=2, expected_size=len(payload), declared_sha256=digest.value
    )
    database_connection.commit()

    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    factory = SqlAlchemyVaultUnitOfWorkFactory(sessions)
    repository = TransactionalIngestRepository(factory)
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    metadata = AudioTechnicalMetadata("flac", "flac", 48_000, 2, 1_000, None, 16)
    evidence = ChromaprintEvidence("chromaprint", "1.6.1", 1_000, b"fixture-fingerprint")
    ingests: list[IngestSession] = []
    for upload_id, _job_id, staging_key in uploads:
        key = OpaqueStorageKey(staging_key)
        storage.create_staging(key)
        storage.write_chunk(key, offset=0, payload=payload, payload_sha256=digest)
        ingests.append(IngestSession(upload_id, recording_id, key, len(payload), digest.value))

    barrier = Barrier(2)

    def prepare(ingest: IngestSession) -> str:
        barrier.wait()
        return repository.prepare_commit(
            ingest, storage.verify_staging(ingest.staging_key), metadata, evidence
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            actions = list(executor.map(prepare, ingests))
        assert sorted(actions) == ["PUBLISH", "WAIT"]
        publisher = ingests[actions.index("PUBLISH")]
        waiter = ingests[actions.index("WAIT")]
        verified = storage.verify_staging(publisher.staging_key)

        # Crash after DB prepare but before publication: the same upload may resume.
        assert repository.prepare_commit(publisher, verified, metadata, evidence) == "PUBLISH"
        first_publish = storage.commit_staging(publisher.staging_key, verified)
        assert not first_publish.already_present

        # Crash after file publication but before DB finalization: publication is idempotent.
        assert repository.prepare_commit(publisher, verified, metadata, evidence) == "PUBLISH"
        second_publish = storage.commit_staging(publisher.staging_key, verified)
        assert second_publish.already_present
        repository.finalize_published(
            publisher, second_publish.storage_key, metadata, evidence, reused=False
        )

        # Crash after DB finalization but before staging cleanup: reconciliation preserves bytes.
        with factory() as unit:
            report = VaultReconciliationService(repository=unit.vault, storage=storage).run(
                mode=ReconcileMode.APPLY, limit=100
            )
            unit.commit()
        assert report.quarantined == 1

        # The concurrent waiter now deterministically reuses the committed exact bytes.
        assert (
            repository.prepare_commit(
                waiter, storage.verify_staging(waiter.staging_key), metadata, evidence
            )
            == "REUSED"
        )
        repository.finalize_published(
            waiter, OpaqueStorageKey(digest.hex), metadata, evidence, reused=True
        )
        storage.cleanup_staging(waiter.staging_key)

        counts = database_connection.execute(
            """
            SELECT
                (SELECT count(*) FROM vault.vault_object),
                (SELECT count(*) FROM vault.audio_variant),
                (SELECT count(*) FROM vault.audio_fingerprint),
                (SELECT count(*) FROM audit.audit_event
                    WHERE action IN ('vault.ingest_committed', 'vault.ingest_reused'))
            """
        ).fetchone()
        states = database_connection.execute(
            "SELECT state FROM vault.upload_session ORDER BY idempotency_key"
        ).fetchall()
        assert counts == (1, 1, 1, 2)
        assert sorted(states) == [("COMMITTED",), ("REUSED",)]
        assert storage.inventory().object_keys == (OpaqueStorageKey(digest.hex),)
        assert storage.inventory().staging_keys == ()
    finally:
        engine.dispose()


def test_reconciliation_quarantines_orphan_and_corrupt_final_bytes(
    database_connection: Connection[Any], database_url: str, tmp_path: Path
) -> None:
    engine: Engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    factory = SqlAlchemyVaultUnitOfWorkFactory(sessions)
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    try:
        orphan_payload = b"orphan final"
        orphan = _publish(storage, "orphanstage", orphan_payload)
        with factory() as unit:
            report = VaultReconciliationService(repository=unit.vault, storage=storage).run(
                mode=ReconcileMode.APPLY, limit=100
            )
            unit.commit()
        assert report.quarantined == 1
        assert orphan not in storage.inventory().object_keys

        healthy_payload = b"healthy tracked final"
        healthy = _publish(storage, "healthystage", healthy_payload)
        healthy_object_id = _returned_uuid(
            database_connection.execute(
                """
                INSERT INTO vault.vault_object (
                    sha256, byte_size, detected_mime_type, commit_status, committed_at
                ) VALUES (%s, %s, 'audio/flac', 'COMMITTED', now())
                RETURNING vault_object_id
                """,
                (bytes.fromhex(healthy.value), len(healthy_payload)),
            )
        )
        database_connection.execute(
            """
            INSERT INTO vault.vault_replica (
                vault_object_id, storage_backend, storage_key, replica_status, verified_at
            ) VALUES (%s, 'LOCAL_FILESYSTEM', %s, 'AVAILABLE', now() - interval '2 hours')
            """,
            (healthy_object_id, healthy.value),
        )
        tracked_payload = b"tracked final"
        tracked = _publish(storage, "trackedstage", tracked_payload)
        object_id = _returned_uuid(
            database_connection.execute(
                """
                INSERT INTO vault.vault_object (
                    sha256, byte_size, detected_mime_type, commit_status, committed_at
                ) VALUES (%s, %s, 'audio/flac', 'COMMITTED', now())
                RETURNING vault_object_id
                """,
                (bytes.fromhex(tracked.value), len(tracked_payload)),
            )
        )
        database_connection.execute(
            """
            INSERT INTO vault.vault_replica (
                vault_object_id, storage_backend, storage_key, replica_status, verified_at
            ) VALUES (%s, 'LOCAL_FILESYSTEM', %s, 'AVAILABLE', now() - interval '1 hour')
            """,
            (object_id, tracked.value),
        )
        database_connection.commit()
        object_path = tmp_path / "objects" / tracked.value[:2] / tracked.value[2:4] / tracked.value
        if os.name != "nt":
            object_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        object_path.write_bytes(b"corrupted bytes")
        if os.name != "nt":
            object_path.chmod(stat.S_IRUSR)

        with factory() as unit:
            first_page = VaultReconciliationService(repository=unit.vault, storage=storage).run(
                mode=ReconcileMode.APPLY, limit=1
            )
            unit.commit()
        assert first_page.repaired == 0
        assert first_page.remaining >= 1
        with factory() as unit:
            report = VaultReconciliationService(repository=unit.vault, storage=storage).run(
                mode=ReconcileMode.APPLY, limit=1
            )
            unit.commit()
        status = database_connection.execute(
            """
            SELECT vo.commit_status, vr.replica_status, vo.verification_error
            FROM vault.vault_object vo
            JOIN vault.vault_replica vr USING (vault_object_id)
            WHERE vo.vault_object_id = %s
            """,
            (object_id,),
        ).fetchone()
        assert report.repaired == 1 and report.quarantined == 1
        assert status == ("QUARANTINED", "QUARANTINED", "vault_replica_integrity_mismatch")
        assert tracked not in storage.inventory().object_keys
    finally:
        engine.dispose()


def test_reconciliation_expires_open_and_repairs_missing_processing_staging(
    database_connection: Connection[Any], database_url: str, tmp_path: Path
) -> None:
    payload = b"abc"
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    _recording_id, uploads = _seed_processing_uploads(
        database_connection,
        count=2,
        expected_size=len(payload),
        declared_sha256=digest.value,
    )
    expired_upload, _expired_job, expired_key = uploads[0]
    missing_upload, _missing_job, _missing_key = uploads[1]
    database_connection.execute(
        """
        UPDATE vault.upload_session
        SET state = 'OPEN', job_id = NULL, sealed_at = NULL,
            received_size = 0, chunk_count = 0,
            created_at = now() - interval '2 hours',
            expires_at = now() - interval '1 hour'
        WHERE upload_session_id = %s
        """,
        (expired_upload,),
    )
    database_connection.commit()

    engine: Engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    factory = SqlAlchemyVaultUnitOfWorkFactory(sessions)
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    expired_staging = OpaqueStorageKey(expired_key)
    storage.create_staging(expired_staging)
    storage.write_chunk(
        expired_staging,
        offset=0,
        payload=payload[:1],
        payload_sha256=Sha256Digest(hashlib.sha256(payload[:1]).digest()),
    )
    try:
        with factory() as unit:
            report = VaultReconciliationService(repository=unit.vault, storage=storage).run(
                mode=ReconcileMode.APPLY, limit=100
            )
            unit.commit()
        states = dict(
            database_connection.execute(
                """
                SELECT upload_session_id, state
                FROM vault.upload_session
                WHERE upload_session_id IN (%s, %s)
                """,
                (expired_upload, missing_upload),
            ).fetchall()
        )
        assert states == {expired_upload: "EXPIRED", missing_upload: "FAILED"}
        assert report.repaired == 2
        assert report.quarantined == 1
        assert storage.inventory().staging_keys == ()
    finally:
        engine.dispose()


def test_exact_byte_reuse_fails_closed_for_a_different_active_recording(
    database_connection: Connection[Any], database_url: str, tmp_path: Path
) -> None:
    payload = b"same bytes are not recording identity"
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    first_recording, first_uploads = _seed_processing_uploads(
        database_connection,
        count=1,
        expected_size=len(payload),
        declared_sha256=digest.value,
    )
    second_recording, second_uploads = _seed_processing_uploads(
        database_connection,
        count=1,
        expected_size=len(payload),
        declared_sha256=digest.value,
    )
    assert first_recording != second_recording
    database_connection.commit()

    engine: Engine = create_engine(database_url, pool_pre_ping=True)
    factory = SqlAlchemyVaultUnitOfWorkFactory(
        sessionmaker(engine, class_=Session, expire_on_commit=False)
    )
    repository = TransactionalIngestRepository(factory)
    storage = FilesystemVaultStorage(
        tmp_path, limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=1024)
    )
    metadata = AudioTechnicalMetadata("flac", "flac", 48_000, 2, 1_000, None, 16)
    evidence = ChromaprintEvidence("chromaprint", "1.6.1", 1_000, b"strict-reuse")

    def ingest_for(
        recording_id: uuid.UUID, upload: tuple[uuid.UUID, uuid.UUID, str]
    ) -> IngestSession:
        upload_id, _job_id, raw_key = upload
        key = OpaqueStorageKey(raw_key)
        storage.create_staging(key)
        storage.write_chunk(key, offset=0, payload=payload, payload_sha256=digest)
        return IngestSession(upload_id, recording_id, key, len(payload), digest.value)

    publisher = ingest_for(first_recording, first_uploads[0])
    conflict = ingest_for(second_recording, second_uploads[0])
    try:
        verified = storage.verify_staging(publisher.staging_key)
        assert repository.prepare_commit(publisher, verified, metadata, evidence) == "PUBLISH"
        committed = storage.commit_staging(publisher.staging_key, verified)
        assert repository.finalize_published(
            publisher, committed.storage_key, metadata, evidence, reused=False
        )
        storage.cleanup_staging(publisher.staging_key)

        assert (
            repository.prepare_commit(
                conflict, storage.verify_staging(conflict.staging_key), metadata, evidence
            )
            == "CONFLICT"
        )
        repository.quarantine(conflict, "vault.integrity_conflict")
        storage.quarantine(conflict.staging_key, OpaqueStorageKey("reuse-conflict"))

        variant_rows = database_connection.execute(
            "SELECT recording_id FROM vault.audio_variant"
        ).fetchall()
        conflict_state = database_connection.execute(
            """
            SELECT state, vault_object_id, audio_variant_id
            FROM vault.upload_session WHERE upload_session_id = %s
            """,
            (conflict.upload_session_id,),
        ).fetchone()
        assert variant_rows == [(first_recording,)]
        assert conflict_state == ("QUARANTINED", None, None)
        assert len(storage.inventory().object_keys) == 1
    finally:
        engine.dispose()


def _seed_processing_uploads(
    connection: Connection[Any], *, count: int, expected_size: int, declared_sha256: bytes
) -> tuple[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, str]]]:
    user_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.user_account (display_name, role)
            VALUES (%s, 'USER') RETURNING user_id
            """,
            (f"vault-user-{uuid.uuid4().hex}",),
        )
    )
    device_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.device (user_id, device_name, platform, app_version)
            VALUES (%s, %s, 'OTHER', 'p06') RETURNING device_id
            """,
            (user_id, f"vault-device-{uuid.uuid4().hex}"),
        )
    )
    artist_credit_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES (%s, %s) RETURNING artist_credit_id
            """,
            ("P06 Artist", f"p06-artist-{uuid.uuid4().hex}"),
        )
    )
    recording_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.recording (artist_credit_id, title, normalized_title)
            VALUES (%s, 'P06 Recording', %s) RETURNING recording_id
            """,
            (artist_credit_id, f"p06-recording-{uuid.uuid4().hex}"),
        )
    )
    uploads: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for index in range(count):
        job_id = _returned_uuid(
            connection.execute(
                """
                INSERT INTO jobs.job (job_type, schema_version, user_id)
                VALUES ('vault.ingest', 1, %s) RETURNING job_id
                """,
                (user_id,),
            )
        )
        staging_key = f"concurrent-{index}-{uuid.uuid4().hex}"
        upload_id = _returned_uuid(
            connection.execute(
                """
                INSERT INTO vault.upload_session (
                    user_id, device_id, target_recording_id, idempotency_key,
                    request_hash, declared_sha256, expected_size, received_size,
                    chunk_size, max_chunks, chunk_count, staging_key, state,
                    job_id, expires_at, sealed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    1024, 1, 1, %s, 'PROCESSING', %s, %s, now()
                ) RETURNING upload_session_id
                """,
                (
                    user_id,
                    device_id,
                    recording_id,
                    f"concurrent-{index}",
                    hashlib.sha256(f"request-{index}".encode()).digest(),
                    declared_sha256,
                    expected_size,
                    expected_size,
                    staging_key,
                    job_id,
                    datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        uploads.append((upload_id, job_id, staging_key))
    return recording_id, uploads


def _publish(storage: FilesystemVaultStorage, staging_key: str, payload: bytes) -> OpaqueStorageKey:
    key = OpaqueStorageKey(staging_key)
    digest = Sha256Digest(hashlib.sha256(payload).digest())
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=digest)
    committed = storage.commit_staging(key, storage.verify_staging(key))
    storage.cleanup_staging(key)
    return committed.storage_key


def _returned_uuid(cursor: Cursor[Any]) -> uuid.UUID:
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return row[0]

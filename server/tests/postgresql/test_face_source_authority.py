"""Face source metadata selection is exact and fails closed after quarantine."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.face_source import FaceSourceError, SqlAlchemyFaceSourceReader

from .conftest import DatabaseHarness
from .identity_factory import insert_recording

SOURCE_SHA256 = b"s" * 32


def _source(database_harness: DatabaseHarness, name: str) -> tuple[UUID, UUID]:
    with database_harness.connect(name) as connection:
        recording_id = insert_recording(connection, "face-source")
        object_row = connection.execute(
            "INSERT INTO vault.vault_object "
            "(sha256,byte_size,detected_mime_type,commit_status,committed_at) "
            "VALUES(%s,4096,'audio/flac','COMMITTED',now()) RETURNING vault_object_id",
            (SOURCE_SHA256,),
        ).fetchone()
        assert object_row is not None
        connection.execute(
            "INSERT INTO vault.vault_replica "
            "(vault_object_id,storage_backend,storage_key,replica_status,verified_at) "
            "VALUES(%s,'LOCAL_FILESYSTEM',%s,'AVAILABLE',now())",
            (object_row[0], f"face-source-{object_row[0]}"),
        )
        variant = connection.execute(
            "INSERT INTO vault.audio_variant "
            "(recording_id,vault_object_id,codec,container,sample_rate_hz,channels,duration_ms) "
            "VALUES(%s,%s,'flac','flac',48000,2,180000) RETURNING audio_variant_id",
            (recording_id, object_row[0]),
        ).fetchone()
        assert variant is not None
        connection.execute(
            "INSERT INTO vault.recording_canonical_variant "
            "(recording_id,audio_variant_id,policy_version) VALUES(%s,%s,'FACE_TEST_V1')",
            (recording_id, variant[0]),
        )
        connection.commit()
        return recording_id, variant[0]


def test_face_source_requires_exact_current_valid_committed_bytes(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    recording_id, variant_id = _source(database_harness, empty_database_name)
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    reader = SqlAlchemyFaceSourceReader()
    try:
        with sessions.begin() as session:
            source = reader.lock_current(
                session,
                recording_id=recording_id,
                audio_variant_id=variant_id,
                expected_source_sha256=SOURCE_SHA256,
            )
            assert source.source_sha256 == SOURCE_SHA256
            assert source.source_byte_size == 4096
            assert source.canonical_policy_version == "FACE_TEST_V1"
            with pytest.raises(FaceSourceError, match="not_current"):
                reader.lock_current(
                    session,
                    recording_id=recording_id,
                    audio_variant_id=variant_id,
                    expected_source_sha256=b"x" * 32,
                )

        with database_harness.connect(empty_database_name) as connection:
            connection.execute(
                "UPDATE vault.vault_replica SET replica_status='QUARANTINED',"
                "row_version=row_version+1 WHERE vault_object_id=%s",
                (source.vault_object_id,),
            )
            connection.commit()
        with sessions.begin() as session, pytest.raises(FaceSourceError, match="not_current"):
            reader.lock_current(
                session,
                recording_id=recording_id,
                audio_variant_id=variant_id,
                expected_source_sha256=SOURCE_SHA256,
            )
        with database_harness.connect(empty_database_name) as connection:
            connection.execute(
                "UPDATE vault.vault_replica SET replica_status='AVAILABLE',"
                "row_version=row_version+1 WHERE vault_object_id=%s",
                (source.vault_object_id,),
            )
            connection.execute(
                "UPDATE vault.audio_variant SET validation_status='QUARANTINED',"
                "row_version=row_version+1 WHERE audio_variant_id=%s",
                (variant_id,),
            )
            connection.commit()
        with sessions.begin() as session, pytest.raises(FaceSourceError, match="not_current"):
            reader.lock_current(
                session,
                recording_id=recording_id,
                audio_variant_id=variant_id,
                expected_source_sha256=SOURCE_SHA256,
            )
    finally:
        engine.dispose()

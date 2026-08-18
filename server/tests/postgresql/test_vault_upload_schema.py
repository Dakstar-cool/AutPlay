"""Real-PostgreSQL constraints for P06 resumable upload persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import Connection, Cursor


def _returned_uuid(cursor: Cursor[Any]) -> uuid.UUID:
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return row[0]


def _insert_user(connection: Connection[Any]) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.user_account (display_name, role)
            VALUES (%s, 'USER') RETURNING user_id
            """,
            (f"upload-user-{uuid.uuid4().hex}",),
        )
    )


def _insert_device(connection: Connection[Any], user_id: uuid.UUID) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.device (user_id, device_name, platform, app_version)
            VALUES (%s, %s, 'ANDROID', 'p06') RETURNING device_id
            """,
            (user_id, f"upload-device-{uuid.uuid4().hex}"),
        )
    )


def _insert_recording(connection: Connection[Any]) -> uuid.UUID:
    suffix = uuid.uuid4().hex
    artist_credit_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES (%s, %s) RETURNING artist_credit_id
            """,
            (f"artist-{suffix}", f"artist-{suffix}"),
        )
    )
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.recording (artist_credit_id, title, normalized_title)
            VALUES (%s, %s, %s) RETURNING recording_id
            """,
            (artist_credit_id, f"recording-{suffix}", f"recording-{suffix}"),
        )
    )


def _insert_open_session(
    connection: Connection[Any],
    *,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    recording_id: uuid.UUID,
    idempotency_key: str | None = None,
    request_hash: bytes = b"r" * 32,
    expected_size: int = 1_024,
    chunk_size: int = 1_024,
    max_chunks: int = 1,
    staging_key: str | None = None,
) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO vault.upload_session (
                user_id, device_id, target_recording_id, idempotency_key,
                request_hash, expected_size, chunk_size, max_chunks, staging_key, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING upload_session_id
            """,
            (
                user_id,
                device_id,
                recording_id,
                idempotency_key or f"idem-{uuid.uuid4().hex}",
                request_hash,
                expected_size,
                chunk_size,
                max_chunks,
                staging_key or f"stage-{uuid.uuid4().hex}",
                datetime.now(UTC) + timedelta(hours=1),
            ),
        )
    )


def test_upload_session_idempotency_and_owner_lookup_are_durable(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection)
    device_id = _insert_device(database_connection, user_id)
    recording_id = _insert_recording(database_connection)
    session_id = _insert_open_session(
        database_connection,
        user_id=user_id,
        device_id=device_id,
        recording_id=recording_id,
        idempotency_key="same-key",
    )
    row = database_connection.execute(
        """
        SELECT upload_session_id FROM vault.upload_session
        WHERE upload_session_id = %s AND user_id = %s
        """,
        (session_id, user_id),
    ).fetchone()
    assert row == (session_id,)

    with (
        pytest.raises(psycopg.errors.UniqueViolation) as exc_info,
        database_connection.transaction(),
    ):
        _insert_open_session(
            database_connection,
            user_id=user_id,
            device_id=device_id,
            recording_id=recording_id,
            idempotency_key="same-key",
        )
    assert exc_info.value.diag.constraint_name == "uq_upload_session_user_idempotency"


def test_upload_session_rejects_cross_user_device_ownership(
    database_connection: Connection[Any],
) -> None:
    owner = _insert_user(database_connection)
    other = _insert_user(database_connection)
    owner_device = _insert_device(database_connection, owner)
    recording_id = _insert_recording(database_connection)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info:
        _insert_open_session(
            database_connection,
            user_id=other,
            device_id=owner_device,
            recording_id=recording_id,
        )
    assert exc_info.value.diag.constraint_name == "fk_upload_session_device_owner"


@pytest.mark.parametrize(
    ("request_hash", "expected_size", "chunk_size", "max_chunks", "constraint"),
    [
        (b"r" * 31, 1_024, 1_024, 1, "ck_upload_session_request_hash_len"),
        (b"r" * 32, 1_024, 1_048_577, 1, "upload_session_chunk_size_check"),
        (b"r" * 32, 1_024, 1_024, 4_097, "upload_session_max_chunks_check"),
        (b"r" * 32, 4_097, 1_024, 4, "ck_upload_session_capacity"),
    ],
)
def test_upload_session_enforces_hash_and_size_bounds(
    database_connection: Connection[Any],
    request_hash: bytes,
    expected_size: int,
    chunk_size: int,
    max_chunks: int,
    constraint: str,
) -> None:
    user_id = _insert_user(database_connection)
    device_id = _insert_device(database_connection, user_id)
    recording_id = _insert_recording(database_connection)
    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        _insert_open_session(
            database_connection,
            user_id=user_id,
            device_id=device_id,
            recording_id=recording_id,
            request_hash=request_hash,
            expected_size=expected_size,
            chunk_size=chunk_size,
            max_chunks=max_chunks,
        )
    assert exc_info.value.diag.constraint_name == constraint


def test_upload_session_state_links_reject_incomplete_seal(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection)
    device_id = _insert_device(database_connection, user_id)
    recording_id = _insert_recording(database_connection)
    session_id = _insert_open_session(
        database_connection,
        user_id=user_id,
        device_id=device_id,
        recording_id=recording_id,
    )
    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        database_connection.execute(
            "UPDATE vault.upload_session SET state = 'SEALED' WHERE upload_session_id = %s",
            (session_id,),
        )
    assert exc_info.value.diag.constraint_name == "ck_upload_session_state_links"


def test_upload_chunk_duplicate_receipts_are_rejected_by_durable_keys(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection)
    device_id = _insert_device(database_connection, user_id)
    recording_id = _insert_recording(database_connection)
    session_id = _insert_open_session(
        database_connection,
        user_id=user_id,
        device_id=device_id,
        recording_id=recording_id,
    )
    database_connection.execute(
        """
        INSERT INTO vault.upload_chunk (
            upload_session_id, chunk_index, start_offset, byte_size, sha256
        )
        VALUES (%s, 0, 0, 1_024, %s)
        """,
        (session_id, b"c" * 32),
    )
    with (
        pytest.raises(psycopg.errors.UniqueViolation) as index_exc,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO vault.upload_chunk
                (upload_session_id, chunk_index, start_offset, byte_size, sha256)
            VALUES (%s, 0, 1_024, 1_024, %s)
            """,
            (session_id, b"d" * 32),
        )
    assert index_exc.value.diag.constraint_name == "upload_chunk_pkey"

    with (
        pytest.raises(psycopg.errors.UniqueViolation) as offset_exc,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO vault.upload_chunk
                (upload_session_id, chunk_index, start_offset, byte_size, sha256)
            VALUES (%s, 1, 0, 1_024, %s)
            """,
            (session_id, b"e" * 32),
        )
    assert offset_exc.value.diag.constraint_name == "uq_upload_chunk_start_offset"

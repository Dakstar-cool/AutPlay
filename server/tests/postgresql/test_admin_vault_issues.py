from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from autplay.adapters.postgresql.admin_views import PostgreSqlAdminViews
from autplay.domain.admin_vault import VAULT_ERROR_REASONS
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor
from psycopg import Connection
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .test_vault_upload_schema import _insert_open_session, _insert_recording, _insert_user


def test_vault_error_groups_are_account_scoped_bounded_and_redacted(
    database_connection: Connection[object],
    database_url: str,
) -> None:
    connection = database_connection
    owner, other = _insert_user(connection), _insert_user(connection)
    recording = _insert_recording(connection)
    for user, codes in (
        (
            owner,
            ("media_validation_failed", "private/storage?token=secret", "another/private/path"),
        ),
        (other, ("source_authorization_unavailable",)),
    ):
        device = uuid4()
        connection.execute(
            "INSERT INTO account.device (device_id,user_id,device_name,platform,app_version) "
            "VALUES (%s,%s,'Test','ANDROID','test')",
            (device, user),
        )
        for code in codes:
            upload = _insert_open_session(
                connection,
                user_id=user,
                device_id=device,
                recording_id=recording,
            )
            job = uuid4()
            connection.execute(
                "INSERT INTO jobs.job (job_id,job_type,schema_version,user_id) "
                "VALUES (%s,'vault.ingest',1,%s)",
                (job, user),
            )
            connection.execute(
                "UPDATE vault.upload_session SET state='QUARANTINED', error_code=%s, "
                "job_id=%s, sealed_at=now(), completed_at=%s WHERE upload_session_id=%s",
                (code, job, datetime.now(UTC), upload),
            )
        failed = _insert_open_session(
            connection,
            user_id=user,
            device_id=device,
            recording_id=recording,
        )
        job = uuid4()
        connection.execute(
            "INSERT INTO jobs.job (job_id,job_type,schema_version,user_id) "
            "VALUES (%s,'vault.ingest',1,%s)",
            (job, user),
        )
        connection.execute(
            "UPDATE vault.upload_session SET state='FAILED', "
            "error_code='vault_storage_unavailable', "
            "job_id=%s, sealed_at=now(), completed_at=now() WHERE upload_session_id=%s",
            (job, failed),
        )
    object_id = uuid4()
    connection.execute(
        "INSERT INTO vault.vault_object "
        "(vault_object_id,sha256,byte_size,detected_mime_type,commit_status,verification_error) "
        "VALUES (%s,%s,1024,'audio/flac','QUARANTINED','/private/verification/error')",
        (object_id, b"v" * 32),
    )
    for state in ("MISSING", "CORRUPT", "QUARANTINED"):
        connection.execute(
            "INSERT INTO vault.vault_replica "
            "(vault_object_id,storage_backend,storage_key,replica_status) "
            "VALUES (%s,'FILESYSTEM',%s,%s)",
            (object_id, f"secret/{state}", state),
        )
    connection.commit()
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            report = PostgreSqlAdminViews(session).vault(
                WebActor(uuid4(), owner, uuid4(), AccountRole.OWNER, 0)
            )
            assert report.uploads_quarantined == 3 and report.uploads_failed == 1
            assert report.quarantined_objects == 1 and report.unhealthy_replicas == 3
            assert len(report.issues) == 7 and not report.issues_truncated
            upload_groups = {
                item.error_code: item for item in report.issues if item.scope == "upload"
            }
            assert upload_groups["vault_error_unknown"].count == 2
            assert upload_groups["media_validation_failed"].count == 1
            assert upload_groups["vault_storage_unavailable"].count == 1
            assert "source_authorization_unavailable" not in upload_groups
            assert "secret" not in repr(report) and "private" not in repr(report)
            assert all(item.last_occurred_at is not None for item in report.issues)
    finally:
        engine.dispose()


def test_vault_limits_error_groups_without_truncating_summary_counts(
    database_connection: Connection[object],
    database_url: str,
) -> None:
    connection = database_connection
    owner = _insert_user(connection)
    recording = _insert_recording(connection)
    device = uuid4()
    connection.execute(
        "INSERT INTO account.device (device_id,user_id,device_name,platform,app_version) "
        "VALUES (%s,%s,'Test','ANDROID','test')",
        (device, owner),
    )
    codes = (*VAULT_ERROR_REASONS, "private/unknown")
    for index, code in enumerate(codes):
        connection.execute(
            "INSERT INTO vault.vault_object "
            "(sha256,byte_size,detected_mime_type,commit_status,verification_error) "
            "VALUES (%s,1024,'audio/flac','QUARANTINED',%s)",
            (index.to_bytes(32, "big"), code),
        )
        for state in ("FAILED", "QUARANTINED"):
            upload = _insert_open_session(
                connection,
                user_id=owner,
                device_id=device,
                recording_id=recording,
            )
            job = uuid4()
            connection.execute(
                "INSERT INTO jobs.job (job_id,job_type,schema_version,user_id) "
                "VALUES (%s,'vault.ingest',1,%s)",
                (job, owner),
            )
            connection.execute(
                "UPDATE vault.upload_session SET state=%s, error_code=%s, "
                "job_id=%s, sealed_at=now(), completed_at=now() WHERE upload_session_id=%s",
                (state, code, job, upload),
            )
    connection.commit()
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            report = PostgreSqlAdminViews(session).vault(
                WebActor(uuid4(), owner, uuid4(), AccountRole.OWNER, 0)
            )
            assert report.issues_truncated and len(report.issues) == 50
            assert report.uploads_quarantined == len(codes)
            assert report.uploads_failed == len(codes)
            assert report.quarantined_objects == len(codes)
            assert "private" not in repr(report)
    finally:
        engine.dispose()

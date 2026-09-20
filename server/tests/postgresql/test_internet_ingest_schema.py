"""Internet server uploads have one immutable lineage and cannot use device byte APIs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    InternetAcquisitionRow,
    UploadSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.vault_runtime import PostgresVaultRuntime
from autplay.application.vault_uploads import (
    CreateUploadCommand,
    VaultNotFoundError,
    VaultPrincipal,
)
from autplay.domain.jobs import JobKey
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.ports.jobs import EnqueueJob
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from .conftest import DatabaseHarness
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import internet

__all__ = ["admission"]


def test_internet_upload_lineage_is_unique_immutable_and_excludes_device_api(
    admission: AdmissionHarness,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    actor, claim = internet(admission)
    recording = admission.recording(actor)
    upload_id = uuid4()
    command = CreateUploadCommand(recording, 10, str(upload_id), Sha256Digest(b"d" * 32))
    admission.budget()
    granted = admission.service.acquire_worker(claim)
    with admission.sessions.begin() as session:
        acquisition = present(session.get(InternetAcquisitionRow, claim.acquisition_id))
        acquisition.user_track_ref_id = present(
            session.scalar(
                select(UserTrackRefRow.user_track_ref_id).where(
                    UserTrackRefRow.recording_id == recording,
                    UserTrackRefRow.user_id == actor.user_id,
                )
            )
        )
        job = PostgresJobRepository(session).enqueue(
            EnqueueJob(
                key=JobKey("vault.ingest", 1),
                user_id=actor.user_id,
                payload={"upload_session_id": str(upload_id)},
            )
        )
        session.add(
            UploadSessionRow(
                upload_session_id=upload_id,
                user_id=actor.user_id,
                actor_kind="INTERNET",
                device_id=None,
                source_internet_acquisition_id=claim.acquisition_id,
                target_recording_id=recording,
                idempotency_key=str(upload_id),
                request_hash=command.request_hash,
                declared_sha256=b"d" * 32,
                expected_size=10,
                received_size=10,
                chunk_size=1024,
                max_chunks=1,
                chunk_count=1,
                staging_key=upload_id.hex,
                state="SEALED",
                job_id=job.job_id,
                sealed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.flush()
    # Even an incomplete reverse binding forbids restarting provider byte work.
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(claim, fence(granted), claim.acquisition_id)
    with admission.sessions.begin() as session:
        present(session.get(InternetAcquisitionRow, claim.acquisition_id)).upload_id = upload_id
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(claim, fence(granted), claim.acquisition_id)
    admission.service.release(claim, fence(granted))
    with admission.sessions() as session, pytest.raises(VaultNotFoundError):
        PostgresVaultRuntime(session).get_owned_for_update(
            VaultPrincipal(actor.user_id, actor.device_id), upload_id
        )
    with admission.sessions() as session, pytest.raises(VaultNotFoundError):
        PostgresVaultRuntime(session).create_or_replay(
            VaultPrincipal(actor.user_id, actor.device_id),
            command,
            OpaqueStorageKey("device-replay"),
            datetime.now(UTC) + timedelta(hours=1),
            VaultLimits(),
        )

    upload_changes: dict[str, UUID | str | bytes | int | None] = {
        "source_internet_acquisition_id": uuid4(),
        "actor_kind": "DEVICE",
        "user_id": uuid4(),
        "device_id": actor.device_id,
        "target_recording_id": uuid4(),
        "staging_key": "different-stage",
        "job_id": uuid4(),
        "expected_size": 11,
        "declared_sha256": b"x" * 32,
        "request_hash": b"x" * 32,
        "idempotency_key": "changed-receipt",
    }
    for column, value in upload_changes.items():
        with (
            pytest.raises(IntegrityError, match="Internet upload lineage is immutable"),
            admission.sessions.begin() as session,
        ):
            # Column names come only from this closed fixture map.
            session.execute(
                text(
                    f"UPDATE vault.upload_session SET {column}=:value "
                    "WHERE upload_session_id=:upload"
                ),
                {"value": value, "upload": upload_id},
            )
    acquisition_changes: dict[str, UUID | str | None] = {
        "upload_id": None,
        "user_track_ref_id": uuid4(),
        "device_id": uuid4(),
        "job_id": uuid4(),
        "candidate_id": "replacement",
    }
    for column, value in acquisition_changes.items():
        with (
            pytest.raises(IntegrityError, match="Internet acquisition lineage is immutable"),
            admission.sessions.begin() as session,
        ):
            session.execute(
                text(
                    f"UPDATE discovery.internet_acquisition SET {column}=:value "
                    "WHERE acquisition_id=:acquisition"
                ),
                {"value": value, "acquisition": claim.acquisition_id},
            )
    with (
        pytest.raises(IntegrityError, match="uq_upload_session_source_internet_acquisition"),
        admission.sessions.begin() as session,
    ):
        session.execute(
            text(
                "INSERT INTO vault.upload_session "
                "(upload_session_id,user_id,actor_kind,source_internet_acquisition_id,"
                "target_recording_id,idempotency_key,request_hash,declared_sha256,"
                "expected_size,received_size,chunk_size,max_chunks,chunk_count,staging_key,"
                "state,job_id,sealed_at,expires_at) "
                "SELECT :copy,user_id,actor_kind,source_internet_acquisition_id,"
                "target_recording_id,'copy',request_hash,declared_sha256,expected_size,"
                "received_size,chunk_size,max_chunks,chunk_count,'copy',state,job_id,"
                "sealed_at,expires_at FROM vault.upload_session WHERE upload_session_id=:id"
            ),
            {"copy": uuid4(), "id": upload_id},
        )
    with pytest.raises(DBAPIError, match="Refusing to discard Internet ingest lineage"):
        database_harness.downgrade(database_name, "0038_worker_resource_wait")


def test_device_upload_replay_is_bound_to_the_original_device(admission: AdmissionHarness) -> None:
    actor = admission.actor()
    recording = admission.recording(actor)
    replacement = admission.actor(actor.user_id)
    command = CreateUploadCommand(recording, 100, str(uuid4()))
    arguments = (
        command,
        OpaqueStorageKey("replay"),
        datetime.now(UTC) + timedelta(hours=1),
        VaultLimits(),
    )
    with admission.sessions.begin() as session:
        info, created = PostgresVaultRuntime(session).create_or_replay(
            VaultPrincipal(actor.user_id, actor.device_id), *arguments
        )
        assert created
        upload_id = info.upload_session_id
    with admission.sessions() as session:
        info, created = PostgresVaultRuntime(session).create_or_replay(
            VaultPrincipal(actor.user_id, actor.device_id), *arguments
        )
        assert info.upload_session_id == upload_id and not created
    with admission.sessions() as session, pytest.raises(VaultNotFoundError):
        PostgresVaultRuntime(session).create_or_replay(
            VaultPrincipal(replacement.user_id, replacement.device_id), *arguments
        )

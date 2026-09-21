"""Saved Internet publication authority is independent of a live acquisition worker."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.catalog_changes import PostgresCatalogChangeRepository
from autplay.adapters.postgresql.internet_ingest_authority import (
    lock_internet_ingest_scope,
    require_internet_ingest_authority,
)
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    DeviceRow,
    InternetAcquisitionRow,
    JobRow,
    LibraryEntryRow,
    RecordingRow,
    UploadSessionRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.discovery import DiscoveryError
from autplay.domain.jobs import JobError, JobKey, LeaseTransition, RetryPolicy
from autplay.domain.resource_admission import AcquisitionClaim
from autplay.ports.jobs import EnqueueJob
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .test_resource_admission_runtime import AdmissionHarness, admission, present
from .test_resource_worker_admission import internet

__all__ = ["admission"]

PAYLOAD = b"abcdefghij"


@dataclass(frozen=True)
class PublicationFixture:
    harness: AdmissionHarness
    actor: Principal
    claim: AcquisitionClaim
    upload_id: UUID

    def require(self) -> None:
        with self.harness.sessions.begin() as session:
            lock_resource_admission(session)
            lock_internet_ingest_scope(session, self.upload_id)
            upload = present(session.get(UploadSessionRow, self.upload_id))
            source = require_internet_ingest_authority(session, upload)
            assert source.acquisition_id == self.claim.acquisition_id


@pytest.fixture
def publication(admission: AdmissionHarness) -> Iterator[PublicationFixture]:
    actor, claim = internet(admission)
    recording = admission.recording(actor)
    upload_id = uuid4()
    with admission.sessions.begin() as session:
        source = present(session.get(InternetAcquisitionRow, claim.acquisition_id))
        source.user_track_ref_id = present(
            session.scalar(
                select(UserTrackRefRow.user_track_ref_id).where(
                    UserTrackRefRow.recording_id == recording,
                    UserTrackRefRow.user_id == actor.user_id,
                )
            )
        )
        ingest_job = PostgresJobRepository(session).enqueue(
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
                source_internet_acquisition_id=claim.acquisition_id,
                target_recording_id=recording,
                idempotency_key=str(upload_id),
                request_hash=b"r" * 32,
                declared_sha256=hashlib.sha256(PAYLOAD).digest(),
                expected_size=10,
                received_size=10,
                chunk_size=1024,
                max_chunks=1,
                chunk_count=1,
                staging_key=upload_id.hex,
                state="SEALED",
                job_id=ingest_job.job_id,
                sealed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.flush()
        source.upload_id, source.state = upload_id, "PROCESSING"
    yield PublicationFixture(admission, actor, claim, upload_id)


@pytest.mark.parametrize("state", ["RUNNING", "COMPLETED", "RETRY_WAIT"])
def test_handoff_authority_survives_source_completion_or_retry(
    publication: PublicationFixture, state: str
) -> None:
    with publication.harness.sessions.begin() as session:
        jobs = PostgresJobRepository(session)
        if state == "COMPLETED":
            assert jobs.complete(publication.claim.fence) is LeaseTransition.APPLIED
        elif state == "RETRY_WAIT":
            assert (
                jobs.fail_retryable(
                    publication.claim.fence, JobError("test.lost_handoff_ack", {}), RetryPolicy()
                )
                is LeaseTransition.APPLIED
            )
    publication.require()


@pytest.mark.parametrize(
    "failure",
    [
        "generation",
        "device",
        "family",
        "cancel",
        "job_type",
        "job_payload",
        "removed",
        "identity",
    ],
)
def test_publication_rejects_revoked_or_mismatched_original_authority(
    publication: PublicationFixture, failure: str
) -> None:
    publication.require()
    actor, claim = publication.actor, publication.claim
    with publication.harness.sessions.begin() as session:
        lock_resource_admission(session)
        source = present(session.get(InternetAcquisitionRow, claim.acquisition_id))
        match failure:
            case "generation":
                present(session.get(UserAccountRow, actor.user_id)).authority_generation += 1
            case "device":
                present(session.get(DeviceRow, actor.device_id)).revoked_at = datetime.now(UTC)
            case "family":
                present(session.get(UserSessionRow, actor.session_id)).revoked_at = datetime.now(
                    UTC
                )
            case "cancel":
                present(session.get(JobRow, claim.fence.job_id)).cancel_requested_at = datetime.now(
                    UTC
                )
            case "job_type":
                present(session.get(JobRow, claim.fence.job_id)).job_type = "other.test"
            case "job_payload":
                present(session.get(JobRow, claim.fence.job_id)).payload = {
                    "acquisition_id": str(uuid4())
                }
            case "removed":
                present(
                    session.scalar(
                        select(LibraryEntryRow).where(
                            LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                            LibraryEntryRow.user_id == actor.user_id,
                        )
                    )
                ).removed_at = datetime.now(UTC)
            case "identity":
                redirect(session, publication)
    with pytest.raises(DiscoveryError, match="source_authorization_unavailable"):
        publication.require()


@pytest.mark.parametrize("same_family", [True, False])
def test_new_session_only_preserves_the_original_family(
    publication: PublicationFixture, same_family: bool
) -> None:
    actor = publication.actor
    next_session = uuid4()
    with publication.harness.sessions.begin() as session:
        lock_resource_admission(session)
        previous = present(session.get(UserSessionRow, actor.session_id))
        previous.revoked_at = datetime.now(UTC)
        session.add(
            UserSessionRow(
                session_id=next_session,
                user_id=actor.user_id,
                device_id=actor.device_id,
                refresh_token_hash=hashlib.sha256(next_session.bytes).digest(),
                issued_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=30),
                session_mode="V2",
                family_id=previous.family_id if same_family else next_session,
                generation=present(previous.generation) + 1,
            )
        )
    if same_family:
        publication.require()
    else:
        with pytest.raises(DiscoveryError, match="source_authorization_unavailable"):
            publication.require()


def redirect(session: Session, publication: PublicationFixture) -> None:
    """Use the real catalog decision path; never rewrite match-decision projections."""
    actor = publication.actor
    present(session.get(UserAccountRow, actor.user_id)).role = "ADMIN"
    admin = Principal(actor.user_id, actor.device_id, actor.session_id, AccountRole.ADMIN)
    upload = present(session.get(UploadSessionRow, publication.upload_id))
    original = present(session.get(RecordingRow, upload.target_recording_id))
    target = RecordingRow(
        artist_credit_id=original.artist_credit_id,
        title="Reviewed destination",
        normalized_title="reviewed destination",
    )
    session.add(target)
    session.flush()
    repository = PostgresCatalogChangeRepository(session)
    change = repository.propose_recording_change(
        principal=admin,
        operation_type="MERGE",
        source_recording_id=original.recording_id,
        target_recording_id=target.recording_id,
        reason="Synthetic publication race",
        now=datetime.now(UTC),
    )
    session.flush()
    repository.apply(principal=admin, change_set_id=change.change_set_id, now=datetime.now(UTC))
    session.flush()


@pytest.mark.parametrize("change", ["redirect", "remove"])
@pytest.mark.parametrize("writer_first", [True, False])
def test_direct_catalog_and_library_writes_are_serialized_with_publication_guard(
    publication: PublicationFixture, change: str, writer_first: bool
) -> None:
    harness = publication.harness

    def change_target(session: Session) -> None:
        if change == "redirect":
            redirect(session, publication)
        else:
            source = present(session.get(InternetAcquisitionRow, publication.claim.acquisition_id))
            entry = present(
                session.scalar(
                    select(LibraryEntryRow).where(
                        LibraryEntryRow.user_track_ref_id == source.user_track_ref_id,
                        LibraryEntryRow.user_id == source.user_id,
                    )
                )
            )
            entry.removed_at = datetime.now(UTC)
            session.flush()

    def write() -> None:
        with harness.sessions.begin() as session:
            change_target(session)

    def rejected_guard() -> None:
        with pytest.raises(DiscoveryError, match="source_authorization_unavailable"):
            publication.require()

    def wait_for_blocker(blocker: int, pending: Future[None]) -> None:
        deadline = time.monotonic() + 5
        with harness.sessions() as observer:
            while not observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                ),
                {"blocker": blocker},
            ):
                assert not pending.done(), "guard and mutation did not serialize"
                assert time.monotonic() < deadline, "expected row lock was not observed"
                time.sleep(0.01)

    with harness.sessions() as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker_id = present(blocker.scalar(select(func.pg_backend_pid())))
        if writer_first:
            change_target(blocker)
            pending = executor.submit(rejected_guard)
        else:
            lock_resource_admission(blocker)
            lock_internet_ingest_scope(blocker, publication.upload_id)
            require_internet_ingest_authority(
                blocker, present(blocker.get(UploadSessionRow, publication.upload_id))
            )
            pending = executor.submit(write)
        try:
            wait_for_blocker(blocker_id, pending)
        except BaseException:
            blocker.rollback()
            raise
        blocker.commit()
        pending.result(timeout=10)
    with pytest.raises(DiscoveryError, match="source_authorization_unavailable"):
        publication.require()

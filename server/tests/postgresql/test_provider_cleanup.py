"""Cleanup claims serialize with handoff, survive accounting GC, and release DB locks before FS."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql import discovery_runtime
from autplay.adapters.postgresql.discovery_runtime import (
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import (
    AcquisitionAttemptRow,
    InternetAcquisitionRow,
    JobRow,
    ProviderStagingRow,
    SourceAuthorizationRow,
    UploadSessionRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.provider_cleanup import PostgresProviderCleanupRepository
from autplay.adapters.postgresql.resource_limits import RESOURCE_ADMISSION_LOCK
from autplay.application.provider_cleanup import ProviderCleanupClaim, ProviderCleanupService
from autplay.domain.jobs import JobKey, TerminalJobError
from autplay.domain.resource_admission import AcquisitionClaim, ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest, VaultLimits, VerifiedStagedFile
from sqlalchemy import func, select, text

from .test_discovery_runtime import _seed_import, _track
from .test_internet_handoff import ready
from .test_provider_staging import Writer, writer
from .test_resource_admission_runtime import AdmissionHarness, admission, fence, present
from .test_resource_worker_admission import successor

__all__ = ["admission"]


def close(harness: AdmissionHarness, owned: Writer) -> None:
    harness.service.confirm_execution_exit(owned.ticket, owned.proof)
    harness.service.close_io(owned.ticket.permit)


@pytest.mark.parametrize("job_state", ["RUNNING", "EXPIRED", "RETRY_WAIT", "PAUSED"])
def test_live_intent_keeps_successful_bytes_without_current_lease(
    admission: AdmissionHarness, job_state: str
) -> None:
    owned = writer(admission)
    close(admission, owned)
    with admission.sessions.begin() as session:
        job = present(session.get(JobRow, owned.claim.fence.job_id))
        if job_state == "EXPIRED":
            job.lease_deadline = datetime.now(UTC) - timedelta(seconds=1)
        elif job_state != "RUNNING":
            job.state = job_state
            job.lease_owner = job.lease_deadline = job.heartbeat_at = None
    assert (
        PostgresProviderCleanupRepository(admission.sessions).claim(owned.ticket.execution_id)
        is None
    )


@pytest.mark.parametrize(
    ("cause", "reason"),
    [
        ("cancel", "CANCELLED"),
        ("failed", "FAILED"),
        ("exit", "FAILED"),
        ("never_started", "FAILED"),
        ("generation", "AUTHORITY_REVOKED"),
        ("session", "AUTHORITY_REVOKED"),
        ("successor", "SUPERSEDED"),
        ("completed", "SUPERSEDED"),
    ],
)
def test_only_explicit_abandonment_claims_exact_exited_receipt(
    admission: AdmissionHarness, cause: str, reason: str
) -> None:
    owned = writer(admission, started=cause != "never_started")
    if cause == "exit":
        owned = replace(owned, proof=replace(owned.proof, exit_code=1))
    repository = PostgresProviderCleanupRepository(admission.sessions)
    assert repository.claim(owned.ticket.execution_id) is None
    close(admission, owned)
    with admission.sessions.begin() as session:
        source = present(session.get(InternetAcquisitionRow, owned.claim.acquisition_id))
        job = present(session.get(JobRow, owned.claim.fence.job_id))
        if cause == "cancel":
            job.cancel_requested_at = datetime.now(UTC)
        elif cause in {"failed", "completed"}:
            job.state = cause.upper()
            job.lease_owner = job.lease_deadline = job.heartbeat_at = None
        elif cause == "generation":
            present(session.get(UserAccountRow, source.user_id)).authority_generation += 1
        elif cause == "session":
            session.execute(
                text(
                    "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE user_id=:u"
                ),
                {"u": source.user_id},
            )
    if cause == "successor":
        successor(admission, owned.claim)
    claim = present(repository.claim(owned.ticket.execution_id))
    assert repository.claim(owned.ticket.execution_id) == claim
    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert row.state == "CLEANUP_CLAIMED" and row.cleanup_reason == reason
        assert row.closure_evidence_sha256 == owned.proof.evidence_sha256


def test_handoff_winner_protects_file_even_after_authority_revocation(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    handoff, owned, target, verified = ready(admission, tmp_path)
    receipt = handoff.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    with admission.sessions.begin() as session:
        present(session.get(UserAccountRow, target.user_id)).authority_generation += 1
    assert PostgresProviderCleanupRepository(admission.sessions).claim(receipt.execution_id) is None
    assert (tmp_path / "staging" / owned.key.value).exists()


def test_cleanup_claim_prevents_handoff_after_same_family_becomes_live_again(
    admission: AdmissionHarness, tmp_path: Path
) -> None:
    handoff, owned, target, verified = ready(admission, tmp_path)
    with admission.sessions.begin() as session:
        current = present(
            session.scalar(select(UserSessionRow).where(UserSessionRow.user_id == target.user_id))
        )
        # Simulate expiry followed by a legitimate renewal of the same family.
        current.issued_at = datetime.now(UTC) - timedelta(days=2)
        current.expires_at = datetime.now(UTC) - timedelta(days=1)
        session_id = current.session_id
    repository = PostgresProviderCleanupRepository(admission.sessions)
    assert repository.claim(owned.ticket.execution_id) is not None
    with admission.sessions.begin() as session:
        present(session.get(UserSessionRow, session_id)).expires_at = datetime.now(UTC) + timedelta(
            days=1
        )
    with pytest.raises(TerminalJobError, match="music_staging_unavailable"):
        handoff.handoff(owned.claim, target, owned.ticket.execution_id, verified)
    with admission.sessions() as session:
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
        assert (
            present(session.get(ProviderStagingRow, owned.ticket.execution_id)).state
            == "CLEANUP_CLAIMED"
        )


def test_filesystem_runs_after_commit_and_completion_failure_replays(
    admission: AdmissionHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = writer(admission, started=False)
    close(admission, owned)
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    scratch = storage.create_workspace(owned.ticket.execution_id)
    (scratch / "download.part").write_bytes(b"partial bytes")
    (tmp_path / "staging" / owned.key.value).write_bytes(b"final bytes")
    repository = PostgresProviderCleanupRepository(admission.sessions)
    service = ProviderCleanupService(repository, storage)
    retire = storage.retire

    def check_unlocked(claim: ProviderCleanupClaim) -> None:
        with admission.sessions.begin() as session:
            assert session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RESOURCE_ADMISSION_LOCK}
            )
            row = present(
                session.scalar(
                    select(ProviderStagingRow)
                    .where(ProviderStagingRow.execution_id == claim.execution_id)
                    .with_for_update(nowait=True)
                )
            )
            assert row.state == "CLEANUP_CLAIMED"
        retire(claim)

    def fail_complete(claim: ProviderCleanupClaim) -> None:
        raise RuntimeError("test.commit_unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "retire", check_unlocked)
        patch.setattr(repository, "complete", fail_complete)
        with pytest.raises(RuntimeError, match=r"test\.commit_unavailable"):
            service.cleanup(owned.ticket.execution_id)
    claim = present(repository.claim(owned.ticket.execution_id))
    assert not claim.completed and not scratch.exists()
    assert service.cleanup(owned.ticket.execution_id)
    assert present(repository.claim(owned.ticket.execution_id)).completed
    with monkeypatch.context() as patch:
        patch.setattr(storage, "retire", fail_complete)
        assert service.cleanup(owned.ticket.execution_id)
    assert (
        tmp_path / "provider-retired" / claim.claim_id.hex / "download.part"
    ).read_bytes() == b"partial bytes"


def a1_writer(harness: AdmissionHarness) -> Writer:
    harness.budget()
    with harness.sessions.begin() as session:
        owner, _ = _seed_import(session, "Provider cleanup A1")
        PostgresBulkDiscoveryRepository(session).start_search_acquisition(
            owner_user_id=owner, operation_id=uuid4(), evidence=_track()
        )
        attempt = present(session.scalar(select(AcquisitionAttemptRow)))
        acquisition_id = attempt.acquisition_attempt_id
    with harness.sessions.begin() as session:
        job = PostgresJobRepository(session).claim(
            worker_id="cleanup-a1",
            supported=(JobKey("discovery.acquire", 1),),
            lease_interval=timedelta(minutes=1),
            limit=1,
        )[0]
    claim = AcquisitionClaim(job.fence, "DISCOVERY_ACQUISITION", acquisition_id)
    permit = harness.service.open_io(
        claim, fence(harness.service.acquire_worker(claim)), acquisition_id
    )
    ticket = ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, acquisition_id)
    harness.service.prepare_execution(claim, ticket)
    # The same receipt identity shape as real startup, without launching a provider.
    child = ProcessIdentity(34568, b"a" * 32)
    harness.service.start_execution(claim, ticket, child)
    return Writer(
        claim,
        ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"x" * 32, 0, child),
        OpaqueStorageKey(f"provider-{ticket.execution_id.hex}"),
    )


@pytest.mark.parametrize("cause", ["none", "source", "generation"])
def test_a1_cleanup_uses_durable_original_source_authority(
    admission: AdmissionHarness, cause: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = a1_writer(admission)
    close(admission, owned)
    repository = PostgresProviderCleanupRepository(admission.sessions)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cleanup must not consult transient provider/operator gates")

    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "_require_provider", forbidden)
    monkeypatch.setattr(PostgresBulkDiscoveryRepository, "_require_operator_gate", forbidden)
    assert repository.claim(owned.ticket.execution_id) is None
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, owned.claim.acquisition_id))
        if cause == "source":
            present(
                session.scalar(
                    select(SourceAuthorizationRow).where(
                        SourceAuthorizationRow.authorization_id == attempt.source_authorization_id,
                        SourceAuthorizationRow.revision == attempt.source_authorization_revision,
                    )
                )
            ).revoked_at = datetime.now(UTC)
        elif cause == "generation":
            staging = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
            present(session.get(UserAccountRow, staging.user_id)).authority_generation += 1
    claim = repository.claim(owned.ticket.execution_id)
    assert (claim is None) == (cause == "none")


def test_legacy_a1_cannot_bind_provider_namespace_after_cleanup_claim(
    admission: AdmissionHarness,
) -> None:
    owned = a1_writer(admission)
    close(admission, replace(owned, proof=replace(owned.proof, exit_code=1)))
    assert (
        PostgresProviderCleanupRepository(admission.sessions).claim(owned.ticket.execution_id)
        is not None
    )
    with admission.sessions.begin() as session:
        attempt = present(session.get(AcquisitionAttemptRow, owned.claim.acquisition_id))
        staging = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        before = session.scalar(select(func.count()).select_from(JobRow))
        with pytest.raises(BulkDiscoveryError, match="source_authorization_unavailable"):
            PostgresBulkDiscoveryRepository(session).prepare_ingest(
                candidate_id=attempt.candidate_id,
                owner_user_id=staging.user_id,
                fence=owned.claim.fence,
                evidence=_track(),
                staging_key=owned.key,
                verified=VerifiedStagedFile(10, Sha256Digest(b"d" * 32)),
                limits=VaultLimits(),
            )
        assert session.scalar(select(func.count()).select_from(UploadSessionRow)) == 0
        assert session.scalar(select(func.count()).select_from(JobRow)) == before


def test_a1_cleanup_does_not_treat_fast_application_clock_as_source_expiry(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = a1_writer(admission)
    close(admission, owned)

    class FastClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FastClock:
            return cls.fromtimestamp(
                datetime.now(tz).timestamp() + timedelta(days=3650).total_seconds(), tz
            )

    monkeypatch.setattr(discovery_runtime, "datetime", FastClock)
    assert (
        PostgresProviderCleanupRepository(admission.sessions).claim(owned.ticket.execution_id)
        is None
    )


@pytest.mark.parametrize("a1", [False, True])
def test_clock_failure_cannot_be_recorded_as_revocation(
    admission: AdmissionHarness, monkeypatch: pytest.MonkeyPatch, a1: bool
) -> None:
    owned = a1_writer(admission) if a1 else writer(admission)
    close(admission, owned)
    repository = PostgresProviderCleanupRepository(admission.sessions)

    def unavailable(*args: object) -> datetime:
        raise ResourceAdmissionError("provider_cleanup_unavailable")

    monkeypatch.setattr(repository, "_now", unavailable)
    with pytest.raises(ResourceAdmissionError, match="provider_cleanup_unavailable"):
        repository.claim(owned.ticket.execution_id)
    with admission.sessions() as session:
        row = present(session.get(ProviderStagingRow, owned.ticket.execution_id))
        assert row.state == "EXITED" and row.cleanup_claim_id is None

"""Fenced P06 ingest job handler and explicit crash-recovery checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from autplay.application.job_worker import JobExecutionContext
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    ImmutableObjectConflictError,
    MediaValidationError,
    OpaqueStorageKey,
    StagedFileNotFoundError,
    StorageOperationError,
    StorageSafetyError,
    VerifiedStagedFile,
)
from autplay.ports.vault import FingerprintGenerator, MediaInspector, VaultStorage


@dataclass(frozen=True, slots=True)
class IngestSession:
    """Minimal sealed upload state needed by a fenced ingest attempt."""

    upload_session_id: UUID
    target_recording_id: UUID
    staging_key: OpaqueStorageKey
    expected_size: int
    declared_sha256: bytes | None


class IngestRepository(Protocol):
    """Persistence transitions whose database implementation is lease fenced."""

    def start_ingest(self, upload_session_id: UUID, job_id: UUID) -> IngestSession | None: ...
    def prepare_commit(
        self,
        session: IngestSession,
        verified: VerifiedStagedFile,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
    ) -> str: ...
    def finalize_published(
        self,
        session: IngestSession,
        storage_key: OpaqueStorageKey,
        metadata: AudioTechnicalMetadata,
        evidence: ChromaprintEvidence,
        *,
        reused: bool,
    ) -> bool: ...
    def quarantine(self, session: IngestSession, code: str) -> None: ...


class VaultIngestHandler:
    """Validate, fingerprint, CAS-publish, then finalize one upload exactly once."""

    def __init__(
        self,
        *,
        repository: IngestRepository,
        storage: VaultStorage,
        media: MediaInspector,
        fingerprints: FingerprintGenerator,
        minimum_free_bytes: int = 0,
    ) -> None:
        if minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must not be negative")
        self._repository = repository
        self._storage = storage
        self._media = media
        self._fingerprints = fingerprints
        self._minimum_free_bytes = minimum_free_bytes

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        raw_id = lease.payload.get("upload_session_id")
        if not isinstance(raw_id, str):
            raise TerminalJobError("vault.invalid_job_payload")
        try:
            upload_id = UUID(raw_id)
        except ValueError as error:
            raise TerminalJobError("vault.invalid_job_payload") from error
        try:
            if self._storage.available_bytes() < self._minimum_free_bytes:
                raise RetryableJobError("vault.capacity_low")
        except StorageOperationError as error:
            raise RetryableJobError(error.code) from error
        except StorageSafetyError as error:
            raise TerminalJobError(error.code) from error
        session = self._repository.start_ingest(upload_id, lease.fence.job_id)
        if session is None:
            return
        try:
            context.checkpoint({"stage": "VALIDATED"})
            verified = self._storage.verify_staging(session.staging_key)
            if verified.byte_size != session.expected_size or (
                session.declared_sha256 is not None
                and verified.sha256.value != session.declared_sha256
            ):
                self._repository.quarantine(session, "vault.integrity_mismatch")
                raise TerminalJobError("vault.integrity_mismatch")
            safe_path = self._storage.staging_path_for_media(session.staging_key)
            metadata = self._media.inspect(safe_path)
            evidence = self._fingerprints.fingerprint(safe_path)
            action = self._repository.prepare_commit(session, verified, metadata, evidence)
            context.checkpoint({"stage": "DB_PREPARED"})
            if action == "WAIT":
                raise RetryableJobError("vault.sha_commit_pending")
            if action not in {"PUBLISH", "REUSED"}:
                self._repository.quarantine(session, "vault.integrity_conflict")
                raise TerminalJobError("vault.integrity_conflict")
            if action == "REUSED":
                finalized = self._repository.finalize_published(
                    session,
                    OpaqueStorageKey(verified.sha256.hex),
                    metadata,
                    evidence,
                    reused=True,
                )
            else:
                committed = self._storage.commit_staging(session.staging_key, verified)
                context.checkpoint({"stage": "FILE_PUBLISHED"})
                finalized = self._repository.finalize_published(
                    session, committed.storage_key, metadata, evidence, reused=False
                )
            if not finalized:
                raise TerminalJobError("vault.integrity_conflict")
            context.checkpoint({"stage": "DB_FINALIZED"})
            self._cleanup_after_finalization(context, session.staging_key)
        except MediaValidationError as error:
            self._repository.quarantine(session, error.code)
            raise TerminalJobError(error.code) from error
        except (StagedFileNotFoundError, StorageSafetyError, ImmutableObjectConflictError) as error:
            self._repository.quarantine(session, error.code)
            raise TerminalJobError(error.code) from error
        except StorageOperationError as error:
            raise RetryableJobError(error.code) from error

    def _cleanup_after_finalization(
        self, context: JobExecutionContext, staging_key: OpaqueStorageKey
    ) -> None:
        try:
            self._storage.cleanup_staging(staging_key)
        except StagedFileNotFoundError:
            pass
        except StorageOperationError, StorageSafetyError:
            context.checkpoint({"stage": "CLEANUP_DEFERRED"})
            return
        context.checkpoint({"stage": "CLEANED"})


__all__ = ("IngestRepository", "IngestSession", "VaultIngestHandler")

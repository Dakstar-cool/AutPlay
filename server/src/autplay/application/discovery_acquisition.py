"""Fenced A1B provider acquisition followed by canonical Vault ingest enqueue."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.discovery_runtime import (
    BulkDiscoveryError,
    PostgresBulkDiscoveryRepository,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.application.manual_discovery import ManualDiscoveryService
from autplay.domain.discovery import DiscoveryError
from autplay.domain.jobs import JobLease, RetryableJobError, TerminalJobError
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
    UploadLimitError,
    VaultLimits,
)
from autplay.ports.vault import VaultStorage

_TERMINAL_DISCOVERY_ERRORS = frozenset(
    {
        "candidate_not_selectable",
        "discovery_content_invalid",
        "discovery_not_eligible",
        "discovery_operation_conflict",
        "discovery_provider_response_invalid",
        "discovery_target_not_found",
        "identity_review_required",
        "source_authorization_unavailable",
    }
)


class DiscoveryAcquisitionHandler:
    """Download one explicit candidate, copy to Vault staging, and enqueue ingest."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        *,
        discovery: ManualDiscoveryService,
        storage: VaultStorage,
        limits: VaultLimits,
    ) -> None:
        self._sessions = sessions
        self._discovery = discovery
        self._storage = storage
        self._limits = limits

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        candidate_id = _candidate_id(lease)
        owner_id = lease.user_id
        if owner_id is None:
            raise TerminalJobError("discovery.invalid_job_payload")
        try:
            with self._sessions() as session:
                target = PostgresBulkDiscoveryRepository(session).claim_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                )
                session.commit()
            if target is None:
                return
            context.checkpoint({"stage": "ACQUIRING"})
            staged = self._discovery.acquire(
                owner_id,
                target.provider_track_id,
                operation_id=candidate_id,
            )
            context.raise_if_cancelled()
            source = self._discovery.staged_audio_path(owner_id, staged)
            staging_key = OpaqueStorageKey(f"disc-{candidate_id.hex}")
            self._copy_to_vault(source, staging_key)
            verified = self._storage.verify_staging(staging_key)
            if verified.byte_size != staged.byte_count:
                raise StorageSafetyError()
            context.checkpoint({"stage": "VAULT_STAGED"})
            candidate = self._discovery.lookup_for_acquisition(target.provider_track_id)
            context.raise_if_cancelled()
            with self._sessions() as session:
                PostgresBulkDiscoveryRepository(session).prepare_ingest(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                    evidence=candidate,
                    staging_key=staging_key,
                    verified=verified,
                    limits=self._limits,
                )
                session.commit()
            context.checkpoint({"stage": "INGEST_QUEUED"})
        except (DiscoveryError, BulkDiscoveryError) as error:
            code = error.code
            terminal = code in _TERMINAL_DISCOVERY_ERRORS
            self._record_failure(candidate_id, owner_id, lease, code, terminal=terminal)
            if terminal:
                raise TerminalJobError(code) from None
            raise RetryableJobError(code) from None
        except (StorageSafetyError, UploadLimitError, ImmutableObjectConflictError) as error:
            self._record_failure(candidate_id, owner_id, lease, error.code, terminal=True)
            raise TerminalJobError(error.code) from None
        except StorageOperationError as error:
            self._record_failure(candidate_id, owner_id, lease, error.code, terminal=False)
            raise RetryableJobError(error.code) from None
        except SQLAlchemyError as error:
            raise RetryableJobError("database_unavailable") from error

    def _copy_to_vault(self, source: Path, staging_key: OpaqueStorageKey) -> None:
        try:
            self._storage.create_staging(staging_key)
        except ImmutableObjectConflictError:
            self._storage.truncate_staging(staging_key, 0)
        offset = 0
        try:
            with source.open("rb") as stream:
                while payload := stream.read(self._limits.max_chunk_bytes):
                    result = self._storage.write_chunk(
                        staging_key,
                        offset=offset,
                        payload=payload,
                        payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
                    )
                    offset = result.next_offset
                    if offset > self._limits.max_object_bytes:
                        raise UploadLimitError()
        except OSError as error:
            raise StorageOperationError() from error
        if offset < 1:
            raise StorageSafetyError()

    def _record_failure(
        self,
        candidate_id: UUID,
        owner_id: UUID,
        lease: JobLease,
        code: str,
        *,
        terminal: bool,
    ) -> None:
        try:
            with self._sessions() as session:
                PostgresBulkDiscoveryRepository(session).fail_acquisition(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                    error_code=code,
                    terminal=terminal,
                )
                session.commit()
        except BulkDiscoveryError as error:
            if error.code != "lease_fence_lost":
                raise


class StandardAnalysisHandler:
    """Finalize the durable baseline analysis evidence produced by Vault ingest."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        candidate_id = _candidate_id(lease)
        owner_id = lease.user_id
        if owner_id is None:
            raise TerminalJobError("discovery.invalid_job_payload")
        try:
            with self._sessions() as session:
                claimed = PostgresBulkDiscoveryRepository(session).claim_analysis(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                )
                session.commit()
            if not claimed:
                return
            context.checkpoint({"stage": "STANDARD_ANALYSIS_RUNNING"})
            with self._sessions() as session:
                complete = PostgresBulkDiscoveryRepository(session).complete_analysis(
                    candidate_id=candidate_id,
                    owner_user_id=owner_id,
                    fence=lease.fence,
                )
                session.commit()
            if not complete:
                raise TerminalJobError("discovery_analysis_evidence_invalid")
            context.checkpoint({"stage": "STANDARD_ANALYSIS_COMPLETE"})
        except BulkDiscoveryError as error:
            raise TerminalJobError(error.code) from error
        except SQLAlchemyError as error:
            raise RetryableJobError("database_unavailable") from error


def _candidate_id(lease: JobLease) -> UUID:
    raw = lease.payload.get("candidate_id")
    if not isinstance(raw, str):
        raise TerminalJobError("discovery.invalid_job_payload")
    try:
        return UUID(raw)
    except ValueError as error:
        raise TerminalJobError("discovery.invalid_job_payload") from error


__all__ = ("DiscoveryAcquisitionHandler", "StandardAnalysisHandler")

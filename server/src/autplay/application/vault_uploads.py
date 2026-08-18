"""Owner-scoped resumable-upload use cases for the P06 Vault boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from autplay.domain.vault import ChunkWriteResult, OpaqueStorageKey, Sha256Digest, VaultLimits
from autplay.ports.vault import VaultStorage


class VaultNotFoundError(RuntimeError):
    """Indistinguishable owner/resource denial used at the HTTP boundary."""

    code = "vault_resource_not_found"


class UploadConflictError(RuntimeError):
    """A stable conflict which does not reveal another owner's state."""

    code = "upload_idempotency_conflict"


class UploadStateError(RuntimeError):
    """The request is not permitted in the upload's durable state."""

    code = "upload_invalid_state"


class VaultCapacityError(RuntimeError):
    """The configured safety reserve would be violated by a new upload."""

    code = "vault_capacity_low"


@dataclass(frozen=True, slots=True)
class VaultPrincipal:
    """Authenticated user and the active device which owns an operation."""

    user_id: UUID
    device_id: UUID


@dataclass(frozen=True, slots=True)
class CreateUploadCommand:
    """Bounded, hashable upload intent; bytes are appended separately."""

    recording_id: UUID
    expected_size: int
    idempotency_key: str
    declared_sha256: Sha256Digest | None = None

    def __post_init__(self) -> None:
        if self.expected_size < 1:
            raise ValueError("expected_size must be positive")
        if not 1 <= len(self.idempotency_key) <= 200:
            raise ValueError("idempotency_key length must be between one and 200")

    @property
    def request_hash(self) -> bytes:
        """Return a non-secret canonical request fingerprint."""

        declared = "" if self.declared_sha256 is None else self.declared_sha256.hex
        document = f"{self.recording_id}:{self.expected_size}:{declared}"
        return hashlib.sha256(document.encode("ascii")).digest()


@dataclass(frozen=True, slots=True)
class UploadInfo:
    """Redacted owner-facing upload progress state."""

    upload_session_id: UUID
    expected_size: int
    received_size: int
    state: str
    expires_at: datetime
    chunk_size: int
    job_id: UUID | None = None


class UploadRepository(Protocol):
    """Transactional persistence required by :class:`VaultUploadService`."""

    def authorize_target(self, principal: VaultPrincipal, recording_id: UUID) -> bool: ...
    def create_or_replay(
        self,
        principal: VaultPrincipal,
        command: CreateUploadCommand,
        staging_key: OpaqueStorageKey,
        expires_at: datetime,
        limits: VaultLimits,
    ) -> tuple[UploadInfo, bool]: ...
    def get_owned_for_update(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> UploadInfo: ...
    def record_chunk(
        self,
        principal: VaultPrincipal,
        upload_session_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        byte_size: int,
        sha256: Sha256Digest,
    ) -> ChunkWriteResult: ...
    def staging_key_for_owned(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> OpaqueStorageKey: ...
    def seal_and_enqueue(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> UploadInfo: ...
    def expire_open(
        self, principal: VaultPrincipal, upload_session_id: UUID
    ) -> tuple[UploadInfo, OpaqueStorageKey]: ...
    def cancel(self, principal: VaultPrincipal, upload_session_id: UUID) -> None: ...


class VaultUploadService:
    """Coordinates durable staging bytes with owner-scoped receipt rows.

    The storage write is intentionally performed before the receipt commit. A
    retry reconciles any uncommitted suffix via ``truncate_staging`` supplied by
    the P06 filesystem port before appending further bytes.
    """

    def __init__(
        self,
        *,
        repository: UploadRepository,
        storage: VaultStorage,
        limits: VaultLimits | None = None,
        ttl: timedelta = timedelta(hours=24),
        minimum_free_bytes: int = 0,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        if minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must not be negative")
        self._repository = repository
        self._storage = storage
        self._limits = limits or VaultLimits()
        self._ttl = ttl
        self._minimum_free_bytes = minimum_free_bytes

    def create(
        self,
        principal: VaultPrincipal,
        command: CreateUploadCommand,
        *,
        now: datetime,
        staging_key: OpaqueStorageKey,
    ) -> tuple[UploadInfo, bool]:
        """Create/replay an authorized session and its private staging file."""

        if command.expected_size > self._limits.max_object_bytes:
            raise ValueError("expected_size exceeds vault limit")
        if self._storage.available_bytes() - command.expected_size < self._minimum_free_bytes:
            raise VaultCapacityError()
        if not self._repository.authorize_target(principal, command.recording_id):
            raise VaultNotFoundError()
        info, created = self._repository.create_or_replay(
            principal, command, staging_key, now + self._ttl, self._limits
        )
        if created:
            try:
                self._storage.create_staging(staging_key)
            except Exception:
                # The DB session is deliberately left OPEN for reconciliation;
                # callers receive a stable storage failure from the adapter.
                raise
        return info, created

    def append(
        self,
        principal: VaultPrincipal,
        upload_session_id: UUID,
        *,
        offset: int,
        chunk_index: int,
        payload: bytes,
        payload_sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        """Durably append bytes, then persist exactly one matching receipt."""

        info = self._repository.get_owned_for_update(principal, upload_session_id)
        if info.state != "OPEN":
            raise UploadStateError()
        if offset != info.received_size:
            # A duplicate retry is still delegated to the storage implementation
            # after the database repository verifies the matching chunk receipt.
            return self._repository.record_chunk(
                principal,
                upload_session_id,
                offset=offset,
                chunk_index=chunk_index,
                byte_size=len(payload),
                sha256=payload_sha256,
            )
        if len(payload) > info.chunk_size or info.received_size + len(payload) > info.expected_size:
            raise UploadStateError()
        key = self._repository.staging_key_for_owned(principal, upload_session_id)
        self._storage.truncate_staging(key, info.received_size)
        self._storage.write_chunk(
            key, offset=offset, payload=payload, payload_sha256=payload_sha256
        )
        return self._repository.record_chunk(
            principal,
            upload_session_id,
            offset=offset,
            chunk_index=chunk_index,
            byte_size=len(payload),
            sha256=payload_sha256,
        )

    def complete(self, principal: VaultPrincipal, upload_session_id: UUID) -> UploadInfo:
        """Seal a fully received upload and enqueue the unique ingest job."""

        info = self._repository.get_owned_for_update(principal, upload_session_id)
        if info.state in {"SEALED", "PROCESSING", "COMMIT_PREPARED", "COMMITTED", "REUSED"}:
            return info
        if info.state != "OPEN" or info.received_size != info.expected_size:
            raise UploadStateError()
        if self._storage.available_bytes() < self._minimum_free_bytes:
            raise VaultCapacityError()
        return self._repository.seal_and_enqueue(principal, upload_session_id)

    def expire_if_due(
        self, principal: VaultPrincipal, upload_session_id: UUID, *, now: datetime
    ) -> tuple[UploadInfo, OpaqueStorageKey] | None:
        """Persist an OPEN session's terminal expiry before callers report a conflict."""

        info = self._repository.get_owned_for_update(principal, upload_session_id)
        if info.state == "OPEN" and info.expires_at <= now:
            return self._repository.expire_open(principal, upload_session_id)
        return None

    def status(self, principal: VaultPrincipal, upload_session_id: UUID) -> UploadInfo:
        """Return owner-scoped state without exposing cross-owner existence."""

        return self._repository.get_owned_for_update(principal, upload_session_id)

    def cancel(self, principal: VaultPrincipal, upload_session_id: UUID) -> None:
        """Cancel only a still-owned, non-terminal upload."""

        self._repository.cancel(principal, upload_session_id)


__all__ = (
    "CreateUploadCommand",
    "UploadConflictError",
    "UploadInfo",
    "UploadRepository",
    "UploadStateError",
    "VaultCapacityError",
    "VaultNotFoundError",
    "VaultPrincipal",
    "VaultUploadService",
)

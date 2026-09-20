"""Durable cleanup of terminal device uploads; completion is not an integrity claim."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.vault import OpaqueStorageKey


@dataclass(frozen=True, slots=True)
class UploadCleanupClaim:
    claim_id: UUID  # The immutable upload session identity.
    storage_key: OpaqueStorageKey
    completed: bool = False

    @property
    def quarantine_key(self) -> OpaqueStorageKey:
        return OpaqueStorageKey(f"upload-cleanup-{self.claim_id.hex}")


class UploadCleanupRepository(Protocol):
    def claim(self, upload_id: UUID) -> UploadCleanupClaim | None: ...
    def pending(self, *, maximum: int, after: UUID | None = None) -> tuple[UUID, ...]: ...
    def complete(self, claim: UploadCleanupClaim, execution_id: UUID) -> None: ...


class UploadCleanupStorage(Protocol):
    def retire_upload(self, claim: UploadCleanupClaim) -> UUID: ...
    def pending(self) -> tuple[UUID, ...]: ...
    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]: ...


@dataclass(frozen=True, slots=True)
class UploadCleanupReport:
    completed: int
    deferred: int
    pending: bool


class UploadCleanupService:
    def __init__(self, repository: UploadCleanupRepository, storage: UploadCleanupStorage) -> None:
        self._repository, self._storage = repository, storage

    def cleanup(self, upload_id: UUID) -> bool:
        claim = self._repository.claim(upload_id)
        if claim is None:
            return False
        if not claim.completed:
            execution = self._storage.retire_upload(claim)
            self._repository.complete(claim, execution)
        return True

    def run(self, *, limit: int = 100) -> UploadCleanupReport:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("upload_cleanup_limit_invalid")
        completed = deferred = 0
        after = None
        try:
            while page := self._repository.pending(maximum=limit, after=after):
                for upload_id in page:
                    try:
                        success = self.cleanup(upload_id)
                    except ResourceAdmissionError as error:
                        if error.code != "maintenance_storage_failed" or self._storage.pending():
                            raise
                        success = False
                    completed += int(success)
                    deferred += int(not success)
                    after = upload_id
            return UploadCleanupReport(
                completed, deferred, bool(self._repository.pending(maximum=1))
            )
        finally:
            if self._storage.shutdown():
                raise ResourceAdmissionError("maintenance_exit_unconfirmed")

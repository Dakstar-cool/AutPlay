"""Filesystem and CPU media-tool boundaries for Vault application services."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ByteRange,
    ChromaprintEvidence,
    ChunkWriteResult,
    CommitResult,
    OpaqueStorageKey,
    Sha256Digest,
    VaultInventory,
    VerifiedStagedFile,
)


class RangeReader(Protocol):
    """A bounded closeable iterator for an authorized HTTP stream."""

    def __iter__(self) -> Iterator[bytes]:
        """Yield at most the authorized byte range."""

        ...

    def close(self) -> None:
        """Release the file descriptor on normal completion or cancellation."""

        ...


class VaultStorage(Protocol):
    """Crash-safe local/NAS immutable-byte storage operations."""

    def available_bytes(self) -> int:
        """Return current free bytes in the shared staging/CAS atomicity domain."""

        ...

    def create_staging(self, key: OpaqueStorageKey) -> None:
        """Create a new empty private staging file, refusing replacement."""

        ...

    def write_chunk(
        self,
        key: OpaqueStorageKey,
        *,
        offset: int,
        payload: bytes,
        payload_sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        """Durably append one verified chunk or prove a duplicate retry."""

        ...

    def verify_staging(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        """Stream a safe regular staging file and return its exact hash and size."""

        ...

    def verify_object(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        """Verify one immutable CAS object without trusting its key name."""

        ...

    def staging_path_for_media(self, key: OpaqueStorageKey) -> Path:
        """Return a verified private staging path for a trusted local media tool."""

        ...

    def truncate_staging(self, key: OpaqueStorageKey, byte_size: int) -> None:
        """Durably discard uncommitted trailing staging bytes during recovery."""

        ...

    def commit_staging(self, key: OpaqueStorageKey, verified: VerifiedStagedFile) -> CommitResult:
        """Publish verified bytes while retaining staging until DB finalization."""

        ...

    def cleanup_staging(self, key: OpaqueStorageKey) -> None:
        """Remove only a tracked staging link after the final DB commit."""

        ...

    def open_range(
        self,
        key: OpaqueStorageKey,
        byte_range: ByteRange,
        *,
        expected_size: int,
        verified_at: datetime,
        cancelled: Callable[[], bool] | None = None,
    ) -> RangeReader:
        """Open a bounded reader only while its commit-time stat proof remains valid."""

        ...

    def quarantine(self, key: OpaqueStorageKey, quarantine_key: OpaqueStorageKey) -> None:
        """Atomically move a suspect staging object out of processing."""

        ...

    def quarantine_object(self, key: OpaqueStorageKey, quarantine_key: OpaqueStorageKey) -> None:
        """Move a suspect final object into recoverable quarantine without deletion."""

        ...

    def inventory(self) -> VaultInventory:
        """Return safe key-only reconciliation inventory."""

        ...


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Sanitized result from a bounded argument-vector subprocess invocation."""

    returncode: int
    stdout: bytes
    stderr: bytes


class ExecutableRunner(Protocol):
    """CPU-only process boundary; implementations must not invoke a shell."""

    def run(
        self, arguments: Sequence[str], *, timeout_seconds: float, max_output_bytes: int
    ) -> ProcessResult:
        """Run exactly the supplied executable argument vector."""

        ...


class MediaInspector(Protocol):
    """Decode validation and technical metadata extraction boundary."""

    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        """Validate one safe staged file and return bounded audio metadata."""

        ...


class MediaDecodeValidator(Protocol):
    """Full-decode validation boundary kept separate from metadata probing."""

    def validate(self, path: Path) -> None:
        """Reject bytes which cannot be decoded as one bounded audio stream."""

        ...


class FingerprintGenerator(Protocol):
    """Chromaprint evidence generation boundary."""

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        """Generate versioned evidence from one safe staged file."""

        ...


__all__ = (
    "ExecutableRunner",
    "FingerprintGenerator",
    "MediaDecodeValidator",
    "MediaInspector",
    "ProcessResult",
    "RangeReader",
    "VaultStorage",
)

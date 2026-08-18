"""Pure values and stable failures for the P06 Vault boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

_OPAQUE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


class VaultError(RuntimeError):
    """Base class for expected Vault failures safe to map to API errors."""

    code: ClassVar[str] = "vault_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidStorageKeyError(VaultError):
    code = "invalid_storage_key"


class UploadOffsetError(VaultError):
    code = "upload_offset_mismatch"


class ChunkIntegrityError(VaultError):
    code = "upload_chunk_hash_mismatch"


class UploadLimitError(VaultError):
    code = "upload_limit_exceeded"


class StorageSafetyError(VaultError):
    code = "vault_storage_unsafe"


class StorageOperationError(VaultError):
    code = "vault_storage_unavailable"


class StagedFileNotFoundError(VaultError):
    code = "staged_file_not_found"


class ImmutableObjectConflictError(VaultError):
    code = "immutable_object_conflict"


class MediaValidationError(VaultError):
    code = "media_validation_failed"


class MediaToolTimeoutError(MediaValidationError):
    code = "media_tool_timeout"


class MediaToolOutputError(MediaValidationError):
    code = "media_tool_output_invalid"


@dataclass(frozen=True, slots=True)
class OpaqueStorageKey:
    """Generated server storage key; it is deliberately not a user path."""

    value: str

    def __post_init__(self) -> None:
        if _OPAQUE_KEY.fullmatch(self.value) is None:
            raise InvalidStorageKeyError()


@dataclass(frozen=True, slots=True)
class Sha256Digest:
    """Exactly one SHA-256 digest."""

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 32:
            raise ValueError("SHA-256 digests must contain 32 bytes")

    @property
    def hex(self) -> str:
        """Return the canonical lowercase hexadecimal encoding."""

        return self.value.hex()


@dataclass(frozen=True, slots=True)
class VaultLimits:
    """Resource bounds applied before untrusted uploaded bytes reach media tools."""

    max_object_bytes: int = 4 * 1024 * 1024 * 1024
    max_chunk_bytes: int = 1024 * 1024
    max_chunks: int = 4096
    io_block_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if (
            min(
                self.max_object_bytes,
                self.max_chunk_bytes,
                self.max_chunks,
                self.io_block_bytes,
            )
            < 1
        ):
            raise ValueError("Vault limits must be positive")
        if self.max_chunk_bytes > self.max_object_bytes:
            raise ValueError("chunk limit cannot exceed object limit")


@dataclass(frozen=True, slots=True)
class ChunkWriteResult:
    """The durable result of one resumable chunk attempt."""

    next_offset: int
    idempotent: bool


@dataclass(frozen=True, slots=True)
class VerifiedStagedFile:
    """Exact bytes observed while streaming a safe regular staging file."""

    byte_size: int
    sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class CommitResult:
    """The immutable object key selected by a commit attempt."""

    storage_key: OpaqueStorageKey
    already_present: bool


@dataclass(frozen=True, slots=True)
class ByteRange:
    """Inclusive byte range with a caller-validated total length."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid inclusive byte range")

    @property
    def length(self) -> int:
        """Return the number of bytes in this inclusive range."""

        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class VaultInventory:
    """Safe, key-only filesystem reconciliation snapshot."""

    staging_keys: tuple[OpaqueStorageKey, ...]
    object_keys: tuple[OpaqueStorageKey, ...]
    quarantine_keys: tuple[OpaqueStorageKey, ...]


@dataclass(frozen=True, slots=True)
class AudioTechnicalMetadata:
    """Bounded metadata extracted by ffprobe after a successful decode check."""

    codec: str
    container: str
    sample_rate_hz: int
    channels: int
    duration_ms: int
    bitrate_bps: int | None
    bit_depth: int | None


@dataclass(frozen=True, slots=True)
class ChromaprintEvidence:
    """Versioned fingerprint evidence, never a Recording identity assertion."""

    algorithm: str
    algorithm_version: str
    duration_ms: int
    payload: bytes


__all__ = (
    "AudioTechnicalMetadata",
    "ByteRange",
    "ChromaprintEvidence",
    "ChunkIntegrityError",
    "ChunkWriteResult",
    "CommitResult",
    "ImmutableObjectConflictError",
    "InvalidStorageKeyError",
    "MediaToolOutputError",
    "MediaToolTimeoutError",
    "MediaValidationError",
    "OpaqueStorageKey",
    "Sha256Digest",
    "StagedFileNotFoundError",
    "StorageOperationError",
    "StorageSafetyError",
    "UploadLimitError",
    "UploadOffsetError",
    "VaultError",
    "VaultInventory",
    "VaultLimits",
    "VerifiedStagedFile",
)

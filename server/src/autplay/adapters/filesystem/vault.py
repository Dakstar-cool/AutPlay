"""Safe, same-filesystem staging and immutable CAS storage implementation."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from autplay.domain.vault import (
    ByteRange,
    ChunkIntegrityError,
    ChunkWriteResult,
    CommitResult,
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StagedFileNotFoundError,
    StorageOperationError,
    StorageSafetyError,
    UploadLimitError,
    UploadOffsetError,
    VaultInventory,
    VaultLimits,
    VerifiedStagedFile,
)
from autplay.ports.vault import RangeReader


class _FileRangeReader:
    """Private descriptor-backed range iterator which closes on cancellation."""

    def __init__(
        self,
        descriptor: int,
        *,
        length: int,
        block_size: int,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        self._descriptor = descriptor
        self._remaining = length
        self._block_size = block_size
        self._cancelled = cancelled
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        try:
            while self._remaining > 0:
                if self._cancelled is not None and self._cancelled():
                    return
                payload = os.read(self._descriptor, min(self._remaining, self._block_size))
                if not payload:
                    raise StorageSafetyError()
                self._remaining -= len(payload)
                yield payload
        finally:
            self.close()

    def close(self) -> None:
        """Close exactly once, including after a client-disconnect cancellation."""

        if not self._closed:
            os.close(self._descriptor)
            self._closed = True


class FilesystemVaultStorage:
    """Filesystem/NAS Vault with private staging and immutable SHA-256 CAS objects.

    The root is initialized once by trusted server configuration.  Public methods
    only accept server-generated opaque keys, never user-provided paths.
    """

    _STAGING = "staging"
    _OBJECTS = "objects"
    _QUARANTINE = "quarantine"
    _BINARY = getattr(os, "O_BINARY", 0)

    def __init__(self, root: Path, *, limits: VaultLimits | None = None) -> None:
        self._root = root.resolve(strict=False)
        self._limits = limits or VaultLimits()
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._require_directory(self._root)
            for name in (self._STAGING, self._OBJECTS, self._QUARANTINE):
                directory = self._root / name
                directory.mkdir(mode=0o700, exist_ok=True)
                self._require_directory(directory)
            if (
                os.stat(self._root / self._STAGING).st_dev
                != os.stat(self._root / self._OBJECTS).st_dev
            ):
                raise StorageSafetyError()
        except OSError as error:
            raise StorageOperationError() from error

    def available_bytes(self) -> int:
        """Return free bytes without exposing the configured filesystem path."""

        try:
            free = shutil.disk_usage(self._root).free
        except OSError as error:
            raise StorageOperationError() from error
        if free < 0:
            raise StorageSafetyError()
        return free

    def create_staging(self, key: OpaqueStorageKey) -> None:
        """Create a new zero-length staging file without replacing another upload."""

        path = self._staging_path(key)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._BINARY,
                0o600,
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._fsync_parent(path)
        except FileExistsError as error:
            raise ImmutableObjectConflictError() from error
        except OSError as error:
            raise StorageOperationError() from error

    def write_chunk(
        self,
        key: OpaqueStorageKey,
        *,
        offset: int,
        payload: bytes,
        payload_sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        """Append one validated chunk, recognizing an exact retry at an old offset."""

        if offset < 0:
            raise UploadOffsetError()
        if not payload or len(payload) > self._limits.max_chunk_bytes:
            raise UploadLimitError()
        if hashlib.sha256(payload).digest() != payload_sha256.value:
            raise ChunkIntegrityError()
        path = self._staging_path(key)
        descriptor = self._open_regular(path, missing_as_staged=True, writable=True)
        try:
            current_size = os.fstat(descriptor).st_size
            if current_size + len(payload) > self._limits.max_object_bytes:
                raise UploadLimitError()
            if offset == current_size:
                os.lseek(descriptor, current_size, os.SEEK_SET)
                self._write_all(descriptor, payload)
                os.fsync(descriptor)
                return ChunkWriteResult(next_offset=current_size + len(payload), idempotent=False)
            if offset < current_size and offset + len(payload) <= current_size:
                os.lseek(descriptor, offset, os.SEEK_SET)
                existing = self._read_exact(descriptor, len(payload))
                if existing == payload:
                    return ChunkWriteResult(next_offset=current_size, idempotent=True)
            raise UploadOffsetError()
        except OSError as error:
            raise StorageOperationError() from error
        finally:
            os.close(descriptor)

    def verify_staging(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        """Hash a bounded staging file through a regular non-symlink descriptor."""

        path = self._staging_path(key)
        descriptor = self._open_regular(path, missing_as_staged=True, writable=False)
        try:
            digest = hashlib.sha256()
            byte_size = 0
            while payload := os.read(descriptor, self._limits.io_block_bytes):
                byte_size += len(payload)
                if byte_size > self._limits.max_object_bytes:
                    raise UploadLimitError()
                digest.update(payload)
            if byte_size < 1:
                raise StorageSafetyError()
            return VerifiedStagedFile(byte_size=byte_size, sha256=Sha256Digest(digest.digest()))
        except OSError as error:
            raise StorageOperationError() from error
        finally:
            os.close(descriptor)

    def verify_object(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        """Verify final CAS bytes independently of the hash-derived key."""

        return self._verify_final(self._object_path(key))

    def staging_path_for_media(self, key: OpaqueStorageKey) -> Path:
        """Return a safe private path only for a locally configured trusted tool."""

        path = self._staging_path(key)
        self._assert_safe_file(path, missing_as_staged=True)
        return path

    def truncate_staging(self, key: OpaqueStorageKey, byte_size: int) -> None:
        """Durably remove only an uncommitted trailing suffix after a DB rollback."""

        if byte_size < 0:
            raise UploadOffsetError()
        path = self._staging_path(key)
        descriptor = self._open_regular(path, missing_as_staged=True, writable=True)
        try:
            current_size = os.fstat(descriptor).st_size
            if byte_size > current_size:
                raise UploadOffsetError()
            os.ftruncate(descriptor, byte_size)
            os.fsync(descriptor)
        except OSError as error:
            raise StorageOperationError() from error
        finally:
            os.close(descriptor)

    def commit_staging(self, key: OpaqueStorageKey, verified: VerifiedStagedFile) -> CommitResult:
        """Publish matching bytes by hard link and retain staging for DB recovery."""

        source = self._staging_path(key)
        actual = self.verify_staging(key)
        if actual != verified:
            raise ImmutableObjectConflictError()
        destination_key = OpaqueStorageKey(verified.sha256.hex)
        destination = self._object_path(destination_key)
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._require_directory(destination.parent)
            try:
                os.link(source, destination)
            except FileExistsError:
                existing = self._verify_final(destination)
                if existing != verified:
                    raise ImmutableObjectConflictError() from None
                self._seal_immutable(destination)
                self._fsync_parent(destination)
                return CommitResult(storage_key=destination_key, already_present=True)
            self._seal_immutable(destination)
            self._fsync_parent(destination)
            return CommitResult(storage_key=destination_key, already_present=False)
        except ImmutableObjectConflictError:
            raise
        except OSError as error:
            raise StorageOperationError() from error

    def cleanup_staging(self, key: OpaqueStorageKey) -> None:
        """Unlink a verified staging entry only after application DB finalization."""

        path = self._staging_path(key)
        self._assert_safe_file(path, missing_as_staged=True)
        try:
            os.unlink(path)
            self._fsync_parent(path)
        except OSError as error:
            raise StorageOperationError() from error

    def open_range(
        self,
        key: OpaqueStorageKey,
        byte_range: ByteRange,
        *,
        expected_size: int,
        verified_at: datetime,
        cancelled: Callable[[], bool] | None = None,
    ) -> RangeReader:
        """Open an exact subset while its commit-time size/mtime proof remains valid."""

        path = self._object_path(key)
        descriptor = self._open_regular(path, missing_as_staged=False, writable=False)
        try:
            observed = os.fstat(descriptor)
            if verified_at.tzinfo is None or expected_size < 1:
                raise StorageSafetyError()
            verified_utc = verified_at.astimezone(UTC)
            verified_ns = (
                int(verified_utc.timestamp()) * 1_000_000_000 + verified_utc.microsecond * 1_000
            )
            if (
                observed.st_size != expected_size
                or byte_range.end >= observed.st_size
                or observed.st_mtime_ns > verified_ns
                or (os.name != "nt" and observed.st_mode & 0o222)
            ):
                raise StorageSafetyError()
            os.lseek(descriptor, byte_range.start, os.SEEK_SET)
            return _FileRangeReader(
                descriptor,
                length=byte_range.length,
                block_size=self._limits.io_block_bytes,
                cancelled=cancelled,
            )
        except OSError, StorageSafetyError:
            os.close(descriptor)
            raise

    def quarantine(self, key: OpaqueStorageKey, quarantine_key: OpaqueStorageKey) -> None:
        """Move a staging file to a non-processing location without exposing its path."""

        source = self._staging_path(key)
        destination = self._quarantine_path(quarantine_key)
        self._assert_safe_file(source, missing_as_staged=True)
        try:
            os.link(source, destination)
            self._fsync_parent(destination)
            os.unlink(source)
            self._fsync_parent(source)
        except FileExistsError as error:
            raise ImmutableObjectConflictError() from error
        except OSError as error:
            raise StorageOperationError() from error

    def quarantine_object(self, key: OpaqueStorageKey, quarantine_key: OpaqueStorageKey) -> None:
        """Move a final CAS entry to recoverable quarantine without discarding bytes."""

        source = self._object_path(key)
        destination = self._quarantine_path(quarantine_key)
        self._assert_safe_file(source, missing_as_staged=False)
        try:
            os.link(source, destination)
            self._fsync_parent(destination)
            os.unlink(source)
            self._fsync_parent(source)
        except FileExistsError as error:
            raise ImmutableObjectConflictError() from error
        except OSError as error:
            raise StorageOperationError() from error

    def inventory(self) -> VaultInventory:
        """List safe filenames only; suspicious filesystem entries cause reconciliation failure."""

        return VaultInventory(
            staging_keys=self._inventory_directory(self._root / self._STAGING),
            object_keys=self._inventory_objects(),
            quarantine_keys=self._inventory_directory(self._root / self._QUARANTINE),
        )

    def _staging_path(self, key: OpaqueStorageKey) -> Path:
        return self._root / self._STAGING / key.value

    def _quarantine_path(self, key: OpaqueStorageKey) -> Path:
        return self._root / self._QUARANTINE / key.value

    def _object_path(self, key: OpaqueStorageKey) -> Path:
        if len(key.value) != 64 or any(
            character not in "0123456789abcdef" for character in key.value
        ):
            raise StorageSafetyError()
        return self._root / self._OBJECTS / key.value[:2] / key.value[2:4] / key.value

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short file write")
            offset += written

    def _seal_immutable(self, path: Path) -> None:
        """Durably remove POSIX write bits from a verified final CAS inode."""

        if os.name == "nt":
            return
        try:
            os.chmod(path, stat.S_IRUSR)
            descriptor = self._open_regular(path, missing_as_staged=False, writable=False)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StorageOperationError() from error

    @staticmethod
    def _read_exact(descriptor: int, length: int) -> bytes:
        output = bytearray()
        while len(output) < length:
            payload = os.read(descriptor, length - len(output))
            if not payload:
                break
            output.extend(payload)
        return bytes(output)

    def _open_regular(self, path: Path, *, missing_as_staged: bool, writable: bool) -> int:
        self._assert_safe_file(path, missing_as_staged=missing_as_staged)
        flags = os.O_RDWR if writable else os.O_RDONLY
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags | nofollow | self._BINARY)
        except FileNotFoundError as error:
            if missing_as_staged:
                raise StagedFileNotFoundError() from error
            raise StorageSafetyError() from error
        except OSError as error:
            raise StorageOperationError() from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise StorageSafetyError()
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _assert_safe_file(self, path: Path, *, missing_as_staged: bool) -> None:
        self._require_safe_ancestors(path.parent)
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError as error:
            if missing_as_staged:
                raise StagedFileNotFoundError() from error
            raise StorageSafetyError() from error
        except OSError as error:
            raise StorageOperationError() from error
        if not stat.S_ISREG(mode):
            raise StorageSafetyError()

    def _require_safe_ancestors(self, directory: Path) -> None:
        try:
            directory.relative_to(self._root)
        except ValueError as error:
            raise StorageSafetyError() from error
        current = self._root
        self._require_directory(current)
        for part in directory.relative_to(self._root).parts:
            current = current / part
            self._require_directory(current)

    @staticmethod
    def _require_directory(path: Path) -> None:
        try:
            mode = os.lstat(path).st_mode
        except OSError as error:
            raise StorageSafetyError() from error
        if not stat.S_ISDIR(mode):
            raise StorageSafetyError()

    def _verify_final(self, path: Path) -> VerifiedStagedFile:
        descriptor = self._open_regular(path, missing_as_staged=False, writable=False)
        try:
            digest = hashlib.sha256()
            byte_size = 0
            while payload := os.read(descriptor, self._limits.io_block_bytes):
                byte_size += len(payload)
                if byte_size > self._limits.max_object_bytes:
                    raise StorageSafetyError()
                digest.update(payload)
            return VerifiedStagedFile(byte_size=byte_size, sha256=Sha256Digest(digest.digest()))
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        # CPython's Windows os.open cannot obtain a directory handle; production
        # Vault deployment is Linux x86_64, where directory fsync is mandatory.
        # Individual file fsync and atomic hard-link publication still occur on
        # development Windows hosts, whose directory durability is documented as
        # weaker and therefore requires reconciliation after a crash.
        if os.name == "nt":
            return
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StorageOperationError() from error

    def _inventory_directory(self, directory: Path) -> tuple[OpaqueStorageKey, ...]:
        self._require_directory(directory)
        keys: list[OpaqueStorageKey] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise StorageOperationError() from error
        for entry in entries:
            if not stat.S_ISREG(os.lstat(entry).st_mode):
                raise StorageSafetyError()
            keys.append(OpaqueStorageKey(entry.name))
        return tuple(keys)

    def _inventory_objects(self) -> tuple[OpaqueStorageKey, ...]:
        root = self._root / self._OBJECTS
        self._require_directory(root)
        keys: list[OpaqueStorageKey] = []
        try:
            for first in sorted(root.iterdir(), key=lambda item: item.name):
                self._require_directory(first)
                for second in sorted(first.iterdir(), key=lambda item: item.name):
                    self._require_directory(second)
                    for entry in sorted(second.iterdir(), key=lambda item: item.name):
                        if not stat.S_ISREG(os.lstat(entry).st_mode):
                            raise StorageSafetyError()
                        key = OpaqueStorageKey(entry.name)
                        if key.value[:2] != first.name or key.value[2:4] != second.name:
                            raise StorageSafetyError()
                        if len(key.value) != 64 or any(
                            character not in "0123456789abcdef" for character in key.value
                        ):
                            raise StorageSafetyError()
                        keys.append(key)
        except OSError as error:
            raise StorageOperationError() from error
        return tuple(keys)


__all__ = ("FilesystemVaultStorage",)

"""Retire exact provider files without traversing scratch contents or overwriting evidence."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
from pathlib import Path
from uuid import UUID

from autplay.application.provider_cleanup import ProviderCleanupClaim
from autplay.application.provider_scratch import ProviderScratchClaim
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
    UploadLimitError,
    VaultLimits,
    VerifiedStagedFile,
)

from .vault import FilesystemVaultStorage


def _rename_directory(source: Path, destination: Path) -> None:
    """Atomic no-replace only; never fall back to a byte-copy or replacing rename."""
    if os.name == "nt":
        os.rename(source, destination)
        return
    if sys.platform != "linux":
        raise StorageSafetyError()
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as error:
        raise StorageSafetyError() from error
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # AT_FDCWD=-100, RENAME_NOREPLACE=1. Both paths are trusted absolute paths.
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class FilesystemProviderStorage:
    def __init__(self, root: Path) -> None:
        # Vault root is trusted configuration. Do not resolve a child reparse point.
        self._root = Path(os.path.abspath(root))
        self._directory(self._root)
        for name in ("staging", "quarantine"):
            self._directory(self._root / name)
        for name in ("provider-work", "provider-retired"):
            path = self._root / name
            try:
                path.mkdir(mode=0o700, exist_ok=True)
            except OSError as error:
                raise StorageOperationError() from error
            self._directory(path)
            FilesystemVaultStorage._fsync_parent(path)

    @staticmethod
    def _stat(path: Path, *, directory: bool) -> os.stat_result | None:
        try:
            result = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StorageOperationError() from error
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(result.st_mode) or (
            getattr(result, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise StorageSafetyError()
        return result

    @classmethod
    def _directory(cls, path: Path) -> None:
        if cls._stat(path, directory=True) is None:
            raise StorageSafetyError()

    def create_workspace(self, execution_id: UUID) -> Path:
        """The contained child calls this only after durable registration and GO."""
        self._directory(self._root)
        parent = self._root / "provider-work"
        self._directory(parent)
        path = parent / execution_id.hex
        try:
            path.mkdir(mode=0o700)
            FilesystemVaultStorage._fsync_parent(path)
        except FileExistsError as error:
            raise ImmutableObjectConflictError() from error
        except OSError as error:
            raise StorageOperationError() from error
        return path

    def retire(self, claim: ProviderCleanupClaim) -> None:
        self._directory(self._root)
        for name in ("staging", "quarantine", "provider-work", "provider-retired"):
            self._directory(self._root / name)
        self._retire_file(
            self._root / "staging" / claim.staging_key.value,
            self._root / "quarantine" / claim.quarantine_key.value,
        )
        self._retire_workspace(
            self._root / "provider-work" / claim.execution_id.hex,
            self._root / "provider-retired" / claim.claim_id.hex,
        )

    def copy_verified_source(
        self, execution_id: UUID, source: Path, *, limits: VaultLimits
    ) -> VerifiedStagedFile:
        """Run only in the admitted child; preserve partial files for claimed cleanup."""
        workspace = self._root / "provider-work" / execution_id.hex
        for path in (self._root, workspace.parent, workspace, self._root / "staging"):
            self._directory(path)
        if source.parent != workspace:
            raise StorageSafetyError()
        original = self._stat(source, directory=False)
        if original is None or original.st_nlink != 1 or original.st_size < 1:
            raise StorageSafetyError()
        if original.st_size > limits.max_object_bytes:
            raise UploadLimitError()
        vault = FilesystemVaultStorage(self._root, limits=limits)
        descriptor = vault._open_regular(source, missing_as_staged=False, writable=False)
        key = OpaqueStorageKey(f"provider-{execution_id.hex}")
        digest, offset, chunks = hashlib.sha256(), 0, 0
        try:
            opened = os.fstat(descriptor)
            if not os.path.samestat(original, opened):
                raise StorageSafetyError()
            vault.create_staging(key)  # A retry must never truncate an earlier writer.
            while payload := os.read(descriptor, limits.max_chunk_bytes):
                chunks += 1
                if chunks > limits.max_chunks or offset + len(payload) > limits.max_object_bytes:
                    raise UploadLimitError()
                digest.update(payload)
                offset = vault.write_chunk(
                    key,
                    offset=offset,
                    payload=payload,
                    payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
                ).next_offset
            finished = os.fstat(descriptor)
            current = self._stat(source, directory=False)
            if (
                current is None
                or not os.path.samestat(original, current)
                or offset != original.st_size
                or finished.st_size != original.st_size
                or finished.st_mtime_ns != original.st_mtime_ns
            ):
                raise StorageSafetyError()
        except OSError as error:
            raise StorageOperationError() from error
        finally:
            os.close(descriptor)
        verified = vault.verify_staging(key)
        if verified.byte_size != offset or verified.sha256.value != digest.digest():
            raise StorageSafetyError()
        return verified

    def retire_scratch(self, claim: ProviderScratchClaim) -> None:
        self._directory(self._root)
        for name in ("provider-work", "provider-retired"):
            self._directory(self._root / name)
        self._retire_workspace(
            self._root / "provider-work" / claim.execution_id.hex,
            self._root / "provider-retired" / claim.claim_id.hex,
            required=True,
        )

    @classmethod
    def _retire_file(cls, source: Path, destination: Path) -> None:
        original = cls._stat(source, directory=False)
        target = cls._stat(destination, directory=False)
        if original is None:
            # A durable claim can precede creation, or replay a completed unlink.
            FilesystemVaultStorage._fsync_parent(destination)
            FilesystemVaultStorage._fsync_parent(source)
            return
        if target is None:
            try:
                os.link(source, destination, follow_symlinks=False)
            except FileExistsError:
                pass
            except FileNotFoundError:
                # A concurrent replay may already have linked and unlinked it.
                if cls._stat(source, directory=False) is not None:
                    raise StorageOperationError() from None
            except OSError as error:
                if not cls._file_retired(source, destination, original):
                    raise StorageOperationError() from error
            target = cls._stat(destination, directory=False)
        if target is None or not os.path.samestat(original, target):
            raise ImmutableObjectConflictError()
        FilesystemVaultStorage._fsync_parent(destination)
        current = cls._stat(source, directory=False)
        if current is not None and not os.path.samestat(original, current):
            raise ImmutableObjectConflictError()
        try:
            if current is not None:
                os.unlink(source)
        except FileNotFoundError:
            pass
        except OSError as error:
            # Windows can report ACCESS_DENIED for a concurrent delete-pending
            # link. Accept only observed absence plus the exact retained inode.
            if not cls._file_retired(source, destination, original):
                raise StorageOperationError() from error
        FilesystemVaultStorage._fsync_parent(source)

    @classmethod
    def _file_retired(cls, source: Path, destination: Path, original: os.stat_result) -> bool:
        if cls._stat(source, directory=False) is not None:
            return False
        target = cls._stat(destination, directory=False)
        return target is not None and os.path.samestat(original, target)

    @classmethod
    def _retire_workspace(cls, source: Path, destination: Path, *, required: bool = False) -> None:
        original = cls._stat(source, directory=True)
        target = cls._stat(destination, directory=True)
        if required and original is None and target is None:
            raise StorageOperationError()
        if original is not None:
            if target is not None:
                if cls._stat(source, directory=True) is not None or not os.path.samestat(
                    original, target
                ):
                    raise ImmutableObjectConflictError()
            else:
                try:
                    _rename_directory(source, destination)
                except OSError as error:
                    # A second cleaner may have won. Accept only the same directory.
                    target = cls._stat(destination, directory=True)
                    if (
                        error.errno not in {errno.ENOENT, errno.EEXIST}
                        or cls._stat(source, directory=True) is not None
                        or target is None
                        or not os.path.samestat(original, target)
                    ):
                        raise StorageOperationError() from error
        # Fsync even on replay: the previous call may have died after rename.
        FilesystemVaultStorage._fsync_parent(destination)
        FilesystemVaultStorage._fsync_parent(source)

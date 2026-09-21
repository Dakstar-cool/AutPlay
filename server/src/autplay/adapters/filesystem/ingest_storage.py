"""Open an existing Vault and preserve bytes until durable CAS is verified."""

import os
from pathlib import Path

from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    StorageOperationError,
    VaultLimits,
    VerifiedStagedFile,
)

from .provider_staging import FilesystemProviderStorage
from .vault import FilesystemVaultStorage


class ExistingIngestStorage(FilesystemVaultStorage):
    """No mkdir/resolve: an absent mount must never become an empty Vault."""

    def __init__(self, root: Path, *, limits: VaultLimits) -> None:
        self._root = Path(os.path.abspath(root))
        self._limits = limits
        self._directories = (
            *reversed(self._root.parents),
            self._root,
            self._root / "staging",
            self._root / "objects",
        )
        identities = []
        for directory in self._directories:
            identity = FilesystemProviderStorage._stat(directory, directory=True)
            if identity is None:
                raise StorageOperationError()
            identities.append(identity)
        self._identities = tuple(identities)
        self.check_namespace()

    def check_namespace(self) -> None:
        for directory, expected in zip(self._directories, self._identities, strict=True):
            actual = FilesystemProviderStorage._stat(directory, directory=True)
            if actual is None or not os.path.samestat(expected, actual):
                raise StorageOperationError()

    def cleanup_finalized(self, key: OpaqueStorageKey, expected: VerifiedStagedFile) -> None:
        self.check_namespace()
        # Never delete the remaining staging link on metadata assertions alone.
        if self.verify_object(OpaqueStorageKey(expected.sha256.hex)) != expected:
            raise ImmutableObjectConflictError()
        destination = self._object_path(OpaqueStorageKey(expected.sha256.hex))
        self._seal_immutable(destination)
        self._sync_publication(destination)
        source = self._root / "staging" / key.value
        present = FilesystemProviderStorage._stat(source, directory=False)
        if present is not None:
            if self.verify_staging(key) != expected:
                raise ImmutableObjectConflictError()
            actual = FilesystemProviderStorage._stat(source, directory=False)
            if actual is None or not os.path.samestat(present, actual):
                raise StorageOperationError()
            self.check_namespace()
            try:
                source.unlink()
            except OSError as error:
                raise StorageOperationError() from error
        # An earlier attempt may have unlinked but crashed before fsync.
        self._fsync_parent(source)
        self.check_namespace()
        if FilesystemProviderStorage._stat(source, directory=False) is not None:
            raise StorageOperationError()

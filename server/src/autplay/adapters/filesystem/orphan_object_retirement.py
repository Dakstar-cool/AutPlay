"""Replayable orphan CAS retirement preserves bytes and never replaces a destination."""

import os
from pathlib import Path

from autplay.application.orphan_object_retirement import OrphanObjectClaim
from autplay.domain.vault import StorageOperationError

from .provider_staging import FilesystemProviderStorage


class FilesystemOrphanObjectRetirement:
    def __init__(self, root: Path) -> None:
        self._root = Path(os.path.abspath(root))

    def _paths(self, claim: OrphanObjectClaim) -> tuple[Path, Path, tuple[Path, ...]]:
        key = claim.storage_key.value
        objects = self._root / "objects"
        source = objects / key[:2] / key[2:4] / key
        destination = self._root / "quarantine" / claim.quarantine_key.value
        directories = (
            self._root,
            objects,
            source.parent.parent,
            source.parent,
            destination.parent,
        )
        return source, destination, directories

    def retire(self, claim: OrphanObjectClaim) -> None:
        source, destination, directories = self._paths(claim)
        # No mkdir/resolve/traversal of an arbitrary user path, even during replay.
        for directory in directories:
            FilesystemProviderStorage._directory(directory)
        if (
            FilesystemProviderStorage._stat(source, directory=False) is None
            and FilesystemProviderStorage._stat(destination, directory=False) is None
        ):
            raise StorageOperationError()
        FilesystemProviderStorage._retire_file(source, destination)

    def confirm_missing(self, claim: OrphanObjectClaim) -> None:
        """Check absence without changing bytes or inferring any historical writer exit."""
        source, destination, directories = self._paths(claim)
        identities: list[os.stat_result] = []
        for directory in directories:
            identity = FilesystemProviderStorage._stat(directory, directory=True)
            if identity is None:
                raise StorageOperationError()
            identities.append(identity)
        for path in (source, destination):
            if FilesystemProviderStorage._stat(path, directory=False) is not None:
                raise StorageOperationError()
        # ENOENT can describe a disappeared parent. Require the same safe namespace
        # after both leaf checks before an absence result can release the digest claim.
        for directory, expected in zip(directories, identities, strict=True):
            actual = FilesystemProviderStorage._stat(directory, directory=True)
            if actual is None or not os.path.samestat(expected, actual):
                raise StorageOperationError()

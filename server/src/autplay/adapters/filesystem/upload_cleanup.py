"""Retire one claimed terminal upload without traversal or replacement."""

import os
from pathlib import Path

from autplay.application.upload_cleanup import UploadCleanupClaim
from autplay.domain.vault import StorageOperationError

from .provider_staging import FilesystemProviderStorage


class FilesystemUploadCleanup:
    def __init__(self, root: Path) -> None:
        self._root = Path(os.path.abspath(root))

    def retire(self, claim: UploadCleanupClaim) -> None:
        source = self._root / "staging" / claim.storage_key.value
        destination = self._root / "quarantine" / claim.quarantine_key.value
        directories = (self._root, source.parent, destination.parent)
        identities = []
        for directory in directories:
            identity = FilesystemProviderStorage._stat(directory, directory=True)
            if identity is None:
                # An absent mount/ancestor cannot prove that the leaf is absent.
                raise StorageOperationError()
            identities.append(identity)
        FilesystemProviderStorage._retire_file(source, destination)
        if FilesystemProviderStorage._stat(source, directory=False) is not None:
            raise StorageOperationError()
        for directory, expected in zip(directories, identities, strict=True):
            actual = FilesystemProviderStorage._stat(directory, directory=True)
            if actual is None or not os.path.samestat(expected, actual):
                raise StorageOperationError()

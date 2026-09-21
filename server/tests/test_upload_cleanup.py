"""One exact staging leaf is preserved; missing namespaces never prove cleanup."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.upload_cleanup import FilesystemUploadCleanup
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.application.upload_cleanup import UploadCleanupClaim
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    StorageOperationError,
    StorageSafetyError,
)


@pytest.mark.parametrize("state", ["source", "linked", "destination", "absent"])
def test_retirement_and_replay_preserve_bytes_without_replacement(
    tmp_path: Path, state: str
) -> None:
    FilesystemVaultStorage(tmp_path)
    claim = UploadCleanupClaim(uuid4(), OpaqueStorageKey("owned-upload"))
    source = tmp_path / "staging" / claim.storage_key.value
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    if state in {"source", "linked"}:
        source.write_bytes(b"partial upload")
    if state == "linked":
        os.link(source, target)
    if state == "destination":
        target.write_bytes(b"partial upload")
    storage = FilesystemUploadCleanup(tmp_path)
    storage.retire(claim)
    storage.retire(claim)
    assert not source.exists()
    if state == "absent":
        assert not target.exists()
    else:
        assert target.read_bytes() == b"partial upload"


def test_destination_collision_does_not_discard_either_file(tmp_path: Path) -> None:
    FilesystemVaultStorage(tmp_path)
    claim = UploadCleanupClaim(uuid4(), OpaqueStorageKey("owned-upload"))
    source = tmp_path / "staging" / claim.storage_key.value
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    source.write_bytes(b"source")
    target.write_bytes(b"other evidence")
    with pytest.raises(ImmutableObjectConflictError):
        FilesystemUploadCleanup(tmp_path).retire(claim)
    assert source.read_bytes() == b"source" and target.read_bytes() == b"other evidence"


@pytest.mark.parametrize("missing", ["root", "staging", "quarantine"])
def test_missing_namespace_defers_without_creating_directories(
    tmp_path: Path, missing: str
) -> None:
    root = tmp_path / "vault"
    if missing != "root":
        FilesystemVaultStorage(root)
        (root / missing).rmdir()
    with pytest.raises(StorageOperationError):
        FilesystemUploadCleanup(root).retire(UploadCleanupClaim(uuid4(), OpaqueStorageKey("key")))
    assert not (root if missing == "root" else root / missing).exists()


@pytest.mark.parametrize("target", ["staging", "quarantine", "leaf"])
def test_symlink_or_reparse_target_is_rejected(tmp_path: Path, target: str) -> None:
    FilesystemVaultStorage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    claim = UploadCleanupClaim(uuid4(), OpaqueStorageKey("owned-upload"))
    if target == "leaf":
        path = tmp_path / "staging" / claim.storage_key.value
        actual = outside / "bytes"
        actual.write_bytes(b"unrelated")
    else:
        path, actual = tmp_path / target, outside
        path.rmdir()
    try:
        path.symlink_to(actual, target_is_directory=target != "leaf")
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(StorageSafetyError):
        FilesystemUploadCleanup(tmp_path).retire(claim)


def test_directory_replacement_during_absence_check_cannot_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FilesystemVaultStorage(tmp_path)
    original = FilesystemProviderStorage._retire_file

    def replace_after_check(source: Path, destination: Path) -> None:
        original(source, destination)
        source.parent.rename(tmp_path / "old-staging")
        source.parent.mkdir()

    monkeypatch.setattr(FilesystemProviderStorage, "_retire_file", replace_after_check)
    with pytest.raises(StorageOperationError):
        FilesystemUploadCleanup(tmp_path).retire(
            UploadCleanupClaim(uuid4(), OpaqueStorageKey("key"))
        )

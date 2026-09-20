"""Real filesystem replay preserves all provider bytes through interrupted retirement."""

from __future__ import annotations

import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem import provider_staging
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.application.provider_cleanup import ProviderCleanupClaim, provider_cleanup_id
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    StorageOperationError,
    StorageSafetyError,
)


def setup(root: Path) -> tuple[FilesystemProviderStorage, ProviderCleanupClaim, Path, Path]:
    FilesystemVaultStorage(root)
    storage = FilesystemProviderStorage(root)
    execution = uuid4()
    claim = ProviderCleanupClaim(execution, provider_cleanup_id(execution))
    staged = root / "staging" / claim.staging_key.value
    staged.write_bytes(b"verified provider bytes")
    workspace = storage.create_workspace(execution)
    (workspace / "audio.part").write_bytes(b"partial")
    (workspace / "fragments").mkdir()
    (workspace / "fragments" / "001").write_bytes(b"fragment")
    return storage, claim, staged, workspace


def assert_retired(root: Path, claim: ProviderCleanupClaim) -> None:
    assert not (root / "staging" / claim.staging_key.value).exists()
    assert not (root / "provider-work" / claim.execution_id.hex).exists()
    assert (
        root / "quarantine" / claim.quarantine_key.value
    ).read_bytes() == b"verified provider bytes"
    retired = root / "provider-retired" / claim.claim_id.hex
    assert (retired / "audio.part").read_bytes() == b"partial"
    assert (retired / "fragments" / "001").read_bytes() == b"fragment"


def test_entire_workspace_and_final_staging_retirement_replay(tmp_path: Path) -> None:
    storage, claim, _, _ = setup(tmp_path)
    storage.retire(claim)
    storage.retire(claim)
    assert_retired(tmp_path, claim)


def test_crash_after_hardlink_before_unlink_recovers_exact_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, claim, staged, _ = setup(tmp_path)
    unlink = os.unlink

    def interrupted(path: Path) -> None:
        if path == staged:
            raise OSError(errno.EIO, "test.interrupted")
        unlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", interrupted)
        with pytest.raises(StorageOperationError):
            storage.retire(claim)
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    assert os.path.samestat(staged.lstat(), target.lstat())
    storage.retire(claim)
    assert_retired(tmp_path, claim)


def test_crash_after_scratch_rename_replays_without_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, claim, _, _ = setup(tmp_path)
    rename = provider_staging._rename_directory

    def interrupted(source: Path, destination: Path) -> None:
        rename(source, destination)
        raise OSError(errno.EIO, "test.interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(provider_staging, "_rename_directory", interrupted)
        with pytest.raises(StorageOperationError):
            storage.retire(claim)
    storage.retire(claim)
    assert_retired(tmp_path, claim)


@pytest.mark.parametrize("removed", [False, True])
def test_access_denied_requires_proof_of_other_cleaners_completed_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed: bool
) -> None:
    storage, claim, staged, _ = setup(tmp_path)
    unlink = os.unlink

    def denied(path: Path) -> None:
        if path == staged:
            if removed:
                unlink(path)
            raise PermissionError(errno.EACCES, "test.concurrent_delete")
        unlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", denied)
        if removed:
            storage.retire(claim)
        else:
            with pytest.raises(StorageOperationError):
                storage.retire(claim)
            assert staged.read_bytes() == b"verified provider bytes"
    storage.retire(claim)
    assert_retired(tmp_path, claim)


@pytest.mark.parametrize("conflict", ["file", "directory"])
def test_conflicting_destination_is_never_replaced(tmp_path: Path, conflict: str) -> None:
    storage, claim, staged, workspace = setup(tmp_path)
    if conflict == "file":
        destination = tmp_path / "quarantine" / claim.quarantine_key.value
        destination.write_bytes(b"other evidence")
    else:
        destination = tmp_path / "provider-retired" / claim.claim_id.hex
        destination.mkdir()
    with pytest.raises(ImmutableObjectConflictError):
        storage.retire(claim)
    assert (workspace / "audio.part").read_bytes() == b"partial"
    if conflict == "file":
        assert staged.read_bytes() == b"verified provider bytes"
        assert destination.read_bytes() == b"other evidence"
    else:
        assert list(destination.iterdir()) == []


def test_no_replace_primitive_protects_existing_empty_directory(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "fragment").write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        provider_staging._rename_directory(source, destination)
    assert (source / "fragment").read_bytes() == b"preserve"
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("_race", range(10))
def test_two_cleaners_converge_on_same_owned_bytes(tmp_path: Path, _race: int) -> None:
    storage, claim, _, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(storage.retire, claim) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    assert_retired(tmp_path, claim)


def test_symlink_destination_cannot_authorize_unlink(tmp_path: Path) -> None:
    storage, claim, staged, _ = setup(tmp_path)
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    try:
        target.symlink_to(staged)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(StorageSafetyError):
        storage.retire(claim)
    assert staged.read_bytes() == b"verified provider bytes"


def test_cross_device_failure_keeps_scratch_without_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, claim, _, workspace = setup(tmp_path)

    def cross_device(source: Path, destination: Path) -> None:
        raise OSError(errno.EXDEV, "test.cross_device")

    monkeypatch.setattr(provider_staging, "_rename_directory", cross_device)
    with pytest.raises(StorageOperationError):
        storage.retire(claim)
    assert (workspace / "fragments" / "001").read_bytes() == b"fragment"
    assert not (tmp_path / "provider-retired" / claim.claim_id.hex).exists()

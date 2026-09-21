"""Scratch-only retirement preserves the separate Vault file and exact replay."""

import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.adapters.filesystem import provider_staging
from autplay.adapters.filesystem.provider_staging import FilesystemProviderStorage
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.application.provider_scratch import ProviderScratchClaim, provider_scratch_id
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    StorageOperationError,
    StorageSafetyError,
)
from test_provider_filesystem import setup


def test_scratch_retirement_replay_leaves_staging_inode_and_bytes_untouched(tmp_path: Path) -> None:
    storage, abandoned, staged, workspace = setup(tmp_path)
    claim = ProviderScratchClaim(
        abandoned.execution_id, provider_scratch_id(abandoned.execution_id)
    )
    assert claim.claim_id != abandoned.claim_id
    original = staged.stat()
    storage.retire_scratch(claim)
    storage.retire_scratch(claim)
    assert not workspace.exists()
    assert os.path.samestat(original, staged.stat())
    assert staged.read_bytes() == b"verified provider bytes"
    assert (
        tmp_path / "provider-retired" / claim.claim_id.hex / "audio.part"
    ).read_bytes() == b"partial"
    assert not tuple((tmp_path / "quarantine").iterdir())


def test_missing_both_directories_is_not_successful_retirement(tmp_path: Path) -> None:
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    execution = uuid4()
    claim = ProviderScratchClaim(execution, provider_scratch_id(execution))
    with pytest.raises(StorageOperationError):
        storage.retire_scratch(claim)
    assert not tuple((tmp_path / "provider-retired").iterdir())


def test_failed_acknowledgement_after_rename_replays_without_staging_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, abandoned, staged, _ = setup(tmp_path)
    claim = ProviderScratchClaim(
        abandoned.execution_id, provider_scratch_id(abandoned.execution_id)
    )
    rename = provider_staging._rename_directory

    def interrupted(source: Path, destination: Path) -> None:
        rename(source, destination)
        raise OSError(errno.EIO, "synthetic scratch interruption")

    with monkeypatch.context() as patch:
        patch.setattr(provider_staging, "_rename_directory", interrupted)
        with pytest.raises(StorageOperationError):
            storage.retire_scratch(claim)
    storage.retire_scratch(claim)
    assert staged.read_bytes() == b"verified provider bytes"
    assert (
        tmp_path / "provider-retired" / claim.claim_id.hex / "audio.part"
    ).read_bytes() == b"partial"


@pytest.mark.parametrize("_race", range(3))
def test_concurrent_scratch_replays_keep_one_directory(tmp_path: Path, _race: int) -> None:
    storage, abandoned, staged, workspace = setup(tmp_path)
    claim = ProviderScratchClaim(
        abandoned.execution_id, provider_scratch_id(abandoned.execution_id)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(storage.retire_scratch, claim) for _ in range(2)]
        for result in pending:
            result.result(timeout=5)
    assert not workspace.exists()
    assert staged.read_bytes() == b"verified provider bytes"
    assert len(tuple((tmp_path / "provider-retired").iterdir())) == 1


def test_conflicting_scratch_destination_preserves_both_directories(tmp_path: Path) -> None:
    storage, abandoned, staged, workspace = setup(tmp_path)
    claim = ProviderScratchClaim(
        abandoned.execution_id, provider_scratch_id(abandoned.execution_id)
    )
    target = tmp_path / "provider-retired" / claim.claim_id.hex
    target.mkdir()
    (target / "other").write_bytes(b"different ownership")
    with pytest.raises(ImmutableObjectConflictError):
        storage.retire_scratch(claim)
    assert (target / "other").read_bytes() == b"different ownership"
    assert (workspace / "audio.part").read_bytes() == b"partial"
    assert staged.read_bytes() == b"verified provider bytes"


@pytest.mark.parametrize("location", ["source", "destination"])
def test_scratch_root_symlink_never_grants_retirement(tmp_path: Path, location: str) -> None:
    FilesystemVaultStorage(tmp_path)
    storage = FilesystemProviderStorage(tmp_path)
    execution = uuid4()
    claim = ProviderScratchClaim(execution, provider_scratch_id(execution))
    foreign = tmp_path / "other-owner"
    foreign.mkdir()
    (foreign / "preserve").write_bytes(b"other owner")
    target = (
        tmp_path / "provider-work" / execution.hex
        if location == "source"
        else tmp_path / "provider-retired" / claim.claim_id.hex
    )
    try:
        target.symlink_to(foreign, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable; Linux proof required")
        raise
    with pytest.raises(StorageSafetyError):
        storage.retire_scratch(claim)
    assert (foreign / "preserve").read_bytes() == b"other owner"

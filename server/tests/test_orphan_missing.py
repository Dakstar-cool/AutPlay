"""Absence is a separate exact-path proof, never a successful retirement."""

import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import provider_maintenance_child_launch
from autplay.adapters.filesystem.maintenance_protocol import result_identity
from autplay.adapters.filesystem.orphan_object_retirement import FilesystemOrphanObjectRetirement
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_execution import ExecutionState
from autplay.domain.vault import StorageOperationError, StorageSafetyError
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from test_orphan_object_retirement import PAYLOAD, orphan_file


@pytest.mark.parametrize("present_path", ["source", "destination", "both", "neither"])
def test_absence_requires_both_exact_paths_missing(tmp_path: Path, present_path: str) -> None:
    claim, source = orphan_file(tmp_path)
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    if present_path in {"destination", "both"}:
        target.write_bytes(PAYLOAD)
    if present_path in {"destination", "neither"}:
        source.unlink()
    storage = FilesystemOrphanObjectRetirement(tmp_path)
    if present_path == "neither":
        storage.confirm_missing(claim)
        # Other claims' quarantine objects are not evidence for this claim.
        (target.parent / f"orphan-{uuid4().hex}").write_bytes(PAYLOAD)
        storage.confirm_missing(claim)
    else:
        with pytest.raises(StorageOperationError):
            storage.confirm_missing(claim)
    assert source.exists() == (present_path in {"source", "both"})
    assert target.exists() == (present_path in {"destination", "both"})


@pytest.mark.parametrize("location", ["source", "destination", "shard"])
def test_dangling_links_cannot_prove_absence(tmp_path: Path, location: str) -> None:
    claim, source = orphan_file(tmp_path)
    source.unlink()
    link = (
        source
        if location == "source"
        else tmp_path / "quarantine" / claim.quarantine_key.value
        if location == "destination"
        else source.parent
    )
    if location == "shard":
        link.rmdir()
    try:
        link.symlink_to(tmp_path / "absent", target_is_directory=location == "shard")
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(StorageSafetyError):
        FilesystemOrphanObjectRetirement(tmp_path).confirm_missing(claim)


@pytest.mark.parametrize("fault", ["permission", "io", "vanish", "replace"])
def test_errors_and_changed_ancestors_cannot_prove_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    claim, source = orphan_file(tmp_path)
    source.unlink()
    original = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        if path == source:
            if fault == "permission":
                raise PermissionError("synthetic denied lookup")
            if fault == "io":
                raise OSError("synthetic I/O failure")
            path.parent.rename(tmp_path / "displaced-shard")
            if fault == "replace":
                path.parent.mkdir()
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(StorageOperationError):
        FilesystemOrphanObjectRetirement(tmp_path).confirm_missing(claim)


def test_actual_missing_child_returns_its_check_action_after_go(tmp_path: Path) -> None:
    claim, source = orphan_file(tmp_path)
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.ORPHAN_MISSING, claim.storage_key
    )
    child = RetainedVaultProcess(
        ticket, ResourceIoDeadline(monotonic()), launch=provider_maintenance_child_launch
    )
    identity = child.spawn()
    try:
        # HELLO alone may neither inspect nor mutate the still-present source.
        assert source.read_bytes() == PAYLOAD
        source.unlink()
        child.allow_go(MaintenanceStatus(ticket, ExecutionState.RUNNING, identity))
        child.go({"version": 1, "root": str(tmp_path), **result_identity(ticket)})
        tag, payload = child.read_result()
        assert tag == b"R" and decode_document(payload) == result_identity(ticket)
        until = monotonic() + 5
        while (proof := child.exit_evidence(identity)) is None:
            assert monotonic() < until
            sleep(0.01)
        assert proof.exit_code == 0
        assert not source.exists()
        assert not (tmp_path / "quarantine" / claim.quarantine_key.value).exists()
    finally:
        child.request_stop()
        until = monotonic() + 5
        while child.exit_evidence(identity) is None:
            assert monotonic() < until
            sleep(0.01)
        child.close_pipes_after_worker_exit()

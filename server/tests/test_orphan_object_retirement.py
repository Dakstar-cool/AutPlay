"""Retirement preserves CAS bytes across crash replay and exact child commands."""

import hashlib
import io
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import provider_maintenance_child_launch
from autplay.adapters.filesystem.maintenance_protocol import result_identity
from autplay.adapters.filesystem.orphan_object_retirement import FilesystemOrphanObjectRetirement
from autplay.adapters.filesystem.provider_maintenance_child import execute_command
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.application.orphan_object_retirement import OrphanObjectClaim
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_execution import ExecutionState
from autplay.domain.vault import (
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
)
from autplay.runtime.resource_io_deadline import ResourceIoDeadline

PAYLOAD = b"retained orphan bytes"


def orphan_file(root: Path) -> tuple[OrphanObjectClaim, Path]:
    storage = FilesystemVaultStorage(root)
    staged = OpaqueStorageKey(uuid4().hex)
    storage.create_staging(staged)
    digest = Sha256Digest(hashlib.sha256(PAYLOAD).digest())
    storage.write_chunk(staged, offset=0, payload=PAYLOAD, payload_sha256=digest)
    key = storage.commit_staging(staged, storage.verify_staging(staged)).storage_key
    storage.cleanup_staging(staged)
    return OrphanObjectClaim(uuid4(), key), root / "objects" / key.value[:2] / key.value[
        2:4
    ] / key.value


def test_linked_before_unlink_failure_replays_without_losing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, source = orphan_file(tmp_path)
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    storage = FilesystemOrphanObjectRetirement(tmp_path)
    original = os.unlink

    def fail(path: object, **kwargs: object) -> None:
        raise OSError("synthetic interruption after durable link")

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", fail)
        with pytest.raises(StorageOperationError):
            storage.retire(claim)
    assert os.path.samestat(source.stat(), target.stat())
    assert os.unlink is original
    storage.retire(claim)
    storage.retire(claim)
    assert not source.exists() and target.read_bytes() == PAYLOAD


def test_different_destination_inode_never_authorizes_unlink(tmp_path: Path) -> None:
    claim, source = orphan_file(tmp_path)
    target = tmp_path / "quarantine" / claim.quarantine_key.value
    target.write_bytes(PAYLOAD)
    with pytest.raises(ImmutableObjectConflictError):
        FilesystemOrphanObjectRetirement(tmp_path).retire(claim)
    assert source.read_bytes() == target.read_bytes() == PAYLOAD
    assert not os.path.samestat(source.stat(), target.stat())


def test_missing_source_and_destination_is_not_success(tmp_path: Path) -> None:
    claim, source = orphan_file(tmp_path)
    source.unlink()
    with pytest.raises(StorageOperationError):
        FilesystemOrphanObjectRetirement(tmp_path).retire(claim)


@pytest.mark.parametrize("location", ["source", "destination", "shard"])
def test_symlink_cannot_authorize_retirement(tmp_path: Path, location: str) -> None:
    claim, source = orphan_file(tmp_path)
    external = tmp_path / "external"
    external.write_bytes(PAYLOAD)
    if location == "source":
        source.unlink()
        link, target = source, external
    elif location == "destination":
        link, target = tmp_path / "quarantine" / claim.quarantine_key.value, external
    else:
        link, target = source.parent, tmp_path / "displaced-shard"
        link.rename(target)
    try:
        link.symlink_to(target, target_is_directory=location == "shard")
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(StorageSafetyError):
        FilesystemOrphanObjectRetirement(tmp_path).retire(claim)
    assert external.read_bytes() == PAYLOAD
    assert source.read_bytes() == PAYLOAD


@pytest.mark.parametrize("fault", ["key", "claim", "provider", "root", "action", "version"])
def test_invalid_orphan_command_never_touches_storage(tmp_path: Path, fault: str) -> None:
    claim, source = orphan_file(tmp_path)
    document: dict[str, object] = {
        "version": 1,
        "action": "ORPHAN_OBJECT",
        "root": str(tmp_path),
        "storage_key": claim.storage_key.value,
        "claim_id": str(claim.claim_id),
    }
    if fault == "key":
        document["storage_key"] = "A" * 64
    elif fault == "claim":
        document["claim_id"] = claim.claim_id.hex
    elif fault == "provider":
        document["provider_execution_id"] = str(uuid4())
    elif fault == "root":
        document["root"] = "relative"
    elif fault == "action":
        document["action"] = "CLEANUP"
    else:
        document["version"] = True
    with pytest.raises(ValueError):
        execute_command(document, io.BytesIO())
    assert source.read_bytes() == PAYLOAD
    assert not (tmp_path / "quarantine" / claim.quarantine_key.value).exists()


def test_actual_orphan_child_waits_for_go_and_returns_exact_target(tmp_path: Path) -> None:
    claim, source = orphan_file(tmp_path)
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.ORPHAN_OBJECT, claim.storage_key
    )
    child = RetainedVaultProcess(
        ticket, ResourceIoDeadline(monotonic()), launch=provider_maintenance_child_launch
    )
    identity = child.spawn()
    try:
        assert source.read_bytes() == PAYLOAD
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
        assert (tmp_path / "quarantine" / claim.quarantine_key.value).read_bytes() == PAYLOAD
    finally:
        child.request_stop()
        until = monotonic() + 5
        while child.exit_evidence(identity) is None:
            assert monotonic() < until
            sleep(0.01)
        child.close_pipes_after_worker_exit()

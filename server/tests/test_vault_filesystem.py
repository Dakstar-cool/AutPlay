"""Host filesystem evidence for P06 safe staging, CAS, and ranged reads."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.domain.vault import (
    ByteRange,
    ChunkIntegrityError,
    ImmutableObjectConflictError,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    StorageSafetyError,
    UploadOffsetError,
    VaultLimits,
)


def _digest(payload: bytes) -> Sha256Digest:
    return Sha256Digest(hashlib.sha256(payload).digest())


def _storage(tmp_path: Path) -> FilesystemVaultStorage:
    return FilesystemVaultStorage(
        tmp_path,
        limits=VaultLimits(max_object_bytes=1024, max_chunk_bytes=64),
    )


def test_chunk_offset_hash_and_duplicate_retry_are_enforced(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    assert storage.available_bytes() > 0
    key = OpaqueStorageKey("session001")
    payload = b"hello"
    storage.create_staging(key)
    first = storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    assert first.next_offset == 5
    assert not first.idempotent
    retry = storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    assert retry.next_offset == 5
    assert retry.idempotent
    with pytest.raises(UploadOffsetError):
        storage.write_chunk(key, offset=3, payload=b"z", payload_sha256=_digest(b"z"))
    with pytest.raises(ChunkIntegrityError):
        storage.write_chunk(key, offset=5, payload=b"z", payload_sha256=_digest(b"x"))


def test_commit_is_immutable_deduplicated_and_reads_bounded(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first = OpaqueStorageKey("sessionfirst")
    second = OpaqueStorageKey("sessionsecond")
    payload = b"immutable\nbytes\n"
    for key in (first, second):
        storage.create_staging(key)
        storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    verified = storage.verify_staging(first)
    committed = storage.commit_staging(first, verified)
    assert not committed.already_present
    physical_object = (
        tmp_path
        / "objects"
        / committed.storage_key.value[:2]
        / committed.storage_key.value[2:4]
        / committed.storage_key.value
    )
    assert physical_object.read_bytes() == payload
    assert storage.verify_staging(first) == verified
    duplicate = storage.commit_staging(second, storage.verify_staging(second))
    assert duplicate.already_present
    storage.cleanup_staging(first)
    storage.cleanup_staging(second)
    assert storage.inventory().staging_keys == ()
    verified_at = datetime.now(UTC) + timedelta(seconds=1)
    reader = storage.open_range(
        committed.storage_key,
        ByteRange(2, 8),
        expected_size=len(payload),
        verified_at=verified_at,
    )
    assert b"".join(reader) == payload[2:9]
    with pytest.raises(StorageSafetyError):
        storage.open_range(
            committed.storage_key,
            ByteRange(0, len(payload)),
            expected_size=len(payload),
            verified_at=verified_at,
        )


def test_staging_rejects_symlink_and_special_non_regular_entries(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("linkentry")
    target = tmp_path / "target"
    target.write_bytes(b"data")
    staging = tmp_path / "staging" / key.value
    try:
        staging.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable on this host: {error}")
    with pytest.raises(StorageSafetyError):
        storage.verify_staging(key)
    directory_key = OpaqueStorageKey("directoryentry")
    (tmp_path / "staging" / directory_key.value).mkdir()
    with pytest.raises(StorageSafetyError):
        storage.verify_staging(directory_key)


def test_quarantine_inventory_and_cancelled_reader_leave_no_processing_file(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("toquarantine")
    quarantine_key = OpaqueStorageKey("quarantined001")
    payload = b"abcdefgh"
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    storage.quarantine(key, quarantine_key)
    inventory = storage.inventory()
    assert inventory.staging_keys == ()
    assert inventory.quarantine_keys == (quarantine_key,)

    fresh = OpaqueStorageKey("streamsource")
    storage.create_staging(fresh)
    storage.write_chunk(fresh, offset=0, payload=payload, payload_sha256=_digest(payload))
    commit = storage.commit_staging(fresh, storage.verify_staging(fresh))
    storage.cleanup_staging(fresh)
    reader = storage.open_range(
        commit.storage_key,
        ByteRange(0, 7),
        expected_size=len(payload),
        verified_at=datetime.now(UTC) + timedelta(seconds=1),
        cancelled=lambda: True,
    )
    assert list(reader) == []


def test_storage_write_error_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("writeerror")
    payload = b"abc"
    storage.create_staging(key)

    def fail_write(_descriptor: int, _payload: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", fail_write)
    with pytest.raises(StorageOperationError):
        storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))


def test_recovery_truncates_only_uncommitted_staging_suffix(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("recoverable")
    payload = b"abcdefgh"
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    storage.truncate_staging(key, 3)
    assert storage.verify_staging(key).byte_size == 3
    with pytest.raises(UploadOffsetError):
        storage.truncate_staging(key, 4)


def test_commit_refuses_same_hash_key_with_different_existing_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("conflicting")
    payload = b"expected"
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    verified = storage.verify_staging(key)
    object_path = tmp_path / "objects" / verified.sha256.hex[:2] / verified.sha256.hex[2:4]
    object_path.mkdir(parents=True)
    (object_path / verified.sha256.hex).write_bytes(b"different")
    with pytest.raises(ImmutableObjectConflictError):
        storage.commit_staging(key, verified)


def test_stream_refuses_same_size_corruption_before_returning_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    key = OpaqueStorageKey("streamintegrity")
    payload = b"trusted-bytes"
    storage.create_staging(key)
    storage.write_chunk(key, offset=0, payload=payload, payload_sha256=_digest(payload))
    committed = storage.commit_staging(key, storage.verify_staging(key))
    storage.cleanup_staging(key)
    object_path = (
        tmp_path
        / "objects"
        / committed.storage_key.value[:2]
        / committed.storage_key.value[2:4]
        / committed.storage_key.value
    )
    object_path.write_bytes(b"corrupt-bytes")
    assert object_path.stat().st_size == len(payload)
    with pytest.raises(StorageSafetyError):
        storage.open_range(
            committed.storage_key,
            ByteRange(0, len(payload) - 1),
            expected_size=len(payload),
            verified_at=datetime.now(UTC) - timedelta(seconds=1),
        )


def test_final_object_verification_and_quarantine_preserve_recoverable_bytes(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path)
    staging_key = OpaqueStorageKey("published")
    payload = b"recoverable final bytes"
    storage.create_staging(staging_key)
    storage.write_chunk(
        staging_key,
        offset=0,
        payload=payload,
        payload_sha256=_digest(payload),
    )
    verified = storage.verify_staging(staging_key)
    committed = storage.commit_staging(staging_key, verified)
    assert storage.verify_object(committed.storage_key) == verified
    quarantine_key = OpaqueStorageKey("orphan-final")
    storage.quarantine_object(committed.storage_key, quarantine_key)
    inventory = storage.inventory()
    assert committed.storage_key not in inventory.object_keys
    assert quarantine_key in inventory.quarantine_keys
    assert (tmp_path / "quarantine" / quarantine_key.value).read_bytes() == payload

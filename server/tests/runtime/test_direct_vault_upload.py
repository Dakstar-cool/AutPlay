from __future__ import annotations

import hashlib
from pathlib import Path

from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest
from autplay.entrypoints.composition import _DirectVaultChunkWriter


def test_direct_writer_lazily_creates_and_reconciles_staging(tmp_path: Path) -> None:
    storage = FilesystemVaultStorage(tmp_path)
    writer = _DirectVaultChunkWriter(storage, minimum_free_bytes=0)
    key = OpaqueStorageKey("device-upload")

    first = writer.append_reconciled_chunk(
        key,
        committed_size=0,
        expected_size=6,
        offset=0,
        payload=b"abc",
        payload_sha256=Sha256Digest(hashlib.sha256(b"abc").digest()),
    )
    second = writer.append_reconciled_chunk(
        key,
        committed_size=3,
        expected_size=6,
        offset=3,
        payload=b"def",
        payload_sha256=Sha256Digest(hashlib.sha256(b"def").digest()),
    )

    assert first.next_offset == 3
    assert second.next_offset == 6
    assert storage.staging_path_for_media(key).read_bytes() == b"abcdef"

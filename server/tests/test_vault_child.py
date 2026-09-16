"""Actual isolated-child/filesystem proof for the private GO and bounded READ protocol."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from autplay.adapters.child_process import vault_child_launch
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest


@dataclass
class Child:
    process: subprocess.Popen[bytes]
    source: BinaryIO
    destination: BinaryIO
    reader: ThreadPoolExecutor

    def read(self) -> tuple[bytes, bytes]:
        return self.pending().result(timeout=5)

    def pending(self) -> Future[tuple[bytes, bytes]]:
        return self.reader.submit(read_frame, self.destination)

    def send(self, tag: bytes, payload: bytes) -> None:
        write_frame(self.source, tag, payload)


@contextmanager
def child() -> Iterator[Child]:
    arguments, environment = vault_child_launch()
    process = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=os.name != "nt",
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    with ThreadPoolExecutor(max_workers=1) as reader:
        instance = Child(
            process, cast(BinaryIO, process.stdin), cast(BinaryIO, process.stdout), reader
        )
        try:
            tag, payload = instance.read()
            assert tag == b"H"
            greeting = decode_document(payload)
            assert greeting["pid"] == process.pid
            assert isinstance(greeting["nonce"], str) and len(greeting["nonce"]) == 32
            yield instance
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            assert process.stderr.read() == b""
            process.stderr.close()


def command(root: Path, key: str, *, kind: str = "UPLOAD") -> dict[str, object]:
    return {
        "version": 1,
        "kind": kind,
        "root": str(root),
        "key": key,
        "max_object_bytes": 1024 * 1024,
        "max_chunk_bytes": 1024 * 1024,
        "max_chunks": 4096,
        "io_block_bytes": 4,
    }


def upload_command(root: Path, key: str, payload: bytes, *, offset: int = 0) -> dict[str, object]:
    return {
        **command(root, key),
        "committed_size": offset,
        "offset": offset,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_no_storage_initialization_before_complete_go_and_payload(tmp_path: Path) -> None:
    root = tmp_path / "not-initialized"
    with child() as worker:
        assert not root.exists()
        worker.send(b"G", encode_document(upload_command(root, "staging-test", b"x")))
        pending = worker.pending()
        with pytest.raises(TimeoutError):
            pending.result(timeout=0.05)
        assert not root.exists()
        worker.process.kill()
        worker.process.wait(timeout=5)
        with pytest.raises(ChildProtocolError):
            pending.result(timeout=5)
    assert not root.exists()


def test_truncate_and_write_reconcile_only_the_uncommitted_suffix(tmp_path: Path) -> None:
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey("upload-test")
    storage.create_staging(key)
    original = b"kept--uncommitted"
    storage.write_chunk(
        key,
        offset=0,
        payload=original,
        payload_sha256=Sha256Digest(hashlib.sha256(original).digest()),
    )
    with child() as worker:
        worker.send(b"G", encode_document(upload_command(tmp_path, key.value, b"new", offset=4)))
        worker.send(b"D", b"new")
        tag, payload = worker.read()
        assert tag == b"R" and decode_document(payload) == {"next_offset": 7}
        assert worker.process.wait(timeout=5) == 0
    assert storage.staging_path_for_media(key).read_bytes() == b"keptnew"


def test_bad_hash_never_truncates_existing_staging(tmp_path: Path) -> None:
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey("upload-test")
    storage.create_staging(key)
    storage.write_chunk(
        key,
        offset=0,
        payload=b"keep",
        payload_sha256=Sha256Digest(hashlib.sha256(b"keep").digest()),
    )
    with child() as worker:
        worker.send(b"G", encode_document(upload_command(tmp_path, key.value, b"expected")))
        worker.send(b"D", b"different")
        tag, payload = worker.read()
        assert tag == b"E" and decode_document(payload) == {"code": "upload_chunk_hash_mismatch"}
        assert worker.process.wait(timeout=5) == 2
    assert storage.staging_path_for_media(key).read_bytes() == b"keep"


def test_range_is_pulled_one_block_at_a_time_and_preserves_requested_bytes(tmp_path: Path) -> None:
    storage = FilesystemVaultStorage(tmp_path)
    key = OpaqueStorageKey("range-test")
    storage.create_staging(key)
    payload = b"0123456789abcdef"
    storage.write_chunk(
        key,
        offset=0,
        payload=payload,
        payload_sha256=Sha256Digest(hashlib.sha256(payload).digest()),
    )
    committed = storage.commit_staging(key, storage.verify_staging(key))
    document = {
        **command(tmp_path, committed.storage_key.value, kind="STREAM"),
        "start": 2,
        "end": 10,
        "expected_size": len(payload),
        "verified_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    }
    with child() as worker:
        worker.send(b"G", encode_document(document))
        assert worker.read() == (b"O", b"")
        pending = worker.pending()
        with pytest.raises(TimeoutError):
            pending.result(timeout=0.05)
        worker.send(b"N", b"")
        assert pending.result(timeout=5) == (b"D", payload[2:6])
        worker.send(b"N", b"")
        assert worker.read() == (b"D", payload[6:10])
        worker.send(b"N", b"")
        assert worker.read() == (b"D", payload[10:11])
        worker.send(b"N", b"")
        assert worker.read() == (b"R", b"")
        assert worker.process.wait(timeout=5) == 0


@pytest.mark.parametrize(
    "fault", ["oversized", "duplicate", "wrong_key", "unknown", "wrong_offset"]
)
def test_malformed_command_is_bounded_sanitized_and_does_not_initialize_storage(
    tmp_path: Path,
    fault: str,
) -> None:
    root = tmp_path / "private-root-must-not-appear"
    with child() as worker:
        document = upload_command(root, "test", b"x")
        if fault == "oversized":
            worker.source.write(struct.pack("!cI", b"G", 8193))
            worker.source.flush()
        elif fault == "duplicate":
            worker.send(b"G", b'{"kind":"UPLOAD","kind":"STREAM"}')
        else:
            if fault == "wrong_key":
                document["key"] = "../elsewhere"
            elif fault == "wrong_offset":
                document["offset"] = 1
            else:
                document["extra"] = "unsupported"
            worker.send(b"G", encode_document(document))
        tag, payload = worker.read()
        assert tag == b"E"
        assert str(root).encode() not in payload
        assert worker.process.wait(timeout=5) == 2
    assert not root.exists()


def test_frames_reject_oversize_before_consuming_payload() -> None:
    stream = io.BytesIO(struct.pack("!cI", b"D", 1024 * 1024 + 1) + b"unread")
    with pytest.raises(ChildProtocolError):
        read_frame(stream)
    assert stream.read() == b"unread"

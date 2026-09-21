"""Upload chunk process work which cannot unwind its caller's row lock before exit."""

from __future__ import annotations

import threading
from pathlib import Path
from time import sleep

from autplay.domain.resource_execution import ExecutionStatus
from autplay.domain.vault import (
    ChunkIntegrityError,
    ChunkWriteResult,
    OpaqueStorageKey,
    Sha256Digest,
    StorageOperationError,
    UploadLimitError,
    UploadOffsetError,
    VaultCapacityError,
    VaultLimits,
)

from .vault_child import ChildProtocolError, decode_document
from .vault_process import RetainedVaultProcess


class ProcessVaultChunkWriter:
    """Called in the upload's retained worker, never on the ASGI event loop.

    The child and its RUNNING registration already exist before the upload row is
    locked. This method retains that lock across deadline/cancellation until exact
    process exit. Its caller then commits with the authority guard or rolls back;
    durable execution closure is acknowledged only after that transaction ends.
    """

    def __init__(
        self,
        child: RetainedVaultProcess,
        registered: ExecutionStatus,
        *,
        root: Path,
        limits: VaultLimits,
        minimum_free_bytes: int = 0,
    ) -> None:
        if type(minimum_free_bytes) is not int or not 0 <= minimum_free_bytes <= 1024**4:
            raise ValueError("invalid free-space reserve")
        child.allow_go(registered)
        self._child, self._registered = child, registered
        self._root, self._limits = root, limits
        self._minimum_free_bytes = minimum_free_bytes

    def append_reconciled_chunk(
        self,
        key: OpaqueStorageKey,
        *,
        committed_size: int,
        expected_size: int,
        offset: int,
        payload: bytes,
        payload_sha256: Sha256Digest,
    ) -> ChunkWriteResult:
        child = self._child
        done = threading.Event()
        watcher = threading.Thread(
            target=self._watch,
            args=(done,),
            name="vault-upload-stop",
            daemon=True,
        )
        watcher.start()
        try:
            try:
                child.go(
                    {
                        "version": 1,
                        "kind": "UPLOAD",
                        "root": str(self._root),
                        "key": key.value,
                        "max_object_bytes": self._limits.max_object_bytes,
                        "max_chunk_bytes": self._limits.max_chunk_bytes,
                        "max_chunks": self._limits.max_chunks,
                        "io_block_bytes": self._limits.io_block_bytes,
                        "committed_size": committed_size,
                        "expected_size": expected_size,
                        "minimum_free_bytes": self._minimum_free_bytes,
                        "offset": offset,
                        "payload_sha256": payload_sha256.hex,
                    },
                    payload,
                )
                tag, response = child.read_result()
                result = decode_document(response)
                if tag == b"E":
                    error = {
                        "upload_chunk_hash_mismatch": ChunkIntegrityError,
                        "upload_offset_mismatch": UploadOffsetError,
                        "upload_limit_exceeded": UploadLimitError,
                        "vault_capacity_low": VaultCapacityError,
                    }.get(str(result.get("code")), StorageOperationError)
                    raise error()
                if (
                    tag != b"R"
                    or set(result) != {"next_offset"}
                    or type(result["next_offset"]) is not int
                    or result["next_offset"] != offset + len(payload)
                ):
                    raise ChildProtocolError()
            except BaseException:
                child.deadline.freeze_error()
                child.request_stop()
                raise
            finally:
                # This is intentionally not a bounded join. HTTP has its separate
                # deadline; a surviving writer must keep this upload row locked.
                while child.exit_evidence(self._registered.child) is None:
                    if child.deadline.stopped():
                        child.request_stop()
                    sleep(0.05)
        finally:
            done.set()
            watcher.join(timeout=1)
        child.deadline.check()
        proof = child.exit_evidence(self._registered.child)
        if proof is None or proof.exit_code != 0:
            raise StorageOperationError()
        return ChunkWriteResult(next_offset=offset + len(payload), idempotent=False)

    def _watch(self, done: threading.Event) -> None:
        while not done.wait(0.2):
            if self._child.deadline.stopped():
                self._child.request_stop()

"""One isolated Vault command, entered only after the parent durably permits GO.

The private binary pipe carries no database connection or application credentials.
This child never starts another process. The parent owns authorization, process
identity, deadlines, exit verification and the upload database transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, cast
from uuid import uuid4

from autplay.domain.vault import (
    ByteRange,
    ChunkIntegrityError,
    OpaqueStorageKey,
    Sha256Digest,
    VaultCapacityError,
    VaultError,
    VaultLimits,
)

from .vault import FilesystemVaultStorage

MAX_COMMAND_BYTES = 8192
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_FRAME_BYTES = MAX_PAYLOAD_BYTES
_FRAME_HEADER = struct.Struct("!cI")


class ChildProtocolError(ValueError):
    """A bounded private protocol failure with no command or path in its message."""

    def __init__(self) -> None:
        super().__init__("vault_child_protocol_invalid")


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    payload = bytearray()
    while len(payload) < length:
        part = stream.read(length - len(payload))
        if not part:
            raise ChildProtocolError()
        payload.extend(part)
    return bytes(payload)


def read_frame(stream: BinaryIO, *, maximum: int = MAX_FRAME_BYTES) -> tuple[bytes, bytes]:
    tag, size = _FRAME_HEADER.unpack(_read_exact(stream, _FRAME_HEADER.size))
    if size > maximum:
        raise ChildProtocolError()
    return tag, _read_exact(stream, size)


def write_frame(stream: BinaryIO, tag: bytes, payload: bytes) -> None:
    if len(tag) != 1 or len(payload) > MAX_FRAME_BYTES:
        raise ChildProtocolError()
    data = _FRAME_HEADER.pack(tag, len(payload)) + payload
    sent = 0
    while sent < len(data):
        count = stream.write(data[sent:])
        if count is None or count < 1:
            raise ChildProtocolError()
        sent += count
    stream.flush()


def encode_document(document: dict[str, object], *, maximum: int = MAX_COMMAND_BYTES) -> bytes:
    result = json.dumps(document, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    if not 1 <= maximum <= MAX_FRAME_BYTES or len(result) > maximum:
        raise ChildProtocolError()
    return result


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ChildProtocolError()
        result[key] = value
    return result


def decode_document(payload: bytes, *, maximum: int = MAX_COMMAND_BYTES) -> dict[str, object]:
    if not 1 <= maximum <= MAX_FRAME_BYTES or len(payload) > maximum:
        raise ChildProtocolError()
    try:
        value = json.loads(payload, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ChildProtocolError() from error
    if not isinstance(value, dict):
        raise ChildProtocolError()
    return cast(dict[str, object], value)


def _integer(document: dict[str, object], key: str, *, maximum: int) -> int:
    value = document[key]
    if type(value) is not int or not 0 <= value <= maximum:
        raise ChildProtocolError()
    return value


def _string(document: dict[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str):
        raise ChildProtocolError()
    return value


def execute_command(command: dict[str, object], source: BinaryIO, destination: BinaryIO) -> None:
    """Initialize storage only after a complete, bounded GO command was received."""
    common = {
        "version",
        "kind",
        "root",
        "key",
        "max_object_bytes",
        "max_chunk_bytes",
        "max_chunks",
        "io_block_bytes",
    }
    kind = _string(command, "kind")
    extra = (
        {"committed_size", "expected_size", "minimum_free_bytes", "offset", "payload_sha256"}
        if kind == "UPLOAD"
        else {"start", "end", "expected_size", "verified_at"}
    )
    if (
        set(command) != common | extra
        or type(command["version"]) is not int
        or command["version"] != 1
    ):
        raise ChildProtocolError()
    root = Path(_string(command, "root"))
    if not root.is_absolute():
        raise ChildProtocolError()
    key = OpaqueStorageKey(_string(command, "key"))
    max_object = _integer(command, "max_object_bytes", maximum=4 * 1024**3)
    block_bytes = _integer(command, "io_block_bytes", maximum=MAX_PAYLOAD_BYTES)
    limits = VaultLimits(
        max_object_bytes=max_object,
        max_chunk_bytes=_integer(command, "max_chunk_bytes", maximum=MAX_PAYLOAD_BYTES),
        max_chunks=_integer(command, "max_chunks", maximum=2**53 - 1),
        io_block_bytes=block_bytes,
    )
    if kind == "UPLOAD":
        offset = _integer(command, "offset", maximum=max_object)
        committed_size = _integer(command, "committed_size", maximum=max_object)
        expected_size = _integer(command, "expected_size", maximum=max_object)
        minimum_free = _integer(command, "minimum_free_bytes", maximum=1024**4)
        if offset != committed_size or not 0 <= committed_size < expected_size:
            raise ChildProtocolError()
        declared = Sha256Digest(bytes.fromhex(_string(command, "payload_sha256")))
        tag, payload = read_frame(source, maximum=limits.max_chunk_bytes)
        if tag != b"D" or not payload or offset + len(payload) > expected_size:
            raise ChildProtocolError()
        if hashlib.sha256(payload).digest() != declared.value:
            raise ChunkIntegrityError()
        storage = FilesystemVaultStorage(root, limits=limits)
        if storage.available_bytes() - (expected_size - committed_size) < minimum_free:
            raise VaultCapacityError()
        # These operations are inseparable under the parent's upload row lock.
        storage.prepare_upload_staging(key, committed_size)
        result = storage.write_chunk(key, offset=offset, payload=payload, payload_sha256=declared)
        write_frame(destination, b"R", encode_document({"next_offset": result.next_offset}))
    elif kind == "STREAM":
        selected = ByteRange(
            _integer(command, "start", maximum=max_object),
            _integer(command, "end", maximum=max_object),
        )
        expected = _integer(command, "expected_size", maximum=max_object)
        verified_at = datetime.fromisoformat(_string(command, "verified_at"))
        if verified_at.tzinfo is None:
            raise ChildProtocolError()
        storage = FilesystemVaultStorage(root, limits=limits)
        reader = storage.open_range(key, selected, expected_size=expected, verified_at=verified_at)
        try:
            write_frame(destination, b"O", b"")
            iterator = iter(reader)
            while True:
                tag, payload = read_frame(source, maximum=0)
                if tag != b"N" or payload:
                    raise ChildProtocolError()
                block = next(iterator, None)
                if block is None:
                    write_frame(destination, b"R", b"")
                    break
                write_frame(destination, b"D", block)
        finally:
            reader.close()
    else:
        raise ChildProtocolError()


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=MAX_COMMAND_BYTES)
        if tag != b"G":
            raise ChildProtocolError()
        execute_command(decode_document(payload), source, destination)
        return 0
    except (VaultError, ValueError, TypeError, KeyError, OverflowError, OSError) as error:
        code = error.code if isinstance(error, VaultError) else "vault_storage_unavailable"
        with suppress(ValueError, OSError):
            write_frame(destination, b"E", encode_document({"code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded metadata RPC packets; large artwork travels in fixed-size binary frames."""

from typing import BinaryIO

from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)

MAX_METADATA_BYTES = 4 * 1024 * 1024
METADATA_BLOCK_BYTES = 64 * 1024


def write_packet(
    stream: BinaryIO, tag: bytes, document: dict[str, object], payload: bytes | None = None
) -> None:
    if payload is not None and len(payload) > MAX_METADATA_BYTES:
        raise ChildProtocolError()
    write_frame(
        stream,
        tag,
        encode_document(
            {
                "document": document,
                "size": len(payload) if payload is not None else None,
            },
            maximum=65536,
        ),
    )
    if payload is not None:
        for start in range(0, len(payload), METADATA_BLOCK_BYTES):
            write_frame(stream, b"D", payload[start : start + METADATA_BLOCK_BYTES])


def read_packet(stream: BinaryIO, envelope: bytes) -> tuple[dict[str, object], bytes | None]:
    value = decode_document(envelope, maximum=65536)
    if set(value) != {"document", "size"} or not isinstance(value["document"], dict):
        raise ChildProtocolError()
    size = value["size"]
    if size is None:
        return value["document"], None
    if type(size) is not int or not 0 <= size <= MAX_METADATA_BYTES:
        raise ChildProtocolError()
    payload = bytearray()
    while len(payload) < size:
        tag, part = read_frame(stream, maximum=min(METADATA_BLOCK_BYTES, size - len(payload)))
        if tag != b"D" or not part:
            raise ChildProtocolError()
        payload.extend(part)
    return value["document"], bytes(payload)

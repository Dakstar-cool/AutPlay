"""One JSON response, bounded before reading its binary frames or parsing JSON."""

from typing import BinaryIO

from .vault_child import ChildProtocolError, decode_document, read_frame

MAX_CATALOG_BYTES = 1024 * 1024


def read_catalog_packet(
    source: BinaryIO, envelope: bytes
) -> tuple[dict[str, object], bytes | None]:
    value = decode_document(envelope, maximum=65536)
    if set(value) != {"document", "size"} or not isinstance(value["document"], dict):
        raise ChildProtocolError()
    size = value["size"]
    if size is None:
        return value["document"], None
    if type(size) is not int or not 0 <= size <= MAX_CATALOG_BYTES:
        raise ChildProtocolError()
    payload = bytearray()
    while len(payload) < size:
        tag, part = read_frame(source, maximum=min(65536, size - len(payload)))
        if tag != b"D" or not part:
            raise ChildProtocolError()
        payload.extend(part)
    return value["document"], bytes(payload)

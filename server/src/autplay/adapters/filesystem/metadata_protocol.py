"""Bounded metadata RPC packets; large artwork travels in fixed-size binary frames."""

from dataclasses import asdict
from typing import BinaryIO

from autplay.domain.track_metadata import (
    MetadataQuery,
    parse_source_metadata,
    partial_date,
    source_metadata_document,
    validate_fields,
)

from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)

MAX_METADATA_BYTES = 4 * 1024 * 1024
METADATA_BLOCK_BYTES = 64 * 1024


def metadata_query_document(query: MetadataQuery) -> dict[str, object]:
    """Canonical bounded request serialization; native URLs remain inert evidence."""
    document = asdict(query)
    document["source_metadata"] = (
        source_metadata_document(query.source_metadata) if query.source_metadata else None
    )
    parse_metadata_query(document)
    return document


def parse_metadata_query(raw: object) -> MetadataQuery:
    if not isinstance(raw, dict) or set(raw) != {
        "title",
        "artist",
        "album",
        "duration_ms",
        "recording_mbid",
        "release_mbid",
        "release_date",
        "track_number",
        "disc_number",
        "source_metadata",
    } | (set(raw) & {"release_track_mbid"}):
        raise ChildProtocolError()
    try:
        encode_document(raw, maximum=65536)
        for key in ("title", "artist"):
            value = raw[key]
            if not isinstance(value, str) or len(value) > 500:
                raise ChildProtocolError()
            if value:
                validate_fields({key: value})
        validate_fields({key: raw[key] for key in ("album", "track_number", "disc_number")})
        for source, target in (
            ("recording_mbid", "mb_recording_id"),
            ("release_mbid", "mb_release_id"),
        ):
            validate_fields({target: raw[source]})
        validate_fields({"mb_recording_id": raw.get("release_track_mbid")})
        if raw["release_date"] is not None:
            partial_date(raw["release_date"])
        duration = raw["duration_ms"]
        if duration is not None and (type(duration) is not int or not 0 < duration <= 3600000):
            raise ChildProtocolError()
        source_metadata = (
            parse_source_metadata(raw["source_metadata"])
            if raw["source_metadata"] is not None
            else None
        )
        return MetadataQuery(**{**raw, "source_metadata": source_metadata})
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ChildProtocolError() from error


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

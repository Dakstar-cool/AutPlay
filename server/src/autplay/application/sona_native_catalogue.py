"""P11 v2 original catalogue and the exact production serving window."""

import base64
import json
import zlib
from collections.abc import Buffer, Sequence
from hashlib import sha256
from io import BytesIO
from itertools import pairwise
from uuid import UUID

import rfc8785

from autplay.application.recommendations import (
    RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
    recommendation_snapshot_limit,
    recommendation_snapshot_track_document,
)
from autplay.domain.recommendations import JsonValue, SnapshotTrack

COLLECTION_V2 = "AUTPLAY_P11_NATIVE_COLLECTION_V2"
COLLECTION_V3 = "AUTPLAY_P11_NATIVE_COLLECTION_V3"
EXTENDED_CATALOGUE_TRACKS = 20_000
MAX_CATALOGUE_BYTES = 32 * 1024 * 1024


class CatalogueSizeExceeded(ValueError):
    pass


class _BoundedCanonicalBuffer(BytesIO):
    def write(self, data: Buffer, /) -> int:
        if self.tell() + memoryview(data).nbytes > MAX_CATALOGUE_BYTES:
            raise CatalogueSizeExceeded("CATALOGUE_BYTE_LIMIT")
        return super().write(data)


def pack_extended_catalogue(document: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], str]:
    with _BoundedCanonicalBuffer() as stream:
        rfc8785.dump(document, stream)
        with stream.getbuffer() as canonical:
            digest = sha256(canonical).hexdigest()
            encoded = base64.b64encode(zlib.compress(canonical, level=1)).decode("ascii")
            return {
                "codec": "zlib-base64",
                "canonical_bytes": len(canonical),
                "data": encoded,
            }, digest


def unpack_extended_catalogue(value: object, expected_sha256: str) -> object:
    if not isinstance(value, dict) or set(value) != {"codec", "canonical_bytes", "data"}:
        raise ValueError("CATALOGUE_ENCODING")
    size, encoded = value["canonical_bytes"], value["data"]
    if (
        value["codec"] != "zlib-base64"
        or type(size) is not int
        or not 0 < size <= MAX_CATALOGUE_BYTES
        or not isinstance(encoded, str)
        or not 0 < len(encoded) <= 16 * 1024 * 1024
    ):
        raise ValueError("CATALOGUE_ENCODING")
    try:
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, size + 1)
    except (ValueError, zlib.error) as exc:
        raise ValueError("CATALOGUE_ENCODING") from exc
    if (
        len(raw) != size
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
        or base64.b64encode(compressed).decode("ascii") != encoded
        or sha256(raw).hexdigest() != expected_sha256
    ):
        raise ValueError("CATALOGUE_HASH_MISMATCH")
    return json.loads(raw)


def catalogue_document(
    owner: UUID, watermark: int, tracks: Sequence[SnapshotTrack], *, extended: bool = False
) -> dict[str, JsonValue]:
    return {
        "schema": "P11_V2_FULL_CATALOGUE_V2" if extended else "P11_V2_FULL_CATALOGUE_V1",
        "user_id": str(owner),
        "interaction_watermark": watermark,
        "max_tracks": EXTENDED_CATALOGUE_TRACKS if extended else RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
        "tracks": [recommendation_snapshot_track_document(track) for track in tracks],
    }


def catalogue_matches_served(
    tracks: Sequence[SnapshotTrack], served: tuple[SnapshotTrack, ...]
) -> bool:
    if any(left.recording_id >= right.recording_id for left, right in pairwise(tracks)):
        return False
    recent = sorted(tracks, key=lambda t: (-t.added_at_ms, t.recording_id))
    window = sorted(recent[: recommendation_snapshot_limit("2")], key=lambda t: t.recording_id)
    return tuple(window) == served

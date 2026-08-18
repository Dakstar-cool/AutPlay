"""Authorization-independent HTTP byte-range planning for Vault streams."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autplay.domain.vault import ByteRange, OpaqueStorageKey, Sha256Digest


@dataclass(frozen=True, slots=True)
class AuthorizedStream:
    """A DB-authorized immutable replica selected before opening a file."""

    storage_key: OpaqueStorageKey
    sha256: Sha256Digest
    byte_size: int
    media_type: str
    verified_at: datetime

    @property
    def etag(self) -> str:
        return f'"sha256-{self.sha256.hex}"'


@dataclass(frozen=True, slots=True)
class StreamPlan:
    """Pure HTTP response plan; 416 has no byte range."""

    status_code: int
    byte_range: ByteRange | None


def parse_single_range(
    range_header: str | None, *, if_range: str | None, etag: str, byte_size: int
) -> StreamPlan:
    """Parse a single RFC 7233 bytes range with conservative If-Range policy."""

    if byte_size < 1:
        raise ValueError("byte_size must be positive")
    full = StreamPlan(200, ByteRange(0, byte_size - 1))
    if range_header is None or (if_range is not None and if_range != etag):
        return full
    if not range_header.startswith("bytes=") or "," in range_header:
        return StreamPlan(416, None)
    spec = range_header[6:].strip()
    if "-" not in spec:
        return StreamPlan(416, None)
    left, right = spec.split("-", 1)
    try:
        if left == "":
            suffix = int(right)
            if suffix <= 0:
                return StreamPlan(416, None)
            return StreamPlan(206, ByteRange(max(0, byte_size - suffix), byte_size - 1))
        start = int(left)
        if start < 0 or start >= byte_size:
            return StreamPlan(416, None)
        end = byte_size - 1 if right == "" else min(int(right), byte_size - 1)
        if end < start:
            return StreamPlan(416, None)
        return StreamPlan(206, ByteRange(start, end))
    except ValueError:
        return StreamPlan(416, None)


__all__ = ("AuthorizedStream", "StreamPlan", "parse_single_range")

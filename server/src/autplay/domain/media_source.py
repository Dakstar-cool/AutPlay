"""M1 closed wire-1 relationship values; no media grant or signed document."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import ClassVar
from uuid import UUID

MAX_INTEGER = 9_007_199_254_740_991
MAX_RESPONSE_BYTES = 4_096
MAX_OBJECT_BYTES = 2_147_483_648
NAMESPACE = "autplay.media-source"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX = re.compile(r"[0-9a-f]{64}")


class MediaSourceError(RuntimeError):
    """Fixed public failures without source identifiers or exception echo."""

    _STATUSES: ClassVar[dict[str, int]] = {
        "request_validation_failed": 422,
        "authentication_required": 401,
        "session_revoked": 401,
        "not_found": 404,
        "media_source_unavailable": 503,
    }

    def __init__(self, code: str = "media_source_unavailable") -> None:
        self.status_code = self._STATUSES[code]
        self.code = code
        super().__init__(code)


def canonical_uuid(value: object) -> UUID:
    if type(value) is not str or _UUID.fullmatch(value) is None:
        raise ValueError("invalid UUID")
    return UUID(value)


@dataclass(frozen=True, slots=True)
class MediaSourceSnapshotRequestV1:
    user_track_ref_id: str

    def __post_init__(self) -> None:
        try:
            canonical_uuid(self.user_track_ref_id)
        except ValueError:
            raise MediaSourceError("request_validation_failed") from None


@dataclass(frozen=True, slots=True)
class MediaSourceSnapshotV1:
    namespace: str
    wire_version: int
    server_instance_id: str
    identity_epoch: int
    identity_thumbprint_sha256: str
    capability_revision: int
    user_id: str
    device_id: str
    user_track_ref_id: str
    user_track_ref_row_version: int
    recording_id: str
    audio_variant_id: str
    audio_variant_row_version: int
    sha256: str
    total_bytes: int
    media_type: str
    codec: str
    container: str

    def __post_init__(self) -> None:
        if self.namespace != NAMESPACE or type(self.namespace) is not str:
            raise ValueError("invalid namespace")
        if type(self.wire_version) is not int or self.wire_version != 1:
            raise ValueError("invalid wire version")
        for identifier in (
            self.server_instance_id,
            self.user_id,
            self.device_id,
            self.user_track_ref_id,
            self.recording_id,
            self.audio_variant_id,
        ):
            canonical_uuid(identifier)
        for revision in (
            self.identity_epoch,
            self.capability_revision,
            self.user_track_ref_row_version,
            self.audio_variant_row_version,
        ):
            if type(revision) is not int or not 1 <= revision <= MAX_INTEGER:
                raise ValueError("invalid positive integer")
        for value in (self.identity_thumbprint_sha256, self.sha256):
            if type(value) is not str or _HEX.fullmatch(value) is None:
                raise ValueError("invalid digest encoding")
        if type(self.total_bytes) is not int or not 1 <= self.total_bytes <= MAX_OBJECT_BYTES:
            raise ValueError("invalid size")
        for value, maximum in ((self.media_type, 200), (self.codec, 100), (self.container, 100)):
            if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= maximum:
                raise ValueError("invalid text bound")

    def encode(self) -> bytes:
        raw = json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response bound")
        return raw


def parse_snapshot(raw: bytes) -> MediaSourceSnapshotV1:
    """Strict client/fixture decoder: duplicate keys and numeric coercion refuse."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RESPONSE_BYTES:
        raise ValueError("response bound")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate member")
            result[key] = value
        return result

    def integer(token: str) -> int:
        if re.fullmatch(r"[0-9]{1,16}", token) is None:
            raise ValueError("integer token")
        return int(token)

    def reject(token: str) -> object:
        raise ValueError("noninteger token")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_int=integer,
            parse_float=reject,
            parse_constant=reject,
        )
        if type(value) is not dict or any(type(item) not in (str, int) for item in value.values()):
            raise ValueError("flat object required")
        return MediaSourceSnapshotV1(**value)
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid snapshot") from error

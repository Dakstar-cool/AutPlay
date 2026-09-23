"""Canonical decoded-sample timeline identity, separate from Face contract v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

import rfc8785

from autplay.domain.face_presentation_map import SourcePresentationMapV1

FACE_V2_SEMANTIC_KEY_DOMAIN = b"autplay.face.semantic-key.v2\0"
FACE_V2_TIMEBASE = "DECODED_SAMPLE_INDEX_V2"
_HEX = frozenset("0123456789abcdef")
_FIELDS = {
    "schema_version",
    "timeline_codec_version",
    "source_timebase",
    "recording_id",
    "audio_variant_id",
    "source_sha256",
    "decoded_sample_rate",
    "decoded_sample_count",
    "source_presentation_map_sha256",
    "embedding_model_id",
    "embedding_manifest_sha256",
    "semantic_interpreter_id",
    "interpreter_manifest_sha256",
    "preprocessing_sha256",
    "calibration_sha256",
    "execution_profile_sha256",
}


class FaceV2IdentityError(ValueError):
    """A candidate identity cannot select, publish or decode a v2 timeline."""


@dataclass(frozen=True, slots=True)
class FaceTimelineIdentityV2:
    recording_id: UUID
    audio_variant_id: UUID
    source_sha256: str
    decoded_sample_rate: int
    decoded_sample_count: int
    source_presentation_map_sha256: str
    embedding_model_id: UUID
    embedding_manifest_sha256: str
    semantic_interpreter_id: UUID
    interpreter_manifest_sha256: str
    preprocessing_sha256: str
    calibration_sha256: str
    execution_profile_sha256: str

    def __post_init__(self) -> None:
        if any(
            type(identifier) is not UUID
            for identifier in (
                self.recording_id,
                self.audio_variant_id,
                self.embedding_model_id,
                self.semantic_interpreter_id,
            )
        ):
            raise FaceV2IdentityError("invalid Face v2 UUID")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in _HEX for character in value)
            for value in (
                self.source_sha256,
                self.source_presentation_map_sha256,
                self.embedding_manifest_sha256,
                self.interpreter_manifest_sha256,
                self.preprocessing_sha256,
                self.calibration_sha256,
                self.execution_profile_sha256,
            )
        ):
            raise FaceV2IdentityError("invalid Face v2 digest")
        if (
            type(self.decoded_sample_rate) is not int
            or not 8_000 <= self.decoded_sample_rate <= 384_000
            or type(self.decoded_sample_count) is not int
            or not 1 <= self.decoded_sample_count <= 33_177_600_000
        ):
            raise FaceV2IdentityError("invalid Face v2 decoded sample identity")

    def bind_map(self, presentation_map: SourcePresentationMapV1) -> None:
        if (
            self.source_sha256 != presentation_map.encoded_source_sha256
            or self.decoded_sample_rate != presentation_map.decoded_sample_rate
            or self.decoded_sample_count != presentation_map.decoded_sample_count
            or self.source_presentation_map_sha256 != presentation_map.sha256()
        ):
            raise FaceV2IdentityError("Face v2 presentation map identity mismatch")

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "timeline_codec_version": 2,
            "source_timebase": FACE_V2_TIMEBASE,
            "recording_id": str(self.recording_id),
            "audio_variant_id": str(self.audio_variant_id),
            "source_sha256": self.source_sha256,
            "decoded_sample_rate": self.decoded_sample_rate,
            "decoded_sample_count": self.decoded_sample_count,
            "source_presentation_map_sha256": self.source_presentation_map_sha256,
            "embedding_model_id": str(self.embedding_model_id),
            "embedding_manifest_sha256": self.embedding_manifest_sha256,
            "semantic_interpreter_id": str(self.semantic_interpreter_id),
            "interpreter_manifest_sha256": self.interpreter_manifest_sha256,
            "preprocessing_sha256": self.preprocessing_sha256,
            "calibration_sha256": self.calibration_sha256,
            "execution_profile_sha256": self.execution_profile_sha256,
        }

    def canonical_bytes(self) -> bytes:
        return rfc8785.dumps(self.document())

    def semantic_key(self) -> str:
        return sha256(FACE_V2_SEMANTIC_KEY_DOMAIN + self.canonical_bytes()).hexdigest()


def parse_face_v2_identity(
    raw: bytes, presentation_map: SourcePresentationMapV1
) -> FaceTimelineIdentityV2:
    """Decode only canonical bytes and bind the exact retained presentation map."""

    if type(raw) is not bytes or not 1 <= len(raw) <= 4096:
        raise FaceV2IdentityError("invalid Face v2 identity byte size")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FaceV2IdentityError("duplicate Face v2 identity field")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != _FIELDS:
            raise FaceV2IdentityError("invalid Face v2 identity fields")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 2
            or type(value["timeline_codec_version"]) is not int
            or value["timeline_codec_version"] != 2
            or value["source_timebase"] != FACE_V2_TIMEBASE
        ):
            raise FaceV2IdentityError("invalid Face v2 identity version")
        identifiers = (
            "recording_id",
            "audio_variant_id",
            "embedding_model_id",
            "semantic_interpreter_id",
        )
        for field in identifiers:
            identifier = UUID(value[field])
            if str(identifier) != value[field]:
                raise FaceV2IdentityError("noncanonical Face v2 UUID")
            value[field] = identifier
        identity = FaceTimelineIdentityV2(
            **{
                key: item
                for key, item in value.items()
                if key not in {"schema_version", "timeline_codec_version", "source_timebase"}
            }
        )
        if identity.canonical_bytes() != raw:
            raise FaceV2IdentityError("noncanonical Face v2 identity bytes")
        identity.bind_map(presentation_map)
        return identity
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        KeyError,
        OverflowError,
        ValueError,
    ) as error:
        if isinstance(error, FaceV2IdentityError):
            raise
        raise FaceV2IdentityError("invalid Face v2 identity document") from error

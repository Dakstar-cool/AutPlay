"""Exact source-presentation to decoded-sample mapping for Face contract v2.

The immutable v1 Face millisecond contract is deliberately separate. A v2
timeline uses decoded sample indices and this validated, hashed map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

import rfc8785

MAX_PRESENTATION_US = 86_400_000_000
MAX_SAMPLE_COUNT = 86_400 * 384_000
MAX_SEGMENTS = 256
MAX_MAP_BYTES = 65_536
_IDENTITY = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_HEX = frozenset("0123456789abcdef")
_MAP_FIELDS = {
    "schema_version",
    "kind",
    "encoded_source_sha256",
    "decoded_sample_rate",
    "decoded_sample_count",
    "decoder_id",
    "decoder_version",
    "probe_id",
    "probe_version",
    "leading_trim_samples",
    "trailing_trim_samples",
    "encoder_delay_samples",
    "encoder_padding_samples",
    "segments",
}


class SourcePresentationMapError(ValueError):
    """An invalid map is never usable as playback or publication evidence."""


@dataclass(frozen=True, slots=True)
class PresentationSegmentV1:
    presentation_start_us: int
    presentation_end_us: int
    source_start_sample: int


@dataclass(frozen=True, slots=True)
class SourcePresentationMapV1:
    encoded_source_sha256: str
    decoded_sample_rate: int
    decoded_sample_count: int
    decoder_id: str
    decoder_version: str
    probe_id: str
    probe_version: str
    leading_trim_samples: int
    trailing_trim_samples: int
    encoder_delay_samples: int | None
    encoder_padding_samples: int | None
    segments: tuple[PresentationSegmentV1, ...]

    def __post_init__(self) -> None:
        if (
            len(self.encoded_source_sha256) != 64
            or any(char not in _HEX for char in self.encoded_source_sha256)
            or type(self.decoded_sample_rate) is not int
            or not 8_000 <= self.decoded_sample_rate <= 384_000
            or type(self.decoded_sample_count) is not int
            or not 1 <= self.decoded_sample_count <= MAX_SAMPLE_COUNT
        ):
            raise SourcePresentationMapError("invalid decoded source identity")
        if any(
            not isinstance(value, str) or not _IDENTITY.fullmatch(value)
            for value in (self.decoder_id, self.decoder_version, self.probe_id, self.probe_version)
        ):
            raise SourcePresentationMapError("invalid decoder or probe identity")
        trims = (self.leading_trim_samples, self.trailing_trim_samples)
        if (
            any(type(value) is not int or value < 0 for value in trims)
            or sum(trims) > self.decoded_sample_count
        ):
            raise SourcePresentationMapError("invalid decoded-source trim")
        for observation in (self.encoder_delay_samples, self.encoder_padding_samples):
            if observation is not None and (type(observation) is not int or observation < 0):
                raise SourcePresentationMapError("invalid encoder delay or padding")
        if not isinstance(self.segments, tuple) or not 1 <= len(self.segments) <= MAX_SEGMENTS:
            raise SourcePresentationMapError("invalid presentation segment count")
        previous_end = -1
        for segment in self.segments:
            if not isinstance(segment, PresentationSegmentV1) or any(
                type(value) is not int
                for value in (
                    segment.presentation_start_us,
                    segment.presentation_end_us,
                    segment.source_start_sample,
                )
            ):
                raise SourcePresentationMapError("invalid presentation segment")
            start, end, source = (
                segment.presentation_start_us,
                segment.presentation_end_us,
                segment.source_start_sample,
            )
            last_sample = source + (end - start - 1) * self.decoded_sample_rate // 1_000_000
            if (
                start < 0
                or start < previous_end
                or not start < end <= MAX_PRESENTATION_US
                or source < 0
                or last_sample >= self.decoded_sample_count
            ):
                raise SourcePresentationMapError("presentation segment escapes decoded source")
            previous_end = end

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "SOURCE_PRESENTATION_MAP_V1",
            "encoded_source_sha256": self.encoded_source_sha256,
            "decoded_sample_rate": self.decoded_sample_rate,
            "decoded_sample_count": self.decoded_sample_count,
            "decoder_id": self.decoder_id,
            "decoder_version": self.decoder_version,
            "probe_id": self.probe_id,
            "probe_version": self.probe_version,
            "leading_trim_samples": self.leading_trim_samples,
            "trailing_trim_samples": self.trailing_trim_samples,
            "encoder_delay_samples": self.encoder_delay_samples,
            "encoder_padding_samples": self.encoder_padding_samples,
            "segments": [
                {
                    "presentation_start_us": segment.presentation_start_us,
                    "presentation_end_us": segment.presentation_end_us,
                    "source_start_sample": segment.source_start_sample,
                }
                for segment in self.segments
            ],
        }

    def canonical_bytes(self) -> bytes:
        encoded = rfc8785.dumps(self.document())
        if len(encoded) > MAX_MAP_BYTES:
            raise SourcePresentationMapError("presentation map exceeds byte bound")
        return encoded

    def sha256(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()

    def sample_at(
        self,
        position_us: int,
        *,
        encoded_source_sha256: str,
        decoder_id: str,
        decoder_version: str,
        probe_id: str,
        probe_version: str,
    ) -> int | None:
        """Return a decoded sample, or neutral for any mismatch/gap/boundary."""

        if (
            type(position_us) is not int
            or not 0 <= position_us <= MAX_PRESENTATION_US
            or encoded_source_sha256 != self.encoded_source_sha256
            or decoder_id != self.decoder_id
            or decoder_version != self.decoder_version
            or probe_id != self.probe_id
            or probe_version != self.probe_version
        ):
            return None
        low, high = 0, len(self.segments)
        while low < high:
            middle = (low + high) // 2
            if self.segments[middle].presentation_start_us <= position_us:
                low = middle + 1
            else:
                high = middle
        index = low - 1
        if index < 0:
            return None
        segment = self.segments[index]
        if position_us >= segment.presentation_end_us:
            return None
        sample = segment.source_start_sample + (
            (position_us - segment.presentation_start_us) * self.decoded_sample_rate // 1_000_000
        )
        return sample if sample < self.decoded_sample_count else None


def parse_source_presentation_map(raw: bytes) -> SourcePresentationMapV1:
    """Accept only exact canonical UTF-8 bytes with no duplicate or unknown fields."""

    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MAP_BYTES:
        raise SourcePresentationMapError("invalid presentation map byte size")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SourcePresentationMapError("duplicate presentation map key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != _MAP_FIELDS:
            raise SourcePresentationMapError("invalid presentation map fields")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["kind"] != "SOURCE_PRESENTATION_MAP_V1"
        ):
            raise SourcePresentationMapError("invalid presentation map version")
        segments = value["segments"]
        if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
            raise SourcePresentationMapError("invalid presentation segments")
        parsed: list[PresentationSegmentV1] = []
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) != {
                "presentation_start_us",
                "presentation_end_us",
                "source_start_sample",
            }:
                raise SourcePresentationMapError("invalid presentation segment fields")
            parsed.append(PresentationSegmentV1(**segment))
        result = SourcePresentationMapV1(
            encoded_source_sha256=cast(str, value["encoded_source_sha256"]),
            decoded_sample_rate=cast(int, value["decoded_sample_rate"]),
            decoded_sample_count=cast(int, value["decoded_sample_count"]),
            decoder_id=cast(str, value["decoder_id"]),
            decoder_version=cast(str, value["decoder_version"]),
            probe_id=cast(str, value["probe_id"]),
            probe_version=cast(str, value["probe_version"]),
            leading_trim_samples=cast(int, value["leading_trim_samples"]),
            trailing_trim_samples=cast(int, value["trailing_trim_samples"]),
            encoder_delay_samples=cast(int | None, value["encoder_delay_samples"]),
            encoder_padding_samples=cast(int | None, value["encoder_padding_samples"]),
            segments=tuple(parsed),
        )
        if result.canonical_bytes() != raw:
            raise SourcePresentationMapError("noncanonical presentation map bytes")
        return result
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, OverflowError) as error:
        raise SourcePresentationMapError("invalid presentation map document") from error

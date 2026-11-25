"""Bounded provider contracts; external identifiers describe evidence, not identity."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autplay.domain.track_metadata import MetadataCandidate, MetadataFields, MetadataQuery
from autplay.domain.vault import ChromaprintEvidence


class MetadataProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True, retry_after_seconds: int = 60) -> None:
        self.code, self.retryable = code, retryable
        self.retry_after_seconds = max(1, min(retry_after_seconds, 86400))
        super().__init__(code)


@dataclass(frozen=True)
class TrackLoudness:
    gain_db: float
    true_peak: float

    def __post_init__(self) -> None:
        if not -60 <= self.gain_db <= 60 or not 0 < self.true_peak <= 64:
            raise ValueError("invalid loudness measurement")


@dataclass(frozen=True)
class EmbeddedMetadata:
    fields: MetadataFields
    artwork: bytes | None = None
    duration_ms: int | None = None
    loudness: TrackLoudness | None = None


class EmbeddedMetadataReader(Protocol):
    def read(self, path: Path) -> EmbeddedMetadata: ...


class MetadataByteWork(Protocol):
    """All audio, artwork decoding and fingerprints stay in the owned process tree."""

    def read_audio(self) -> EmbeddedMetadata: ...
    def fingerprint(self) -> ChromaprintEvidence: ...
    def normalize_artwork(self, payload: bytes, *, catalog_cover: bool = False) -> bytes: ...


class PublicMetadataProvider(Protocol):
    def search(self, query: MetadataQuery) -> tuple[MetadataCandidate, ...]: ...
    def release(
        self, candidate: MetadataCandidate, *, release_track_mbid: str | None = None
    ) -> MetadataCandidate: ...
    def cover(self, release_id: str) -> bytes | None: ...

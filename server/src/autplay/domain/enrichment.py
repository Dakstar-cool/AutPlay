"""Framework-independent P12 model and enrichment values."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

MAX_EMBEDDING_DIMENSION: Final = 16_000
MAX_AUDIO_SEGMENTS: Final = 256
MAX_SEGMENT_BYTES: Final = 16 * 1024 * 1024
MAX_TAGS: Final = 256


@dataclass(frozen=True, slots=True)
class ApprovedEmbeddingModel:
    """One immutable approved registry manifest and mutable lifecycle projection."""

    embedding_model_id: UUID
    model_key: str
    version: str
    task: str
    source: str
    source_revision: str
    artifact_filename: str
    artifact_format: str
    artifact_byte_size: int
    weights_sha256: bytes
    manifest_sha256: bytes
    preprocessing_sha256: bytes
    license_id: str
    runtime: str
    runtime_revision: str
    inference_precision: str
    input_sample_rate_hz: int
    segment_duration_ms: int
    preprocessing_version: str
    pooling_strategy: str
    dimension: int
    status: str

    def __post_init__(self) -> None:
        for value, field, maximum in (
            (self.model_key, "model_key", 300),
            (self.version, "version", 200),
            (self.task, "task", 100),
            (self.source, "source", 500),
            (self.source_revision, "source_revision", 300),
            (self.artifact_filename, "artifact_filename", 300),
            (self.artifact_format, "artifact_format", 100),
            (self.license_id, "license_id", 200),
            (self.runtime, "runtime", 200),
            (self.runtime_revision, "runtime_revision", 200),
            (self.preprocessing_version, "preprocessing_version", 200),
            (self.pooling_strategy, "pooling_strategy", 200),
        ):
            if not 1 <= len(value) <= maximum:
                raise ValueError(f"{field} length is invalid")
        if self.artifact_byte_size < 1:
            raise ValueError("artifact_byte_size must be positive")
        if any(len(value) != 32 for value in self.hashes):
            raise ValueError("model provenance hashes must be SHA-256 values")
        if not 1 <= self.dimension <= MAX_EMBEDDING_DIMENSION:
            raise ValueError("embedding dimension is invalid")
        if self.input_sample_rate_hz < 1 or self.segment_duration_ms < 1:
            raise ValueError("model audio input bounds are invalid")
        if self.status not in {"BENCHMARK", "ACTIVE", "RETIRED", "BLOCKED"}:
            raise ValueError("model lifecycle status is invalid")

    @property
    def hashes(self) -> tuple[bytes, bytes, bytes]:
        """Return immutable artifact, registry and preprocessing hashes."""

        return self.weights_sha256, self.manifest_sha256, self.preprocessing_sha256


@dataclass(frozen=True, slots=True)
class DecodedAudioSegment:
    """One bounded deterministic PCM segment."""

    index: int
    start_ms: int
    pcm_f32le: bytes

    def __post_init__(self) -> None:
        if self.index < 0 or self.start_ms < 0:
            raise ValueError("segment position is invalid")
        if not self.pcm_f32le or len(self.pcm_f32le) > MAX_SEGMENT_BYTES:
            raise ValueError("segment byte size is invalid")
        if len(self.pcm_f32le) % 4:
            raise ValueError("float32 PCM must contain complete samples")


@dataclass(frozen=True, slots=True)
class EmbeddingJobTarget:
    """Typed database target loaded from an opaque job identifier."""

    enrichment_job_id: UUID
    job_kind: str
    recording_id: UUID
    audio_variant_id: UUID
    embedding_model_id: UUID
    expected_weights_sha256: bytes
    expected_preprocessing_sha256: bytes

    def __post_init__(self) -> None:
        if self.job_kind != "AUDIO_EMBEDDING":
            raise ValueError("enrichment job kind is invalid")
        if len(self.expected_weights_sha256) != 32:
            raise ValueError("expected_weights_sha256 must be a SHA-256 value")
        if len(self.expected_preprocessing_sha256) != 32:
            raise ValueError("expected_preprocessing_sha256 must be a SHA-256 value")


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Model-independent immutable enrichment output."""

    target: EmbeddingJobTarget
    preprocessing_input_sha256: bytes
    vector_sha256: bytes
    vector: tuple[float, ...]
    normalized: bool
    tags: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if len(self.preprocessing_input_sha256) != 32 or len(self.vector_sha256) != 32:
            raise ValueError("result provenance hashes must be SHA-256 values")
        if not self.vector or len(self.vector) > MAX_EMBEDDING_DIMENSION:
            raise ValueError("embedding vector dimension is invalid")
        if any(not math.isfinite(value) for value in self.vector):
            raise ValueError("embedding vector contains a non-finite value")
        if len(self.tags) > MAX_TAGS:
            raise ValueError("tag result is not bounded")
        seen: set[str] = set()
        for key, score in self.tags:
            if not 1 <= len(key) <= 200 or key in seen or not math.isfinite(score):
                raise ValueError("tag result is invalid")
            if not 0 <= score <= 1:
                raise ValueError("tag score must be between zero and one")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class AcceleratorDevice:
    """Stable accelerator inventory record used by selection and benchmark evidence."""

    vendor: str
    index: int
    device_uuid: str
    pci_bus_id: str
    name: str
    total_memory_mib: int
    free_memory_mib: int
    compute_capability: str | None
    driver_version: str


@dataclass(frozen=True, slots=True)
class AcceleratorSelection:
    """Selected device plus the deterministic policy reason."""

    selector: str
    device: AcceleratorDevice
    reason: str


@dataclass(frozen=True, slots=True)
class GpuBenchmarkReport:
    """Reproducible shadow benchmark record without tensor or personal payloads."""

    report_version: int
    status: str
    dataset_id: str
    dataset_version: str
    dataset_snapshot_sha256: str
    interaction_schema_version: int
    interaction_watermark: int
    model_manifest_sha256: str
    preprocessing_sha256: str
    environment: dict[str, str | int | float | bool | None]
    metrics: dict[str, int | float | None]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.report_version != 1 or self.status not in {"COMPLETE", "FAILED", "UNAVAILABLE"}:
            raise ValueError("GPU benchmark report identity is invalid")
        for value, field in (
            (self.dataset_id, "dataset_id"),
            (self.dataset_version, "dataset_version"),
        ):
            if not 1 <= len(value) <= 300:
                raise ValueError(f"{field} length is invalid")
        for value in (
            self.dataset_snapshot_sha256,
            self.model_manifest_sha256,
            self.preprocessing_sha256,
        ):
            if len(value) != 64:
                raise ValueError("GPU benchmark report hash is invalid")
            try:
                bytes.fromhex(value)
            except ValueError as error:
                raise ValueError("GPU benchmark report hash is invalid") from error
        if self.interaction_schema_version < 1 or self.interaction_watermark < 0:
            raise ValueError("GPU benchmark dataset identity is invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("GPU benchmark created_at must be timezone-aware")
        if len(self.environment) > 64 or len(self.metrics) > 64:
            raise ValueError("GPU benchmark document is not bounded")
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in (*self.environment.values(), *self.metrics.values())
        ):
            raise ValueError("GPU benchmark document contains non-finite numbers")

    def canonical_bytes(self) -> bytes:
        """Return stable UTF-8 bytes for hashing and persistence."""

        document = {
            "created_at": self.created_at.isoformat(),
            "dataset_id": self.dataset_id,
            "dataset_snapshot_sha256": self.dataset_snapshot_sha256,
            "dataset_version": self.dataset_version,
            "environment": self.environment,
            "interaction_schema_version": self.interaction_schema_version,
            "interaction_watermark": self.interaction_watermark,
            "metrics": self.metrics,
            "model_manifest_sha256": self.model_manifest_sha256,
            "preprocessing_sha256": self.preprocessing_sha256,
            "report_version": self.report_version,
            "status": self.status,
        }
        return json.dumps(
            document, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    @property
    def sha256(self) -> bytes:
        """Return the report identity hash."""

        return hashlib.sha256(self.canonical_bytes()).digest()


def preprocessing_input_sha256(
    manifest_sha256: bytes, segments: tuple[DecodedAudioSegment, ...]
) -> bytes:
    """Hash one deterministic segment layout and its exact PCM bytes."""

    if len(manifest_sha256) != 32:
        raise ValueError("manifest_sha256 must be a SHA-256 value")
    if not 1 <= len(segments) <= MAX_AUDIO_SEGMENTS:
        raise ValueError("decoded segment count is not bounded")
    digest = hashlib.sha256()
    digest.update(b"autplay.preprocessing-input.v1\0")
    digest.update(manifest_sha256)
    for expected, segment in enumerate(segments):
        if segment.index != expected:
            raise ValueError("decoded segment indices must be contiguous")
        digest.update(segment.index.to_bytes(4, "big"))
        digest.update(segment.start_ms.to_bytes(8, "big"))
        digest.update(len(segment.pcm_f32le).to_bytes(8, "big"))
        digest.update(segment.pcm_f32le)
    return digest.digest()


def vector_sha256(vector: tuple[float, ...]) -> bytes:
    """Hash a finite vector using canonical hexadecimal float values."""

    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("vector must contain finite values")
    payload = "\n".join(value.hex() for value in vector).encode("ascii")
    return hashlib.sha256(b"autplay.embedding-vector.v1\0" + payload).digest()


__all__ = (
    "AcceleratorDevice",
    "AcceleratorSelection",
    "ApprovedEmbeddingModel",
    "DecodedAudioSegment",
    "EmbeddingJobTarget",
    "EmbeddingResult",
    "GpuBenchmarkReport",
    "preprocessing_input_sha256",
    "vector_sha256",
)

"""P12 enrichment boundaries shared without importing an ML framework."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from autplay.domain.enrichment import (
    AcceleratorDevice,
    AcceleratorSelection,
    ApprovedEmbeddingModel,
    DecodedAudioSegment,
    EmbeddingJobTarget,
    EmbeddingResult,
)
from autplay.domain.jobs import LeaseFence


class AcceleratorInventory(Protocol):
    """Discover accelerator hardware without exposing it to API/CPU composition."""

    def list_devices(self) -> Sequence[AcceleratorDevice]: ...

    def select(self, selector: str) -> AcceleratorSelection: ...


class ApprovedModelRegistryReader(Protocol):
    """Resolve only pre-approved immutable model manifests by database identity."""

    def get(self, embedding_model_id: UUID) -> ApprovedEmbeddingModel | None: ...


class EnrichmentJobReader(Protocol):
    """Resolve an opaque durable job to its typed target."""

    def get_target(self, enrichment_job_id: UUID) -> EmbeddingJobTarget | None: ...


class BoundedAudioPreprocessor(Protocol):
    """Decode verified Vault audio into bounded deterministic PCM segments."""

    def decode(
        self, target: EmbeddingJobTarget, model: ApprovedEmbeddingModel
    ) -> Sequence[DecodedAudioSegment]: ...


class TrackEmbedder(Protocol):
    """GPU-project-owned concrete embedding implementation."""

    @property
    def model_id(self) -> UUID: ...

    @property
    def weights_sha256(self) -> bytes: ...

    def embed(
        self, segments: Sequence[DecodedAudioSegment], *, batch_size: int
    ) -> tuple[tuple[float, ...], tuple[tuple[str, float], ...]]: ...


class TrackEmbeddingWriter(Protocol):
    """Publish one fenced immutable result and treat exact replay idempotently."""

    def put(
        self,
        fence: LeaseFence,
        model: ApprovedEmbeddingModel,
        result: EmbeddingResult,
    ) -> bool: ...


__all__ = (
    "AcceleratorInventory",
    "ApprovedModelRegistryReader",
    "BoundedAudioPreprocessor",
    "EnrichmentJobReader",
    "TrackEmbedder",
    "TrackEmbeddingWriter",
)

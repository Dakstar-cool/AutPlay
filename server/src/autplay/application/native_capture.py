"""Temporal membership selected by the original Source3 database transaction."""

from dataclasses import dataclass
from uuid import UUID

from autplay.domain.adaptive_recommendations import MAX_SOURCE_EVENTS


@dataclass(frozen=True, slots=True)
class NativeCaptureBoundary:
    """Internal SQL-discovered set, including every causal support parent."""

    owner: UUID
    event_ids: tuple[UUID, ...]
    cutoff_at_ms: int
    interaction_watermark: int

    def __post_init__(self) -> None:
        if (
            len(self.event_ids) > MAX_SOURCE_EVENTS
            or self.event_ids != tuple(sorted(set(self.event_ids)))
            or any(type(value) is not UUID for value in self.event_ids)
            or type(self.owner) is not UUID
            or type(self.cutoff_at_ms) is not int
            or not 0 <= self.cutoff_at_ms <= 2**53 - 1
            or type(self.interaction_watermark) is not int
            or not 0 <= self.interaction_watermark <= 2**53 - 1
        ):
            raise ValueError("NATIVE_CAPTURE_BOUNDARY")

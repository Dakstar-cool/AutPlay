"""Exact retained ownership for descriptive metadata byte and provider work."""

from dataclasses import dataclass
from uuid import UUID

from .jobs import LeaseFence
from .vault import OpaqueStorageKey, VerifiedStagedFile


@dataclass(frozen=True, slots=True)
class MetadataAudioTarget:
    recording_id: UUID
    audio_variant_id: UUID
    vault_object_id: UUID
    storage_key: OpaqueStorageKey
    expected: VerifiedStagedFile


@dataclass(frozen=True, slots=True)
class MetadataExecutionTicket:
    execution_id: UUID
    owner_run_id: UUID
    user_id: UUID
    user_track_ref_id: UUID
    generation: int
    authority_generation: int
    fence: LeaseFence
    audio: MetadataAudioTarget | None

    @property
    def kind(self) -> str:
        return "TRACK_METADATA"

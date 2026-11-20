"""Private P11 invocation identities, never portable authorization tokens."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from autplay.domain.recommendations import SnapshotTrack


@dataclass(frozen=True, slots=True)
class NativeServedIdentity:
    owner: UUID
    device: UUID
    journal_epoch: UUID
    server_instance: UUID
    identity_epoch: int

    def validate(self) -> None:
        if (
            any(
                type(value) is not UUID
                for value in (self.owner, self.device, self.journal_epoch, self.server_instance)
            )
            or type(self.identity_epoch) is not int
            or not 1 <= self.identity_epoch <= 2**63 - 1
        ):
            raise ValueError("NATIVE_SERVED_IDENTITY")


@dataclass(frozen=True, slots=True)
class NativeP11Snapshot:
    """Complete bounded server snapshot read under the writer transaction."""

    tracks: tuple[SnapshotTrack, ...]
    interaction_watermark: int
    cutoff: datetime

    def validate(self) -> None:
        if (
            len(self.tracks) > 5000
            or len({track.recording_id for track in self.tracks}) != len(self.tracks)
            or type(self.interaction_watermark) is not int
            or not 0 <= self.interaction_watermark <= 2**63 - 1
            or self.cutoff.tzinfo is None
        ):
            raise ValueError("NATIVE_SERVED_SNAPSHOT")

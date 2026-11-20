"""P11 v2 original catalogue and the exact production serving window."""

from collections.abc import Sequence
from itertools import pairwise
from uuid import UUID

from autplay.application.recommendations import (
    RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
    recommendation_snapshot_limit,
    recommendation_snapshot_track_document,
)
from autplay.domain.recommendations import JsonValue, SnapshotTrack

COLLECTION_V2 = "AUTPLAY_P11_NATIVE_COLLECTION_V2"


def catalogue_document(
    owner: UUID, watermark: int, tracks: Sequence[SnapshotTrack]
) -> dict[str, JsonValue]:
    return {
        "schema": "P11_V2_FULL_CATALOGUE_V1",
        "user_id": str(owner),
        "interaction_watermark": watermark,
        "max_tracks": RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
        "tracks": [recommendation_snapshot_track_document(track) for track in tracks],
    }


def catalogue_matches_served(
    tracks: Sequence[SnapshotTrack], served: tuple[SnapshotTrack, ...]
) -> bool:
    if any(left.recording_id >= right.recording_id for left, right in pairwise(tracks)):
        return False
    recent = sorted(tracks, key=lambda t: (-t.added_at_ms, t.recording_id))
    window = sorted(recent[: recommendation_snapshot_limit("2")], key=lambda t: t.recording_id)
    return tuple(window) == served

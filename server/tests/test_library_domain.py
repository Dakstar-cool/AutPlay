from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from autplay.domain.library import (
    AppendListeningEvent,
    CreateUnresolvedTrack,
    LibraryCommandError,
    RecommendationAttribution,
    validate_playlist_name,
    validate_position_key,
)


def test_unresolved_track_never_fabricates_a_recording_and_needs_metadata() -> None:
    command = CreateUnresolvedTrack(uuid4(), "Track", None)
    assert command.title == "Track"
    with pytest.raises(LibraryCommandError, match="unresolved_track_metadata_required"):
        CreateUnresolvedTrack(uuid4(), None, None)


def test_playlist_and_logical_history_bounds_are_enforced() -> None:
    assert validate_playlist_name(" a ") == "a"
    assert validate_position_key("a") == "a"
    with pytest.raises(LibraryCommandError):
        validate_position_key("")
    with pytest.raises(LibraryCommandError, match="played_duration_invalid"):
        AppendListeningEvent(uuid4(), uuid4(), datetime.now(UTC), -1)
    attribution = RecommendationAttribution(uuid4(), uuid4(), 1, "p07", "library")
    assert attribution.source == "p07"
    with pytest.raises(LibraryCommandError, match="recommendation_attribution_invalid"):
        RecommendationAttribution(uuid4(), uuid4(), 0, "p07", "library")
    with pytest.raises(LibraryCommandError, match="recommendation_attribution_required"):
        AppendListeningEvent(uuid4(), uuid4(), datetime.now(UTC), 1, event_origin="RECOMMENDED")
    assert (
        AppendListeningEvent(
            uuid4(),
            uuid4(),
            datetime.now(UTC),
            1,
            event_origin="RECOMMENDED",
            attribution=attribution,
        ).attribution
        is not None
    )

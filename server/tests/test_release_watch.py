"""Bounded, deterministic favorite-artist scoring."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autplay.domain.release_watch import ListeningEvidence, evidence_digest, project_favorites


def test_sparse_history_and_repeat_cap() -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    artist, first, second = uuid4(), uuid4(), uuid4()
    sparse = [ListeningEvidence(uuid4(), artist, first, now - timedelta(days=1)) for _ in range(20)]
    assert project_favorites(sparse, as_of=now) == []
    events = [*sparse, ListeningEvidence(uuid4(), artist, second, now - timedelta(days=2))]
    result = project_favorites(events, as_of=now)
    assert len(result) == 1
    assert result[0].qualified_play_count == 21
    assert result[0].distinct_recording_count == 2
    assert result[0].score_milli == 4000  # three repeats + one other recording
    assert result[0].evidence_sha256 == evidence_digest(list(reversed(events)))


def test_recency_window_drops_old_and_future_events() -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    artist, first, second = uuid4(), uuid4(), uuid4()
    events = [
        ListeningEvidence(uuid4(), artist, first, now - timedelta(days=8)),
        ListeningEvidence(uuid4(), artist, first, now - timedelta(days=31)),
        ListeningEvidence(uuid4(), artist, second, now - timedelta(days=1)),
        ListeningEvidence(uuid4(), artist, second, now - timedelta(days=91)),
        ListeningEvidence(uuid4(), artist, second, now + timedelta(seconds=1)),
    ]
    result = project_favorites(events, as_of=now)
    assert len(result) == 1
    assert result[0].qualified_play_count == 3
    assert result[0].score_milli == 2100

"""Distribute an explicit like across the ordered performing acts on its recording."""

from collections import defaultdict
from collections.abc import Sequence

from autplay.domain.recommendations import SnapshotTrack


def snapshot_artist_acts(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > 100
        or not all(isinstance(act, str) and 0 < len(act) <= 100 for act in value)
    ):
        raise ValueError("invalid recommendation artist acts")
    return tuple(dict.fromkeys(value))


def _shares(acts: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
    remaining = 1.0
    result = []
    for act in acts[:-1]:
        result.append((act, remaining * 0.65))
        remaining *= 0.35
    result.append((acts[-1], remaining))
    return tuple(result)


def liked_credit_weights(
    tracks: Sequence[SnapshotTrack], likes: Sequence[SnapshotTrack]
) -> dict[str, float]:
    shares = {
        track.artist_key: _shares(track.artist_acts or (f"credit:{track.artist_key}",))
        for track in tracks
    }
    totals: defaultdict[str, float] = defaultdict(float)
    for track in likes:
        for act, share in shares[track.artist_key]:
            totals[act] += share

    # Preserve relative evidence from repeated likes within the ranker's unit score.
    scale = max(1.0, max(totals.values(), default=0.0))
    matches = {
        credit: sum(totals.get(act, 0.0) * share for act, share in members) / scale
        for credit, members in shares.items()
    }
    return {credit: weight for credit, weight in matches.items() if weight > 0}

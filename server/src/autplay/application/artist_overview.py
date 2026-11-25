"""Public artist previews and a resumable, date-filtered release refresh."""

from datetime import date, timedelta
from uuid import UUID, uuid4

from autplay.domain.music_discovery import MusicDiscoveryPage
from autplay.ports.music_discovery import DatedArtistReleases, MusicDiscoveryProvider
from autplay.ports.track_metadata import MetadataProviderError

PREVIEW_SIZE = 25
RELEASE_OVERLAP = timedelta(days=7)
BASELINE_LIFETIME = timedelta(days=30)


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    rows = page["items"]
    assert isinstance(rows, list)
    return [row for row in rows if isinstance(row, dict)]


def _release_day(row: dict[str, object]) -> date | None:
    value = row.get("release_date")
    if not isinstance(value, str):
        return None
    if len(value) == 4:
        value += "-01-01"
    elif len(value) == 7:
        value += "-01"
    return date.fromisoformat(value)


def _latest(
    rows: list[dict[str, object]], additions: list[dict[str, object]], today: date
) -> list[dict[str, object]]:
    by_id = {str(row["release_id"]): row for row in rows + additions}
    dated = [(day, row) for row in by_id.values() if (day := _release_day(row)) and day <= today]
    dated.sort(key=lambda item: (item[0], str(item[1]["release_id"])), reverse=True)
    groups: dict[str, dict[str, object]] = {}
    for _, row in dated:
        groups.setdefault(str(row.get("release_group_id") or row["release_id"]), row)
        if len(groups) == PREVIEW_SIZE:
            break
    return list(groups.values())


def build_artist_overview(
    provider: MusicDiscoveryProvider, artist: UUID, saved: dict[str, object] | None, today: date
) -> dict[str, object]:
    data = dict(saved or {})
    pending = data.get("delta_offset") is not None
    rebuild = not saved or (
        not pending and date.fromisoformat(str(data["baseline_day"])) + BASELINE_LIFETIME <= today
    )
    if rebuild:
        data["tracks"] = provider.artist_tracks(artist, limit=PREVIEW_SIZE, offset=0).view()
        releases = provider.artist_releases(artist, limit=PREVIEW_SIZE, offset=0).view()
        data["releases"] = releases
        data["baseline_day"] = today.isoformat()
        data["baseline_id"] = str(uuid4())
        albums = _latest([], _items(releases), today)
        data["latest"] = albums
        if releases["next_offset"] is None and not releases["truncated"]:
            data["checked_day"] = today.isoformat()
            data["delta_offset"] = None
            return data
        # Anything newer than the newest dated edition in the browse preview can affect the hero.
        start = _release_day(albums[0]) if albums else None
        data["delta_start"] = (start or date.min).isoformat()
        data["delta_end"] = today.isoformat()
        data["delta_offset"] = 0
        # Publish the first screen before scanning a prolific artist's release history.
        return data

    if not pending:
        data["delta_start"] = (
            date.fromisoformat(str(data["checked_day"])) - RELEASE_OVERLAP
        ).isoformat()
        data["delta_end"] = today.isoformat()
        data["delta_offset"] = 0
    if not isinstance(provider, DatedArtistReleases):
        raise MetadataProviderError("metadata_provider_unavailable", retryable=True)
    start, end = (date.fromisoformat(str(data[key])) for key in ("delta_start", "delta_end"))
    offset = data["delta_offset"]
    assert isinstance(offset, int)
    delta: MusicDiscoveryPage = provider.recent_artist_releases(
        artist, start, end, limit=50, offset=offset
    )
    page = delta.view()
    if page["truncated"]:
        raise MetadataProviderError("metadata_response_invalid", retryable=True)
    latest = data["latest"]
    assert isinstance(latest, list)
    data["latest"] = _latest(latest, _items(page), today)
    data["delta_offset"] = page["next_offset"]
    if page["next_offset"] is None:
        data["checked_day"] = end.isoformat()
    return data


def artist_overview_view(data: dict[str, object], known_baseline: UUID | None) -> dict[str, object]:
    releases = data["releases"]
    latest = data["latest"]
    assert isinstance(releases, dict) and isinstance(latest, list)
    return {
        "contract_version": "artist-overview-v1",
        "baseline_id": data["baseline_id"],
        **(
            {"tracks": data["tracks"], "releases": releases}
            if str(known_baseline) != data["baseline_id"]
            else {}
        ),
        "latest": {
            **releases,
            "items": latest,
            "limit": PREVIEW_SIZE,
            "offset": 0,
            "total_count": len(latest),
            "next_offset": None,
            "truncated": False,
        },
        "latest_complete": "checked_day" in data and data.get("delta_offset") is None,
    }

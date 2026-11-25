from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.catalog_pages import CatalogPageCache
from autplay.application.artist_overview import artist_overview_view, build_artist_overview
from autplay.application.music_discovery import MusicDiscoveryService
from autplay.application.music_library import MusicError
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.music_discovery import (
    MusicDiscoveryCard,
    MusicDiscoveryEntity,
    MusicDiscoveryKind,
    MusicDiscoveryPage,
    MusicDiscoveryValidationError,
    discovery_page_bounds,
    discovery_query,
)
from autplay.ports.music_discovery import MusicDiscoveryProvider
from autplay.ports.track_metadata import MetadataProviderError


@pytest.mark.parametrize("entity", list(MusicDiscoveryEntity))
def test_catalogue_card_is_namespaced_evidence_and_never_a_selected_source(
    entity: MusicDiscoveryEntity,
) -> None:
    card = MusicDiscoveryCard(entity, uuid4(), "Song (Live / Remix)", artist="AC/DC")
    view = card.view()
    assert view["id"] == f"musicbrainz:{entity}:{card.entity_id}"
    assert view["acquisition_allowed"] is False and view["availability"] == "METADATA_ONLY"
    assert "candidate_id" not in view and "search_id" not in view
    if entity in {MusicDiscoveryEntity.ARTIST, MusicDiscoveryEntity.RELEASE}:
        assert view["download_search_query"] is None and view["can_browse_tracks"] is True
    else:
        assert view["download_search_query"] == "AC/DC Song (Live / Remix)"
        assert view["can_browse_tracks"] is False


def test_source_lookup_suggestion_requires_artist_and_keeps_complete_versions() -> None:
    assert (
        MusicDiscoveryCard(MusicDiscoveryEntity.RECORDING, uuid4(), "Track").download_search_query
        is None
    )
    long = MusicDiscoveryCard(
        MusicDiscoveryEntity.RECORDING, uuid4(), "a" * 195 + " Live", artist="Artist"
    )
    assert long.download_search_query is None
    assert discovery_query("  AC/DC\tSong   (Live)  ") == "AC/DC Song (Live)"


def test_disambiguation_only_versions_survive_lookup_and_conflicting_titles_need_manual_query() -> (
    None
):
    card = MusicDiscoveryCard(
        MusicDiscoveryEntity.RELEASE_TRACK,
        uuid4(),
        "Song",
        artist="Artist",
        recording_title="Song",
        disambiguation="live at Venue",
    )
    assert card.download_search_query == "Artist Song (live at Venue)"
    conflicting = MusicDiscoveryCard(
        MusicDiscoveryEntity.RELEASE_TRACK,
        uuid4(),
        "Song (Remix)",
        artist="Artist",
        recording_title="Song (Live)",
    )
    assert conflicting.download_search_query is None


def test_paging_advances_by_raw_rows_and_honestly_marks_offset_ceiling() -> None:
    card = MusicDiscoveryCard(MusicDiscoveryEntity.ARTIST, uuid4(), "Artist")
    page = MusicDiscoveryPage((card,), 3, 0, 5, 3).view()
    assert page["next_offset"] == 3 and page["truncated"] is False
    capped = MusicDiscoveryPage((card,), 1, 1000, 2000, 1).view()
    assert capped["next_offset"] is None and capped["truncated"] is True
    empty = MusicDiscoveryPage((), 25, 0, 0, 0).view()
    assert empty["next_offset"] is None and empty["truncated"] is False


@pytest.mark.parametrize("limit,offset", [(0, 0), (51, 0), (True, 0), (1, -1), (1, 1001)])
def test_catalogue_paging_is_bounded_before_provider_access(limit: int, offset: int) -> None:
    with pytest.raises(MusicDiscoveryValidationError):
        discovery_page_bounds(limit, offset)


class Provider:
    def __init__(self, error: MetadataProviderError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, object, int, int]] = []

    def search(
        self, query: str, kind: MusicDiscoveryKind, *, limit: int, offset: int
    ) -> MusicDiscoveryPage:
        self.calls.append((query, kind, limit, offset))
        if self.error:
            raise self.error
        return MusicDiscoveryPage((), limit, offset, 0, 0)

    def artist_releases(self, artist_id: object, *, limit: int, offset: int) -> MusicDiscoveryPage:
        return self.search(str(artist_id), MusicDiscoveryKind.ARTIST, limit=limit, offset=offset)

    def artist_tracks(self, artist_id: object, *, limit: int, offset: int) -> MusicDiscoveryPage:
        return self.search(str(artist_id), MusicDiscoveryKind.ARTIST, limit=limit, offset=offset)

    def release_tracks(self, release_id: object, *, limit: int, offset: int) -> MusicDiscoveryPage:
        return self.search(str(release_id), MusicDiscoveryKind.ALBUM, limit=limit, offset=offset)


def test_service_opens_and_closes_a_fresh_owner_context_and_validates_before_opening() -> None:
    owners: list[Principal] = []
    closed: list[Principal] = []
    provider = Provider()

    @contextmanager
    def factory(principal: Principal) -> Iterator[MusicDiscoveryProvider]:
        owners.append(principal)
        try:
            yield provider
        finally:
            closed.append(principal)

    service = MusicDiscoveryService(factory)
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    other = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    service.search(owner, "  AC/DC  ", MusicDiscoveryKind.ARTIST)
    service.artist_tracks(other, uuid4(), limit=5, offset=10)
    service.release_tracks(owner, uuid4(), limit=1)
    assert owners == closed == [owner, other, owner]
    assert provider.calls[0] == ("AC/DC", MusicDiscoveryKind.ARTIST, 25, 0)
    for query in ("", " ", "a" * 201, "a\x00b"):
        with pytest.raises(MusicError) as error:
            service.search(owner, query, MusicDiscoveryKind.ARTIST)
        assert error.value.status_code == 422
    with pytest.raises(MusicError):
        service.artist_tracks(owner, uuid4(), limit=51)
    assert owners == [owner, other, owner]


@pytest.mark.parametrize(
    "provider_code,status,public_code",
    [
        ("metadata_provider_busy", 503, "music_discovery_busy"),
        ("metadata_network_unavailable", 503, "music_discovery_unavailable"),
        ("metadata_response_invalid", 503, "music_discovery_unavailable"),
        ("metadata_release_missing", 404, "music_discovery_entity_not_found"),
    ],
)
def test_provider_errors_are_not_fake_empty_successes(
    provider_code: str,
    status: int,
    public_code: str,
) -> None:
    from contextlib import nullcontext

    provider = Provider(MetadataProviderError(provider_code, retryable=False))
    service = MusicDiscoveryService(lambda _: nullcontext(provider))
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    with pytest.raises(MusicError) as error:
        service.search(owner, "Artist", MusicDiscoveryKind.ARTIST)
    assert error.value.status_code == status and error.value.code == public_code
    assert error.value.retryable is False


def test_official_artist_releases_advance_over_filtered_editions() -> None:
    from autplay.adapters.public_music_search import MusicBrainzDiscoveryProvider

    artist, release, group = uuid4(), uuid4(), uuid4()

    class Http:
        def json(self, url: str) -> dict[str, object]:
            assert f"artist={artist}" in url and "status=official" in url
            return {
                "release-count": 3,
                "release-offset": 0,
                "releases": [
                    {
                        "id": str(release),
                        "title": "Joint edition",
                        "date": "2026-05",
                        "status": "Official",
                        "release-group": {"id": str(group), "primary-type": "Single"},
                    },
                    {"id": str(uuid4()), "title": "Demo", "status": "Bootleg"},
                ],
            }

    page = MusicBrainzDiscoveryProvider(Http()).artist_releases(artist, limit=2, offset=0)
    assert page.view()["next_offset"] == 2
    assert [(row.release_id, row.release_date, row.release_group_id) for row in page.items] == [
        (release, "2026-05", group),
    ]


def test_ranking_failure_preserves_artist_identity_and_skips_unrelated_recordings() -> None:

    artist, recording, internal, unrelated = (uuid4() for _ in range(4))
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)

    def artist_recordings(owner_id: UUID, artist_id: UUID) -> tuple[str, dict[str, str]]:
        assert (owner_id, artist_id) == (owner.user_id, artist)
        return str(artist), {str(recording): str(internal)}

    def read(key: str, loader: Callable[[], dict[str, object]]) -> dict[str, object]:
        return loader()

    cache = Mock(spec_set=CatalogPageCache)
    cache.artist_recordings.side_effect = artist_recordings
    cache.read.side_effect = read

    def read_public(principal: Principal, url: str) -> bytes:
        assert principal == owner and url.endswith(str(artist))
        return json.dumps(
            [
                {
                    "artist_mbids": [str(artist)],
                    "recording_mbid": str(recording),
                    "total_listen_count": 19,
                },
                {
                    "artist_mbids": [str(artist)],
                    "recording_mbid": str(unrelated),
                    "total_listen_count": 90,
                },
            ]
        ).encode()

    service = MusicDiscoveryService(lambda _: nullcontext(Provider()), cache, read_public)
    ranked = service.artist_ranking(owner, artist)
    assert ranked["counts"] == {str(internal): 19}
    assert ranked["library_recordings"] == {str(recording): str(internal)}
    assert ranked["recording_counts"] == {str(recording): 19, str(unrelated): 90}
    service.read_public = lambda *_: None
    unavailable = service.artist_ranking(owner, artist)
    assert unavailable["artist_mbid"] == str(artist) and unavailable["ranking_available"] is False


def test_artist_snapshot_updates_latest_without_rereading_tracks_or_replacing_editions() -> None:
    artist, edition, alternate, group = (uuid4() for _ in range(4))
    today = date(2026, 11, 25)
    calls: list[tuple[date, date, int]] = []

    class WithReleases(Provider):
        def recent_artist_releases(
            self, artist_id: UUID, start: date, end: date, *, limit: int, offset: int
        ) -> MusicDiscoveryPage:
            assert artist_id == artist
            calls.append((start, end, offset))
            identity = alternate if offset else edition
            card = MusicDiscoveryCard(
                MusicDiscoveryEntity.RELEASE,
                identity,
                "Album",
                release_id=identity,
                release_date="2026-11-25" if offset else "2026-11-24",
                release_group_id=group,
            )
            return MusicDiscoveryPage((card,), limit, offset, 2, 1)

    provider = WithReleases()
    base = build_artist_overview(provider, artist, None, today - timedelta(days=1))
    token = UUID(str(base["baseline_id"]))
    first = build_artist_overview(provider, artist, base, today)
    assert first["delta_offset"] == 1 and first["checked_day"] == base["checked_day"]
    final = build_artist_overview(provider, artist, first, today)
    assert calls == [(date(2026, 11, 17), today, 0), (date(2026, 11, 17), today, 1)]
    assert len(provider.calls) == 2
    assert final["tracks"] == base["tracks"] and final["releases"] == base["releases"]
    patch = artist_overview_view(final, token)
    assert patch["latest_complete"] is True and "releases" not in patch and "tracks" not in patch
    assert final["latest"] == [
        MusicDiscoveryCard(
            MusicDiscoveryEntity.RELEASE,
            alternate,
            "Album",
            release_id=alternate,
            release_date="2026-11-25",
            release_group_id=group,
        ).view()
    ]
    assert "tracks" in artist_overview_view(final, uuid4())


def test_saved_artist_overview_is_shared_and_stale_refresh_runs_after_the_read() -> None:
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    other = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    artist = uuid4()
    doc = build_artist_overview(Provider(), artist, None, datetime.now(UTC).date())
    cache = Mock(spec_set=CatalogPageCache)
    cache.saved.return_value = (doc, datetime.now(UTC))
    service = MusicDiscoveryService(lambda _: pytest.fail("Unexpected provider read"), cache)
    tasks: list[Callable[[], None]] = []
    library_artist = str(uuid4())
    cache.library_artist_id.side_effect = lambda who, mbid: (
        library_artist if who == owner.user_id and mbid == artist else None
    )
    first = service.artist_overview(owner, artist, tasks.append)
    assert first["library_artist_id"] == library_artist
    assert service.artist_overview(other, artist, tasks.append) == {
        **first,
        "library_artist_id": None,
    }
    assert not tasks and "tracks" in first and "library_recordings" not in first
    cache.saved.return_value = (doc, datetime.now(UTC) - timedelta(days=1))
    token = UUID(str(doc["baseline_id"]))
    stale = service.artist_overview(other, artist, tasks.append, token)
    assert stale["refreshing"] is True and len(tasks) == 1 and "tracks" not in stale
    cache.update.assert_not_called()
    cache.update.return_value = doc
    tasks[0]()
    assert cache.update.call_args.args[0] == f"musicbrainz:artist-overview-v1:{artist}"


def test_artist_portrait_uses_linked_profile_and_rejects_ambiguous_links() -> None:
    owner = Principal(uuid4(), uuid4(), uuid4(), AccountRole.USER)
    artist = uuid4()
    cache = Mock(spec_set=CatalogPageCache)
    cache.read.side_effect = lambda key, load: load()
    relations = [{"url": {"resource": "https://www.deezer.com/artist/42"}}]
    cdn = "https://cdn-images.dzcdn.net/images/artist/" + "a" * 32 + "/500x500-000000-80-0-0.jpg"
    jpeg = bytes.fromhex("ffd8ffe0")
    calls = []

    def read(principal: Principal, url: str) -> bytes:
        assert principal == owner
        calls.append(url)
        if url.startswith("https://musicbrainz.org/ws/2/artist/"):
            return json.dumps({"id": str(artist), "relations": relations}).encode()
        if url == "https://api.deezer.com/artist/42":
            return json.dumps({"id": 42, "picture_big": cdn}).encode()
        assert url == cdn
        return jpeg

    service = MusicDiscoveryService(lambda _: nullcontext(Provider()), cache, read)
    assert service.artist_portrait(owner, artist) == jpeg and len(calls) == 3
    relations.append({"url": {"resource": "https://www.deezer.com/artist/43"}})
    calls.clear()
    with pytest.raises(MusicError) as failure:
        service.artist_portrait(owner, artist)
    assert failure.value.code == "catalog_cover_missing" and len(calls) == 1

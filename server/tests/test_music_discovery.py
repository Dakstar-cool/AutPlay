from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
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

"""Catalogue hints require actual audio corroboration and exact hydrated membership."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.public_catalogue_context import MusicBrainzCatalogueContextProvider
from autplay.domain.catalogue_context import (
    CatalogueContextEntity,
    CatalogueLookupContext,
    CatalogueTrackCard,
    parse_catalogue_card,
)
from autplay.domain.track_metadata import MetadataQuery
from autplay.ports.track_metadata import MetadataProviderError


def card() -> CatalogueTrackCard:
    return CatalogueTrackCard(
        CatalogueContextEntity.RELEASE_TRACK,
        uuid4(),
        uuid4(),
        "Song",
        "Artist",
        uuid4(),
        "Edition",
        "2001",
        180000,
        1,
        2,
        "Song",
    )


@pytest.mark.parametrize(
    ("title", "artist", "duration", "expected"),
    [
        ("Artist - Song (Official Video)", "Artist", 180100, True),
        ("Song", "Artist", None, False),
        ("Song", "", 180000, False),
        ("Song", "Uploader", 180000, False),
        ("Song (Live)", "Artist", 180000, False),
        ("Song", "Artist", 182001, False),
        ("Different song", "Artist", 180000, False),
    ],
)
def test_card_cannot_turn_missing_or_conflicting_audio_facts_into_ids(
    title: str, artist: str, duration: int | None, expected: bool
) -> None:
    hint = CatalogueLookupContext(uuid4(), card(), datetime.now(UTC), b"h" * 32)
    assert hint.corroborates(MetadataQuery(title, artist, duration_ms=duration)) is expected
    assert hint.card.document()["album"] == "Edition"


def test_recording_version_and_existing_album_conflict_veto_context() -> None:
    hint = CatalogueLookupContext(
        uuid4(), replace(card(), disambiguation="live"), datetime.now(UTC), b"h" * 32
    )
    assert not hint.corroborates(MetadataQuery("Song", "Artist", duration_ms=180000))
    assert hint.corroborates(MetadataQuery("Song (Live)", "Artist", duration_ms=180000)) is False
    clean = replace(hint, card=card())
    assert not clean.corroborates(MetadataQuery("Song", "Artist", "Other", 180000))


def test_actual_recording_title_version_can_corroborate_plain_release_track_title() -> None:
    hint = CatalogueLookupContext(
        uuid4(),
        replace(card(), recording_title="Song (Live)", disambiguation="live"),
        datetime.now(UTC),
        b"h" * 32,
    )
    assert hint.corroborates(MetadataQuery("Artist - Song (Live)", "Artist", duration_ms=180000))
    assert not hint.corroborates(MetadataQuery("Song", "Artist", duration_ms=180000))
    assert not hint.corroborates(MetadataQuery("Song (Remix)", "Artist", duration_ms=180000))


@pytest.mark.parametrize(
    "bound",
    [
        {"recording_mbid": str(uuid4())},
        {"release_mbid": str(uuid4())},
        {"disc_number": 2},
        {"track_number": 3},
        {"release_date": "2002"},
    ],
)
def test_existing_actual_ids_dates_and_positions_cannot_be_overridden(
    bound: dict[str, Any],
) -> None:
    hint = CatalogueLookupContext(uuid4(), card(), datetime.now(UTC), b"h" * 32)
    assert not hint.corroborates(MetadataQuery("Song", "Artist", duration_ms=180000, **bound))


def test_persisted_card_rejects_unknown_and_client_source_fields() -> None:
    original = card()
    assert parse_catalogue_card(original.document()) == original
    with pytest.raises(ValueError):
        parse_catalogue_card({**original.document(), "source": "SOURCE_NATIVE"})
    with pytest.raises(ValueError):
        parse_catalogue_card({**original.document(), "schema_version": True})


class Http:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.urls: list[str] = []

    def json(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        return self.document


def release_document(hint: CatalogueTrackCard) -> dict[str, Any]:
    return {
        "id": str(hint.release_mbid),
        "title": "Server edition",
        "date": "2001-03",
        "medium-count": 1,
        "media": [
            {
                "position": 1,
                "track-count": 2,
                "tracks": [
                    {
                        "id": str(uuid4()),
                        "position": 1,
                        "title": "Song",
                        "length": 180000,
                        "recording": {"id": str(hint.recording_mbid), "title": "Song"},
                    },
                    {
                        "id": str(hint.entity_id),
                        "position": 2,
                        "title": "Song",
                        "length": 180000,
                        "artist-credit": [{"name": "Artist", "artist": {"name": "Artist"}}],
                        "recording": {"id": str(hint.recording_mbid), "title": "Song"},
                    },
                ],
            }
        ],
    }


def test_exact_release_track_not_first_repeated_recording_is_hydrated_once() -> None:
    hint = card()
    http = Http(release_document(hint))
    result = MusicBrainzCatalogueContextProvider(http).hydrate(
        hint.entity_type, hint.entity_id, hint.release_mbid
    )
    assert result.entity_id == hint.entity_id and result.track_number == 2
    assert result.album == "Server edition" and result.release_date == "2001-03"
    assert result.recording_mbid == hint.recording_mbid
    assert len(http.urls) == 1 and "/release/" in http.urls[0]


@pytest.mark.parametrize("damage", ["nonmember", "wrong_release", "partial", "duplicate"])
def test_membership_and_completeness_are_not_inferred(damage: str) -> None:
    hint = card()
    document = release_document(hint)
    requested = hint.entity_id
    if damage == "nonmember":
        requested = uuid4()
    elif damage == "wrong_release":
        document["id"] = str(uuid4())
    elif damage == "partial":
        document["media"][0]["track-count"] = 3
    else:
        document["media"][0]["tracks"][0]["id"] = str(hint.entity_id)
    with pytest.raises(MetadataProviderError):
        MusicBrainzCatalogueContextProvider(Http(document)).hydrate(
            hint.entity_type, requested, hint.release_mbid
        )


def test_recording_context_has_no_fabricated_edition() -> None:
    identity = uuid4()
    http = Http({"id": str(identity), "title": "Song", "length": 180000})
    result = MusicBrainzCatalogueContextProvider(http).hydrate(
        CatalogueContextEntity.RECORDING, identity, None
    )
    assert result.recording_mbid == identity
    assert result.release_mbid is None and result.album is None and result.artist is None
    assert not result.corroborates(title="Song", artist="Artist", measured_duration_ms=180000)


def test_no_http_dependency_default_or_client_metadata_input() -> None:
    with pytest.raises(TypeError):
        MusicBrainzCatalogueContextProvider()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        CatalogueTrackCard(CatalogueContextEntity.RECORDING, uuid4(), UUID(int=0), "Song")

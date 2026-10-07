"""Server-validated catalogue descriptions are lookup bounds, never audio facts."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from .track_metadata import MetadataQuery, lookup_title, normalized, partial_date, version_markers

MAX_CATALOGUE_CONTEXT_BYTES = 16384


class CatalogueContextEntity(StrEnum):
    RECORDING = "recording"
    RELEASE_TRACK = "release_track"


@dataclass(frozen=True, slots=True)
class CatalogueTrackCard:
    entity_type: CatalogueContextEntity
    entity_id: UUID
    recording_mbid: UUID
    title: str
    artist: str | None = None
    release_mbid: UUID | None = None
    album: str | None = None
    release_date: str | None = None
    duration_ms: int | None = None
    disc_number: int | None = None
    track_number: int | None = None
    recording_title: str | None = None
    disambiguation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str):
            raise ValueError("catalogue_context_card_invalid")
        if not isinstance(self.entity_type, CatalogueContextEntity):
            raise ValueError("catalogue_context_card_invalid")
        if not all(isinstance(item, UUID) for item in (self.entity_id, self.recording_mbid)):
            raise ValueError("catalogue_context_card_invalid")
        if self.entity_type == CatalogueContextEntity.RECORDING:
            if self.entity_id != self.recording_mbid or any(
                value is not None
                for value in (
                    self.release_mbid,
                    self.album,
                    self.disc_number,
                    self.track_number,
                )
            ):
                raise ValueError("catalogue_context_card_invalid")
        elif not isinstance(self.release_mbid, UUID) or self.album is None:
            raise ValueError("catalogue_context_card_invalid")
        for value in (
            self.title,
            self.artist,
            self.album,
            self.recording_title,
            self.disambiguation,
        ):
            if value is not None and (
                not isinstance(value, str)
                or not 1 <= len(value) <= 500
                or value != value.strip()
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("catalogue_context_card_invalid")
        if self.release_date is not None:
            partial_date(self.release_date)
        for number, maximum in (
            (self.duration_ms, 86400000),
            (self.disc_number, 1000),
            (self.track_number, 10000),
        ):
            if number is not None and (type(number) is not int or not 1 <= number <= maximum):
                raise ValueError("catalogue_context_card_invalid")

    def document(self) -> dict[str, str | int | None]:
        return {
            "schema_version": 1,
            "entity_type": self.entity_type.value,
            "entity_id": str(self.entity_id),
            "recording_mbid": str(self.recording_mbid),
            "release_mbid": str(self.release_mbid) if self.release_mbid else None,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "release_date": self.release_date,
            "duration_ms": self.duration_ms,
            "disc_number": self.disc_number,
            "track_number": self.track_number,
            "recording_title": self.recording_title,
            "disambiguation": self.disambiguation,
        }

    def corroborates(self, *, title: str, artist: str, measured_duration_ms: int | None) -> bool:
        """Missing actual performer or measured duration cannot acquire catalogue IDs."""
        if (
            not self.artist
            or not normalized(artist)
            or type(measured_duration_ms) is not int
            or measured_duration_ms <= 0
            or self.duration_ms is None
            or abs(measured_duration_ms - self.duration_ms) > 2000
        ):
            return False
        actual_title = lookup_title(title, artist)
        titles = {normalized(lookup_title(self.title, self.artist))}
        if self.recording_title:
            titles.add(normalized(lookup_title(self.recording_title, self.artist)))
        if (
            not normalized(actual_title)
            or normalized(artist) != normalized(self.artist)
            or normalized(actual_title) not in titles
        ):
            return False
        versions = version_markers(
            self.title + " " + (self.recording_title or "") + " " + (self.disambiguation or "")
        )
        return version_markers(actual_title) == versions


def parse_catalogue_card(document: Mapping[str, object]) -> CatalogueTrackCard:
    """Revalidate the exact versioned persisted shape before a worker can use it."""
    keys = {
        "schema_version",
        "entity_type",
        "entity_id",
        "recording_mbid",
        "title",
        "artist",
        "release_mbid",
        "album",
        "release_date",
        "duration_ms",
        "disc_number",
        "track_number",
        "recording_title",
        "disambiguation",
    }
    if (
        set(document) != keys
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
    ):
        raise ValueError("catalogue_context_card_invalid")
    # Runtime checks live in the value type; the persisted JSON remains untrusted input.
    values = {key: value for key, value in document.items() if key != "schema_version"}
    values["entity_type"] = CatalogueContextEntity(str(values["entity_type"]))
    for key in ("entity_id", "recording_mbid", "release_mbid"):
        values[key] = UUID(str(values[key])) if values[key] is not None else None
    return CatalogueTrackCard(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CatalogueLookupContext:
    context_id: UUID
    card: CatalogueTrackCard
    observed_at: datetime
    context_sha256: bytes

    def corroborates(self, query: MetadataQuery) -> bool:
        """The worker supplies independently evidenced title/credit and measured duration."""
        if (
            query.album
            and self.card.album
            and normalized(query.album) != normalized(self.card.album)
        ):
            return False
        for actual, expected in (
            (query.recording_mbid, str(self.card.recording_mbid)),
            (query.release_mbid, str(self.card.release_mbid) if self.card.release_mbid else None),
            (
                query.release_track_mbid,
                str(self.card.entity_id)
                if self.card.entity_type == CatalogueContextEntity.RELEASE_TRACK
                else None,
            ),
            (query.disc_number, self.card.disc_number),
            (query.track_number, self.card.track_number),
        ):
            if actual is not None and expected != actual:
                return False
        if (
            query.release_date
            and self.card.release_date
            and not (
                query.release_date.startswith(self.card.release_date)
                or self.card.release_date.startswith(query.release_date)
            )
        ):
            return False
        return self.card.corroborates(
            title=query.title, artist=query.artist, measured_duration_ms=query.duration_ms
        )

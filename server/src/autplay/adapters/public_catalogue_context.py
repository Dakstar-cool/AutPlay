"""Hydrate one real MusicBrainz recording or exact member of a bounded release."""

from uuid import UUID

from autplay.adapters.public_music_search import (
    _count,
    _credit,
    _duration,
    _partial_date,
    _positive,
    _rows,
    _text,
    _title,
)
from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard
from autplay.domain.music_discovery import MAX_RELEASE_TRACKS
from autplay.ports.music_discovery import MusicDiscoveryHttp
from autplay.ports.track_metadata import MetadataProviderError

_BASE = "https://musicbrainz.org/ws/2/"


class MusicBrainzCatalogueContextProvider:
    def __init__(self, http: MusicDiscoveryHttp) -> None:
        self.http = http

    def hydrate(
        self, entity_type: CatalogueContextEntity, entity_id: UUID, release_id: UUID | None
    ) -> CatalogueTrackCard:
        try:
            if entity_type == CatalogueContextEntity.RECORDING and release_id is None:
                document = self.http.json(
                    _BASE + f"recording/{entity_id}?inc=artist-credits&fmt=json"
                )
                if document.get("id") != str(entity_id):
                    raise MetadataProviderError("metadata_entity_missing", retryable=False)
                return CatalogueTrackCard(
                    entity_type,
                    entity_id,
                    entity_id,
                    _title(document.get("title")),
                    _credit(document.get("artist-credit")),
                    release_date=_partial_date(document.get("first-release-date")),
                    duration_ms=_duration(document.get("length")),
                    recording_title=_title(document.get("title")),
                    disambiguation=_text(document.get("disambiguation")),
                )
            if entity_type != CatalogueContextEntity.RELEASE_TRACK or release_id is None:
                raise ValueError("invalid entity")
            document = self.http.json(
                _BASE + f"release/{release_id}?inc=recordings+artist-credits&fmt=json"
            )
            if document.get("id") != str(release_id):
                raise MetadataProviderError("metadata_entity_missing", retryable=False)
            media = _rows(document.get("media"), 1000)
            if "medium-count" in document and _count(document["medium-count"]) != len(media):
                raise ValueError("partial release")
            count = 0
            identities: set[UUID] = set()
            matches: list[CatalogueTrackCard] = []
            for medium in media:
                disc = _positive(medium.get("position"), 1000)
                rows = _rows(medium.get("tracks"), MAX_RELEASE_TRACKS)
                if "track-count" in medium and _count(medium["track-count"]) != len(rows):
                    raise ValueError("partial medium")
                count += len(rows)
                if count > MAX_RELEASE_TRACKS:
                    raise ValueError("release too large")
                for row in rows:
                    identity = UUID(row["id"])
                    if identity in identities:
                        raise ValueError("duplicate release track")
                    identities.add(identity)
                    if identity != entity_id:
                        continue
                    recording = row.get("recording")
                    if not isinstance(recording, dict):
                        raise ValueError("invalid recording")
                    matches.append(
                        CatalogueTrackCard(
                            entity_type,
                            identity,
                            UUID(recording["id"]),
                            _title(row.get("title") or recording.get("title")),
                            _credit(row.get("artist-credit"))
                            or _credit(recording.get("artist-credit")),
                            release_id,
                            _title(document.get("title")),
                            _partial_date(document.get("date")),
                            _duration(
                                row.get("length")
                                if row.get("length") is not None
                                else recording.get("length")
                            ),
                            disc,
                            _positive(row.get("position"), 10000),
                            _text(recording.get("title")),
                            _text(recording.get("disambiguation")),
                        )
                    )
            if len(matches) != 1:
                raise MetadataProviderError("metadata_entity_missing", retryable=False)
            return matches[0]
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise MetadataProviderError("metadata_response_invalid", retryable=False) from error

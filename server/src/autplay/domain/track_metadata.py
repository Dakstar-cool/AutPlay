"""Versioned descriptive metadata; evidence never merges recording identities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal
from uuid import UUID

MetadataValue = str | int | list[str] | None
MetadataFields = dict[str, MetadataValue]
MetadataSource = Literal["EMBEDDED", "MUSICBRAINZ", "USER"]
TEXT_FIELDS = frozenset({"title", "artist", "album", "album_artist", "label", "country"})
DATE_FIELDS = frozenset({"release_date", "original_release_date", "recording_date"})
ID_FIELDS = frozenset({"mb_recording_id", "mb_release_id", "mb_release_group_id"})
NUMBER_FIELDS = frozenset({"track_number", "disc_number"})
EDITABLE_FIELDS = TEXT_FIELDS | DATE_FIELDS | NUMBER_FIELDS | {"genres"}
ALL_FIELDS = EDITABLE_FIELDS | ID_FIELDS
CONTRACT_VERSION = "track-metadata-v1"


def partial_date(value: str) -> str:
    """Retain actual precision: a known year must not become January 1."""
    if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value):
        raise ValueError("metadata_date_invalid")
    parts = [int(part) for part in value.split("-")]
    date(parts[0], parts[1] if len(parts) >= 2 else 1, parts[2] if len(parts) == 3 else 1)
    return value


def validate_fields(raw: dict[str, object], *, manual: bool = False) -> MetadataFields:
    """Validate a bounded field patch without silently accepting unknown keys."""
    allowed = EDITABLE_FIELDS if manual else ALL_FIELDS
    if set(raw) - allowed:
        raise ValueError("metadata_field_unknown")
    result: MetadataFields = {}
    for key, value in raw.items():
        if value is None:
            result[key] = None
        elif key in NUMBER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9999:
                raise ValueError("metadata_number_invalid")
            result[key] = value
        elif key == "genres":
            if not isinstance(value, list) or len(value) > 12:
                raise ValueError("metadata_genres_invalid")
            genres: list[str] = []
            for item in value:
                if not isinstance(item, str) or not 1 <= len(item.strip()) <= 100:
                    raise ValueError("metadata_genres_invalid")
                if item.strip() not in genres:
                    genres.append(item.strip())
            result[key] = genres
        elif isinstance(value, str) and 1 <= len(value.strip()) <= 500:
            cleaned = value.strip()
            if any(ord(c) < 32 for c in cleaned):
                raise ValueError("metadata_text_invalid")
            if key in DATE_FIELDS:
                cleaned = partial_date(cleaned)
            if key in ID_FIELDS:
                cleaned = str(UUID(cleaned))
            result[key] = cleaned
        else:
            raise ValueError("metadata_value_invalid")
    return result


@dataclass(frozen=True)
class FieldEvidence:
    source: MetadataSource
    source_id: str
    observed_at: str
    locked: bool = False


@dataclass(frozen=True)
class MetadataDocument:
    fields: MetadataFields = field(default_factory=dict)
    provenance: dict[str, FieldEvidence] = field(default_factory=dict)

    def merge(
        self, fields: MetadataFields, evidence: FieldEvidence, *, fill_only: bool = False
    ) -> MetadataDocument:
        """Background refresh never overwrites a user value, including an explicit blank."""
        validated = validate_fields(dict(fields), manual=evidence.source == "USER")
        if evidence.source == "USER":
            evidence = replace(evidence, locked=True)
        values = dict(self.fields)
        sources = dict(self.provenance)
        for key, value in validated.items():
            old = sources.get(key)
            if (
                evidence.source != "USER"
                and old is not None
                and (old.locked or old.source == "USER")
            ):
                continue
            if evidence.source != "USER" and value is None:
                continue
            if fill_only and values.get(key) is not None:
                continue
            values[key] = value
            sources[key] = evidence
        return MetadataDocument(values, sources)


def normalized(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", value).casefold()).split())


@dataclass(frozen=True)
class MetadataQuery:
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    recording_mbid: str | None = None


@dataclass(frozen=True)
class MetadataCandidate:
    candidate_id: str
    fields: MetadataFields
    duration_ms: int | None
    score: int
    source_id: str
    auto_eligible: bool = True

    def exact_for(self, query: MetadataQuery) -> bool:
        """Only a conservative text/duration/edition agreement can auto-fill fields."""
        if not self.auto_eligible:
            return False
        title, artist = self.fields.get("title"), self.fields.get("artist")
        if not isinstance(title, str) or not isinstance(artist, str):
            return False
        if not normalized(query.title) or not normalized(query.artist):
            return False
        if normalized(title) != normalized(query.title) or normalized(artist) != normalized(
            query.artist
        ):
            return False
        if query.album and normalized(str(self.fields.get("album") or "")) != normalized(
            query.album
        ):
            return False
        if query.recording_mbid and self.fields.get("mb_recording_id") != query.recording_mbid:
            return False
        if query.duration_ms is None or self.duration_ms is None:
            return query.recording_mbid is not None
        return abs(query.duration_ms - self.duration_ms) <= 2000 and self.score >= 95


def automatic_candidate(
    query: MetadataQuery, candidates: tuple[MetadataCandidate, ...]
) -> MetadataCandidate | None:
    """Different releases remain ambiguous even when they contain the same recording."""
    exact = tuple(candidate for candidate in candidates if candidate.exact_for(query))
    return exact[0] if len(exact) == 1 else None

"""Versioned descriptive metadata; evidence never merges recording identities."""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Literal, cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

MetadataValue = str | int | list[str] | None
MetadataFields = dict[str, MetadataValue]
MetadataSource = Literal["EMBEDDED", "SOURCE_NATIVE", "MUSICBRAINZ", "USER"]
TEXT_FIELDS = frozenset({"title", "artist", "album", "album_artist", "label", "country"})
DATE_FIELDS = frozenset({"release_date", "original_release_date", "recording_date"})
ID_FIELDS = frozenset({"mb_recording_id", "mb_release_id", "mb_release_group_id"})
NUMBER_FIELDS = frozenset({"track_number", "disc_number"})
EDITABLE_FIELDS = TEXT_FIELDS | DATE_FIELDS | NUMBER_FIELDS | {"genres"}
ALL_FIELDS = EDITABLE_FIELDS | ID_FIELDS
CONTRACT_VERSION = "track-metadata-v1"
NORMALIZATION_VERSION = "lookup-v1"
MAX_SOURCE_METADATA_BYTES = 16384
SOURCE_FIELDS = ALL_FIELDS - {"recording_date", "label", "country"}


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
    normalization_version: str = NORMALIZATION_VERSION


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


_TITLE_NOISE = frozenset(
    {
        "official video",
        "official music video",
        "official audio",
        "official lyric video",
        "lyric video",
        "lyrics video",
        "lyrics",
        "visualizer",
        "official visualizer",
        "hd",
        "hq",
        "4k",
        "официальный клип",
        "официальное видео",
        "текст песни",
    }
)
_VERSION_MARKERS = {
    "live": r"\b(?:live|концертная|концертный)\b",
    "remix": r"\b(?:remix|ремикс)\b",
    "cover": r"\b(?:cover|кавер)\b",
    "sped_up": r"\b(?:sped\s*up|speed\s*up|nightcore)\b",
    "slowed": r"\b(?:slowed|slow\s*down)\b",
    "instrumental": r"\b(?:instrumental|инструментал)\b",
    "acoustic": r"\b(?:acoustic|акустическая|акустический)\b",
    "karaoke": r"\b(?:karaoke|караоке)\b",
    "demo": r"\bdemo\b",
    "radio_edit": r"\bradio\s+edit\b",
    "extended": r"\bextended\b",
    "remaster": r"\bremaster(?:ed)?\b",
    "edit": r"\bedit\b",
    "mono": r"\bmono\b",
    "stereo": r"\bstereo\b",
}


def lookup_title(value: str, artist: str = "") -> str:
    """Remove only explicit presentation wrappers, never recording version qualifiers."""
    result = unicodedata.normalize("NFKC", value).strip()
    # A source may repeat its real credited artist before the title. Never infer
    # an artist from this separator or from an uploader name.
    pieces = re.split(r"\s+[-\u2013\u2014]\s+", result, maxsplit=1)
    if len(pieces) == 2 and artist and normalized(pieces[0]) == normalized(artist):
        result = pieces[1]

    def bracket(match: re.Match[str]) -> str:
        return " " if normalized(match.group(1)) in _TITLE_NOISE else match.group(0)

    result = re.sub(r"[\[(]([^\[\]()]+)[\])]", bracket, result)
    # Suffixes must have a separator; a song actually named 'Lyrics' stays intact.
    pieces = re.split(r"\s+[-|\u2013\u2014]\s+", result)
    while len(pieces) > 1 and normalized(pieces[-1]) in _TITLE_NOISE:
        pieces.pop()
    return " ".join(" - ".join(pieces).split())


def version_markers(value: str) -> frozenset[str]:
    text = normalized(value)
    return frozenset(key for key, pattern in _VERSION_MARKERS.items() if re.search(pattern, text))


def _bounded_text(value: object, *, maximum: int = 500) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(unicodedata.category(c) == "Cc" for c in value)
    ):
        raise ValueError("metadata_evidence_text_invalid")
    return value


def _opaque_source_id(value: object) -> str:
    identity = _bounded_text(value)
    if (
        "://" in identity
        or "\\" in identity
        or identity.startswith(("/", "file:", "data:"))
        or re.match(r"^[A-Za-z]:[/\\]", identity)
        or any(c in identity for c in "?&#=@")
    ):
        raise ValueError("metadata_evidence_source_id_invalid")
    return identity


def _source_art_url(value: object) -> str:
    url = _bounded_text(value, maximum=2048)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not host
            or "." not in host
            or "\\" in url
            or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid"))
            or any(c.isspace() for c in url)
        ):
            raise ValueError("url")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            encoded = host.encode("idna").decode("ascii")
            if (
                not all(
                    re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", p)
                    for p in encoded.split(".")
                )
                or encoded.split(".")[-1].isdecimal()
            ):
                raise ValueError("host") from None
        else:
            if not address.is_global:
                raise ValueError("address")
        if parsed.query:
            # Only the documented non-secret native image selector is retained.
            # This is evidence validation, never permission to fetch a new host.
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if (
                host != "usercontent.jamendo.com"
                or len(pairs) > 4
                or len({key for key, _ in pairs}) != len(pairs)
                or any(
                    key not in {"type", "id", "width", "trackid"}
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", part) is None
                    for key, part in pairs
                )
            ):
                raise ValueError("query")
    except (ValueError, UnicodeError) as error:
        raise ValueError("metadata_evidence_artwork_url_invalid") from error
    return url


@dataclass(frozen=True)
class SourceArtwork:
    kind: str
    url: str
    source_id: str


@dataclass(frozen=True)
class SourceMetadata:
    schema_version: int
    provider: str
    source_id: str
    fields: MetadataFields
    external_ids: dict[str, str] = field(default_factory=dict)
    artwork: tuple[SourceArtwork, ...] = ()


def source_metadata_document(value: SourceMetadata) -> dict[str, object]:
    return asdict(value) | {"artwork": [asdict(item) for item in value.artwork]}


def parse_source_metadata(raw: object) -> SourceMetadata:
    """Validate acquisition evidence without fetching its URLs or guessing any fields."""
    if not isinstance(raw, dict):
        raise ValueError("metadata_evidence_invalid")
    try:
        size = len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError("metadata_evidence_invalid") from error
    if size > MAX_SOURCE_METADATA_BYTES:
        raise ValueError("metadata_evidence_too_large")
    if (
        set(raw) - {"schema_version", "provider", "source_id", "fields", "external_ids", "artwork"}
        or not {"schema_version", "provider", "source_id", "fields"} <= set(raw)
        or type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or not isinstance(raw["fields"], dict)
        or set(raw["fields"]) - SOURCE_FIELDS
    ):
        raise ValueError("metadata_evidence_invalid")
    provider = _bounded_text(raw["provider"], maximum=100)
    source_id = _opaque_source_id(raw["source_id"])
    fields = validate_fields(raw["fields"])
    external = raw.get("external_ids", {})
    if not isinstance(external, dict) or set(external) - {
        "isrc",
        "native_album_id",
        "native_artist_id",
    }:
        raise ValueError("metadata_evidence_external_id_invalid")
    external_ids: dict[str, str] = {}
    if "isrc" in external:
        isrc = _bounded_text(external["isrc"], maximum=12)
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", isrc) is None:
            raise ValueError("metadata_evidence_external_id_invalid")
        external_ids["isrc"] = isrc
    for key in ("native_album_id", "native_artist_id"):
        if key in external:
            identity = _bounded_text(external[key], maximum=128)
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", identity) is None:
                raise ValueError("metadata_evidence_external_id_invalid")
            external_ids[key] = identity
    pictures = raw.get("artwork", [])
    if not isinstance(pictures, list) or len(pictures) > 4:
        raise ValueError("metadata_evidence_artwork_invalid")
    artwork: list[SourceArtwork] = []
    for item in pictures:
        if (
            not isinstance(item, dict)
            or set(item) != {"kind", "url", "source_id"}
            or not isinstance(item["kind"], str)
            or item["kind"] not in {"album", "track", "thumbnail", "avatar", "waveform"}
        ):
            raise ValueError("metadata_evidence_artwork_invalid")
        artwork.append(
            SourceArtwork(
                item["kind"], _source_art_url(item["url"]), _opaque_source_id(item["source_id"])
            )
        )
    return SourceMetadata(1, provider, source_id, fields, external_ids, tuple(artwork))


def sanitize_source_metadata(raw: object) -> SourceMetadata:
    """Invalid optional evidence cannot discard otherwise valid acquisition fields."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
        raise ValueError("metadata_evidence_invalid")
    # Check the original size, not just the cleaned document, before inspecting leaves.
    try:
        size = len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError("metadata_evidence_invalid") from error
    if size > MAX_SOURCE_METADATA_BYTES:
        raise ValueError("metadata_evidence_too_large")
    cleaned = {k: v for k, v in raw.items() if k not in {"fields", "external_ids", "artwork"}}
    result: MetadataFields = {}
    for key, value in raw["fields"].items():
        if key not in SOURCE_FIELDS:
            continue
        try:
            result.update(validate_fields({key: value}))
        except ValueError:
            continue
    cleaned["fields"] = result
    external_ids: dict[str, str] = {}
    if isinstance(raw.get("external_ids"), dict):
        for key, external in raw["external_ids"].items():
            pattern = r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}" if key == "isrc" else r"[A-Za-z0-9._-]{1,128}"
            if (
                key in {"isrc", "native_album_id", "native_artist_id"}
                and isinstance(external, str)
                and re.fullmatch(pattern, external)
            ):
                external_ids[key] = external
    if external_ids:
        cleaned["external_ids"] = external_ids
    pictures: list[dict[str, object]] = []
    if isinstance(raw.get("artwork"), list):
        for picture in raw["artwork"][:4]:
            try:
                validated = parse_source_metadata({**cleaned, "artwork": [picture]})
            except ValueError:
                continue
            pictures.extend(asdict(item) for item in validated.artwork)
    cleaned["artwork"] = pictures
    return parse_source_metadata(cleaned)


@dataclass(frozen=True)
class MetadataQuery:
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    recording_mbid: str | None = None
    release_mbid: str | None = None
    release_date: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    source_metadata: SourceMetadata | None = None
    release_track_mbid: str | None = None


@dataclass(frozen=True)
class MetadataCandidate:
    candidate_id: str
    fields: MetadataFields
    duration_ms: int | None
    score: int
    source_id: str
    auto_eligible: bool = True
    release_status: str | None = None
    release_primary_type: str | None = None
    release_secondary_types: tuple[str, ...] = ()
    release_hydrated: bool = False
    release_position_ambiguous: bool = False
    recording_disambiguation: str | None = None
    recording_title: str | None = None
    mb_release_track_id: str | None = None

    def __post_init__(self) -> None:
        # JSON snapshots represent tuples as arrays; old snapshots omit all new fields.
        if (
            not isinstance(self.release_secondary_types, tuple | list)
            or len(self.release_secondary_types) > 12
            or any(
                not isinstance(item, str) or not 1 <= len(item) <= 500
                for item in self.release_secondary_types
            )
            or type(self.auto_eligible) is not bool
            or type(self.release_hydrated) is not bool
            or type(self.release_position_ambiguous) is not bool
        ):
            raise ValueError("metadata_candidate_invalid")
        object.__setattr__(self, "release_secondary_types", tuple(self.release_secondary_types))
        if self.mb_release_track_id is not None:
            _bounded_text(self.mb_release_track_id)
            object.__setattr__(self, "mb_release_track_id", str(UUID(self.mb_release_track_id)))

    def exact_for(self, query: MetadataQuery) -> bool:
        """Only a conservative text/duration/edition agreement can auto-fill fields."""
        if not self.auto_eligible or self.release_position_ambiguous:
            return False
        title, artist = self.fields.get("title"), self.fields.get("artist")
        if not isinstance(title, str) or not isinstance(artist, str):
            return False
        if not normalized(query.title) or not normalized(query.artist):
            return False
        query_title = lookup_title(query.title, query.artist)
        candidate_title = lookup_title(title, artist)
        if version_markers(query_title) != version_markers(
            candidate_title
            + " "
            + (self.recording_disambiguation or "")
            + " "
            + (self.recording_title or "")
            + " "
            + " ".join(self.release_secondary_types)
        ):
            return False
        if any(normalized(kind) == "compilation" for kind in self.release_secondary_types) and not (
            query.album or query.release_mbid
        ):
            return False
        if self.release_status is not None and normalized(self.release_status) != "official":
            return False
        titles = {normalized(candidate_title)}
        if self.release_hydrated and self.recording_title:
            titles.add(normalized(lookup_title(self.recording_title, artist)))
        if normalized(query_title) not in titles or normalized(artist) != normalized(query.artist):
            return False
        if query.album and normalized(str(self.fields.get("album") or "")) != normalized(
            query.album
        ):
            return False
        if query.recording_mbid and self.fields.get("mb_recording_id") != query.recording_mbid:
            return False
        if query.release_mbid and self.fields.get("mb_release_id") != query.release_mbid:
            return False
        if query.release_track_mbid and (
            not query.recording_mbid
            or not query.release_mbid
            or not self.release_hydrated
            or self.mb_release_track_id != query.release_track_mbid
        ):
            return False
        if query.release_date:
            release_date = self.fields.get("release_date")
            if not isinstance(release_date, str) or not release_date.startswith(query.release_date):
                return False
        if self.release_hydrated:
            if normalized(self.release_status or "") != "official":
                return False
            for key in ("disc_number", "track_number"):
                expected = getattr(query, key)
                if expected is not None and self.fields.get(key) != expected:
                    return False
        if query.duration_ms is None or self.duration_ms is None:
            return query.recording_mbid is not None
        return abs(query.duration_ms - self.duration_ms) <= 2000 and self.score >= 95


@dataclass(frozen=True)
class AlbumGroupV1:
    schema_version: int
    key: str
    provider: str
    release_id: str
    title: str
    album_artist: str | None
    release_date: str | None
    disc_number: int | None
    track_number: int | None
    evidence: FieldEvidence


def album_group_document(value: AlbumGroupV1) -> dict[str, object]:
    return asdict(value)


def parse_album_group(raw: object) -> AlbumGroupV1:
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "key",
        "provider",
        "release_id",
        "title",
        "album_artist",
        "release_date",
        "disc_number",
        "track_number",
        "evidence",
    }:
        raise ValueError("metadata_album_group_invalid")
    provider = _bounded_text(raw["provider"], maximum=100)
    if provider == "MUSICBRAINZ":
        identity = str(UUID(_bounded_text(raw["release_id"])))
        key = f"musicbrainz:release:{identity}"
    else:
        identity = _bounded_text(raw["release_id"], maximum=128)
        if (
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", provider) is None
            or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", identity) is None
        ):
            raise ValueError("metadata_album_group_invalid")
        key = f"native:{provider.lower()}:album:{identity}"
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["key"] != key:
        raise ValueError("metadata_album_group_invalid")
    values = validate_fields(
        {
            "album": raw["title"],
            "album_artist": raw["album_artist"],
            "release_date": raw["release_date"],
            "disc_number": raw["disc_number"],
            "track_number": raw["track_number"],
        }
    )
    evidence = raw["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {
        "source",
        "source_id",
        "observed_at",
        "normalization_version",
        "locked",
    }:
        raise ValueError("metadata_album_group_invalid")
    source: MetadataSource = "MUSICBRAINZ" if provider == "MUSICBRAINZ" else "SOURCE_NATIVE"
    if (
        evidence["source"] != source
        or type(evidence["locked"]) is not bool
        or (provider == "MUSICBRAINZ" and evidence["source_id"] != key)
    ):
        raise ValueError("metadata_album_group_invalid")
    observed_at = _bounded_text(evidence["observed_at"], maximum=100)
    datetime.fromisoformat(observed_at)
    proof = FieldEvidence(
        source,
        _opaque_source_id(evidence["source_id"]),
        observed_at,
        evidence["locked"],
        _bounded_text(evidence["normalization_version"], maximum=100),
    )
    return AlbumGroupV1(
        1,
        key,
        provider,
        identity,
        _bounded_text(values["album"]),
        _bounded_text(values["album_artist"]) if values["album_artist"] is not None else None,
        cast(str | None, values["release_date"]),
        cast(int | None, values["disc_number"]),
        cast(int | None, values["track_number"]),
        proof,
    )


def album_group_for(candidate: MetadataCandidate, evidence: FieldEvidence) -> AlbumGroupV1 | None:
    """Service must also verify that edition fields were applied and no manual lock conflicts."""
    if (
        not candidate.release_hydrated
        or candidate.release_position_ambiguous
        or (not candidate.auto_eligible and not evidence.locked)
    ):
        return None
    try:
        return parse_album_group(
            {
                "schema_version": 1,
                "key": candidate.source_id,
                "provider": "MUSICBRAINZ",
                "release_id": candidate.fields.get("mb_release_id"),
                "title": candidate.fields.get("album"),
                "album_artist": candidate.fields.get("album_artist"),
                "release_date": candidate.fields.get("release_date"),
                "disc_number": candidate.fields.get("disc_number"),
                "track_number": candidate.fields.get("track_number"),
                "evidence": asdict(evidence),
            }
        )
    except ValueError, TypeError:
        return None


def native_album_group_for(source: SourceMetadata, evidence: FieldEvidence) -> AlbumGroupV1 | None:
    """Native album ID denotes an explicit selected track-to-album association.

    The producer must omit this ID for playlists, ambiguous multi-album tracks,
    uploaders or inferred title groups. The service checks effective field locks
    and contradictory embedded MB release evidence before publishing it.
    """
    identity = source.external_ids.get("native_album_id")
    if not identity or evidence.source != "SOURCE_NATIVE":
        return None
    provider = source.provider.upper()
    # Transport lanes cannot namespace provider-specific album IDs safely.
    if provider in {"YT_DLP", "YTDLP", "MUSIC_SITES", "MUSICBRAINZ", "NATIVE", "UNKNOWN"}:
        return None
    try:
        return parse_album_group(
            {
                "schema_version": 1,
                "key": f"native:{provider.lower()}:album:{identity}",
                "provider": provider,
                "release_id": identity,
                "title": source.fields.get("album"),
                "album_artist": source.fields.get("album_artist"),
                "release_date": source.fields.get("release_date"),
                "disc_number": source.fields.get("disc_number"),
                "track_number": source.fields.get("track_number"),
                "evidence": asdict(evidence),
            }
        )
    except ValueError, TypeError:
        return None


def automatic_candidate(
    query: MetadataQuery, candidates: tuple[MetadataCandidate, ...]
) -> MetadataCandidate | None:
    """Different releases remain ambiguous even when they contain the same recording."""
    exact = tuple(candidate for candidate in candidates if candidate.exact_for(query))
    return exact[0] if len(exact) == 1 else None

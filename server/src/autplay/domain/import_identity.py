"""Pure P10 import parsing, identity matching, and benchmark contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from html.parser import HTMLParser
from typing import ClassVar, Final
from uuid import UUID

import rfc8785

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

IMPORT_ENVELOPE_VERSION: Final = "1"
IMPORT_ADAPTER_ID: Final = "autplay.generic-user-export"
IMPORT_ADAPTER_VERSION: Final = "1.0.0"
MATCHER_VERSION: Final = "autplay.identity-shadow/1.0.0"
CANDIDATE_GENERATION_VERSION: Final = "autplay.candidates/1.0.0"
NORMALIZATION_VERSION: Final = "autplay.normalize/1.0.0"
FEATURE_EXTRACTOR_VERSIONS: Final[dict[str, JsonValue]] = {
    "metadata": "1.0.0",
    "duration": "1.0.0",
    "version_markers": "1.0.0",
    "fingerprint": "1.0.0",
}
MAX_IMPORT_BYTES: Final = 2 * 1024 * 1024
MAX_IMPORT_ROWS: Final = 10_000
MAX_ROW_BYTES: Final = 64 * 1024
MAX_FIELDS_PER_ROW: Final = 256
MAX_FIELD_CHARS: Final = 4_000
MAX_CANDIDATES: Final = 100
REVIEW_THRESHOLD: Final = 0.75
MARGIN_THRESHOLD: Final = 0.08

_SPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w]+", re.UNICODE)
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:access|auth|refresh)?_?token(?:$|_)|"
    r"(?:^|_)(?:api_key|authorization|cookie|password|passwd|secret|credential)(?:$|_)",
    re.IGNORECASE,
)
_KNOWN_FIELDS: Final = frozenset(
    {
        "title",
        "track",
        "track_title",
        "name",
        "artist",
        "artists",
        "artist_name",
        "album",
        "release",
        "release_title",
        "duration_ms",
        "duration",
        "external_id",
        "track_id",
        "id",
        "available",
        "is_available",
        "unavailable",
        "status",
        "provider",
        "provider_key",
        "market",
        "playlist",
        "playlist_id",
        "position",
        "isrc",
        "mbid",
        "sha256",
        "fingerprint",
        "fingerprint_algorithm",
        "fingerprint_version",
        "fingerprint_coverage",
    }
)
_MARKERS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (category, re.compile(pattern, re.IGNORECASE))
    for category, pattern in (
        ("RADIO_EDIT", r"\bradio[\s_-]*edit\b"),
        ("INSTRUMENTAL", r"\binstrumental\b"),
        ("KARAOKE", r"\bkaraoke\b"),
        ("REHEARSAL", r"\brehearsal\b"),
        ("REMASTER", r"\bremaster(?:ed)?\b"),
        ("ACOUSTIC", r"\bacoustic\b"),
        ("EXTENDED", r"\bextended\b"),
        ("REMIX", r"\bremix\b"),
        ("LIVE", r"\blive\b"),
        ("DEMO", r"\bdemo\b"),
        ("COVER", r"\bcover\b"),
        ("MONO", r"\bmono\b"),
        ("STEREO", r"\bstereo\b"),
        ("EDIT", r"\bedit\b"),
    )
)


class ImportFormat(StrEnum):
    """Supported generic user-owned export containers."""

    CSV = "CSV"
    JSON = "JSON"
    HTML = "HTML"


class ImportEnvelopeError(ValueError):
    """The bounded outer envelope is invalid and cannot isolate rows safely."""

    code: ClassVar[str] = "import.envelope_invalid"


@dataclass(frozen=True, slots=True)
class ImportEnvelope:
    """One versioned, bounded user-owned export envelope."""

    format: ImportFormat
    content: bytes
    schema_version: str = IMPORT_ENVELOPE_VERSION
    adapter_id: str = IMPORT_ADAPTER_ID
    adapter_version: str = IMPORT_ADAPTER_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != IMPORT_ENVELOPE_VERSION:
            raise ImportEnvelopeError("import.schema_version_unsupported")
        if not self.content or len(self.content) > MAX_IMPORT_BYTES:
            raise ImportEnvelopeError("import.input_size_invalid")
        if not 1 <= len(self.adapter_id) <= 200 or not 1 <= len(self.adapter_version) <= 100:
            raise ImportEnvelopeError("import.adapter_identity_invalid")

    @property
    def input_sha256(self) -> bytes:
        """Return the deterministic idempotency digest of the exact input bytes."""

        return hashlib.sha256(self.content).digest()


@dataclass(frozen=True, slots=True)
class ParsedImportRow:
    """A valid or isolated malformed row with stable provenance."""

    source_row_key: str
    row_number: int
    raw_fields: dict[str, JsonValue]
    title: str = ""
    artist: str = ""
    album: str | None = None
    duration_ms: int | None = None
    external_id: str | None = None
    availability: str = "UNKNOWN"
    unknown_field_count: int = 0
    error_code: str | None = None

    @property
    def valid(self) -> bool:
        """Return whether the row has the minimum identity metadata."""

        return self.error_code is None


@dataclass(frozen=True, slots=True)
class ParsedImport:
    """Deterministic parser output including row-isolated failures."""

    envelope: ImportEnvelope
    encoding: str
    rows: tuple[ParsedImportRow, ...]

    @property
    def valid_count(self) -> int:
        return sum(row.valid for row in self.rows)

    @property
    def malformed_count(self) -> int:
        return len(self.rows) - self.valid_count


@dataclass(frozen=True, slots=True)
class FingerprintEvidence:
    """Versioned fingerprint evidence; versions are never compared implicitly."""

    algorithm: str
    algorithm_version: str
    value_hash: str
    coverage: float

    def __post_init__(self) -> None:
        if not self.algorithm or not self.algorithm_version or not self.value_hash:
            raise ValueError("fingerprint identity fields must be non-empty")
        if not math.isfinite(self.coverage) or not 0 <= self.coverage <= 1:
            raise ValueError("fingerprint coverage must be between zero and one")


@dataclass(frozen=True, slots=True)
class IdentityTrack:
    """Sanitized matcher input without private source payloads."""

    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_ms: int | None = None
    version_markers: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    fingerprint: FingerprintEvidence | None = None
    verified_vault_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.title or not self.artists:
            raise ValueError("identity track requires title and artist")
        if self.duration_ms is not None and self.duration_ms <= 0:
            raise ValueError("duration_ms must be positive")


@dataclass(frozen=True, slots=True)
class CatalogCandidate:
    """One bounded catalog snapshot used by the pure matcher."""

    recording_id: UUID
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    duration_ms: int | None = None
    version_markers: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    fingerprint: FingerprintEvidence | None = None
    verified_vault_sha256: str | None = None
    origins: tuple[str, ...] = ("NORMALIZED_METADATA",)


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One explainable, versioned candidate score."""

    recording_id: UUID
    raw_score: float
    confidence: float
    evidence_tier: str
    feature_scores: tuple[dict[str, JsonValue], ...]
    hard_conflicts: tuple[str, ...]
    candidate_origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchEvaluation:
    """Review-only P10 result; probabilistic auto-match is intentionally absent."""

    state: str
    candidates: tuple[ScoredCandidate, ...]
    margin: float | None
    explanation: dict[str, JsonValue]
    evidence_mode: str


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One labeled identity pair-set, including explicit hard negatives."""

    case_id: str
    error_slice: str
    query: IdentityTrack
    candidates: tuple[CatalogCandidate, ...]
    expected_recording_id: UUID | None


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Deterministic binary confusion report for the shadow matcher."""

    dataset_id: str
    dataset_version: str
    dataset_sha256: str
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    confusion: dict[str, int]
    error_slices: dict[str, dict[str, int]]
    auto_match_eligible: bool
    gate_reasons: tuple[str, ...]


def parse_import(envelope: ImportEnvelope) -> ParsedImport:
    """Parse a supported export while isolating malformed rows."""

    text, encoding = _decode(envelope.content)
    if envelope.format is ImportFormat.CSV:
        rows = _parse_csv(text)
    elif envelope.format is ImportFormat.JSON:
        rows = _parse_json(text, envelope.schema_version)
    else:
        rows = _parse_html(text)
    if len(rows) > MAX_IMPORT_ROWS:
        raise ImportEnvelopeError("import.row_limit_exceeded")
    return ParsedImport(envelope, encoding, tuple(rows))


def identity_track_from_row(row: ParsedImportRow) -> IdentityTrack:
    """Build a sanitized matcher query while retaining raw fields elsewhere."""

    if not row.valid:
        raise ValueError("malformed import row cannot become an identity query")
    markers = extract_version_markers(" ".join(filter(None, (row.title, row.album))))
    identifiers: dict[str, str] = {}
    provider = _scalar_text(row.raw_fields.get("provider_key") or row.raw_fields.get("provider"))
    if row.external_id and provider:
        identifiers[f"provider:{normalize_text(provider)}"] = row.external_id
    isrc = _scalar_text(row.raw_fields.get("isrc"))
    if isrc:
        identifiers["isrc"] = isrc.upper().replace("-", "")
    mbid = _scalar_text(row.raw_fields.get("mbid"))
    if mbid:
        identifiers["mbid"] = mbid.casefold()
    fingerprint = _fingerprint_from_raw(row.raw_fields)
    return IdentityTrack(
        title=row.title,
        artists=(row.artist,),
        album=row.album,
        duration_ms=row.duration_ms,
        version_markers=markers,
        identifiers=identifiers,
        fingerprint=fingerprint,
    )


def normalize_text(value: str) -> str:
    """Create a locale-independent NFKC search copy without mutating display text."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _PUNCTUATION.sub(" ", normalized)
    return _SPACE.sub(" ", normalized).strip()


def extract_version_markers(value: str) -> tuple[str, ...]:
    """Extract the closed v1 marker categories without treating absence as studio."""

    found: list[str] = []
    masked = value
    for category, pattern in _MARKERS:
        if pattern.search(masked):
            found.append(category)
            masked = pattern.sub(" ", masked)
    return tuple(found)


def evaluate_identity(
    query: IdentityTrack,
    catalog: Iterable[CatalogCandidate],
    *,
    candidate_limit: int = MAX_CANDIDATES,
) -> MatchEvaluation:
    """Generate, score, and explain candidates with auto-match disabled."""

    if not 1 <= candidate_limit <= MAX_CANDIDATES:
        raise ValueError("candidate_limit must be between one and one hundred")
    generated = _generate_candidates(query, catalog)[:candidate_limit]
    scored = tuple(sorted((_score(query, item) for item in generated), key=_score_order))
    if not scored:
        return MatchEvaluation(
            state="NO_MATCH",
            candidates=(),
            margin=None,
            explanation={"reason_codes": ["NO_CANDIDATES"], "auto_match_enabled": False},
            evidence_mode="METADATA_ONLY",
        )
    top = scored[0]
    top2 = scored[1].confidence if len(scored) > 1 else None
    margin = None if top2 is None else round(max(0.0, top.confidence - top2), 6)
    integrity = any(
        conflict in {"FINGERPRINT_MISMATCH", "EXTERNAL_TARGET_CONFLICT", "SHA_AMBIGUITY"}
        for conflict in top.hard_conflicts
    )
    reasons: list[JsonValue] = ["AUTO_MATCH_DISABLED"]
    if top.hard_conflicts:
        reasons.append("HARD_CONFLICT")
    if margin is not None and margin < MARGIN_THRESHOLD:
        reasons.append("TOP_TWO_MARGIN")
    if top.confidence < REVIEW_THRESHOLD:
        reasons.append("BELOW_REVIEW_THRESHOLD")
    state = "INTEGRITY_CONFLICT" if integrity else "REVIEW_REQUIRED"
    evidence_mode = (
        "DETERMINISTIC_BYTES"
        if top.evidence_tier == "T4"
        else ("AUDIO_AVAILABLE" if top.evidence_tier == "T3" else "METADATA_ONLY")
    )
    return MatchEvaluation(
        state=state,
        candidates=scored,
        margin=margin,
        explanation={
            "reason_codes": reasons,
            "auto_match_enabled": False,
            "top_recording_id": str(top.recording_id),
            "top_confidence": top.confidence,
            "top2_confidence": top2,
            "margin": margin,
        },
        evidence_mode=evidence_mode,
    )


def run_benchmark(
    cases: Sequence[BenchmarkCase], *, dataset_id: str, dataset_version: str
) -> BenchmarkReport:
    """Run a deterministic shadow benchmark with confusion and error slices."""

    if not 1 <= len(dataset_id) <= 200 or not 1 <= len(dataset_version) <= 100:
        raise ValueError("benchmark dataset identity is invalid")
    dataset_sha256 = hashlib.sha256(
        rfc8785.dumps(
            {
                "schema_version": 1,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "cases": [_benchmark_case_document(case) for case in cases],
            }
        )
    ).hexdigest()
    tp = fp = tn = fn = 0
    slices: dict[str, Counter[str]] = {}
    for case in cases:
        result = evaluate_identity(case.query, case.candidates)
        predicted = _counterfactual_prediction(result)
        expected = case.expected_recording_id
        bucket: str
        if expected is None and predicted is None:
            tn += 1
            bucket = "true_negative"
        elif expected is None:
            fp += 1
            bucket = "false_positive"
        elif predicted == expected:
            tp += 1
            bucket = "true_positive"
        else:
            fn += 1
            bucket = "false_negative"
        slices.setdefault(case.error_slice, Counter())[bucket] += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    gate_reasons = ["AUTO_MATCH_DISABLED"]
    positives = tp + fn
    hard_negatives = tn + fp
    if positives < 5_000:
        gate_reasons.append("INSUFFICIENT_POSITIVES")
    if hard_negatives < 10_000:
        gate_reasons.append("INSUFFICIENT_HARD_NEGATIVES")
    return BenchmarkReport(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_sha256=dataset_sha256,
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
        precision=round(precision, 6),
        recall=round(recall, 6),
        confusion={"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        error_slices={
            name: dict(sorted(counts.items())) for name, counts in sorted(slices.items())
        },
        auto_match_eligible=False,
        gate_reasons=tuple(gate_reasons),
    )


def _benchmark_case_document(case: BenchmarkCase) -> dict[str, JsonValue]:
    return {
        "case_id": case.case_id,
        "error_slice": case.error_slice,
        "query": _benchmark_track_document(case.query),
        "candidates": [_benchmark_candidate_document(item) for item in case.candidates],
        "expected_recording_id": (
            str(case.expected_recording_id) if case.expected_recording_id is not None else None
        ),
    }


def _benchmark_track_document(track: IdentityTrack) -> dict[str, JsonValue]:
    return {
        "title": track.title,
        "artists": list(track.artists),
        "album": track.album,
        "duration_ms": track.duration_ms,
        "version_markers": list(track.version_markers),
        "identifiers": dict(sorted(track.identifiers.items())),
        "fingerprint": _benchmark_fingerprint_document(track.fingerprint),
        "verified_vault_sha256": track.verified_vault_sha256,
    }


def _benchmark_candidate_document(candidate: CatalogCandidate) -> dict[str, JsonValue]:
    document = _benchmark_track_document(
        IdentityTrack(
            title=candidate.title,
            artists=candidate.artists,
            album=candidate.album,
            duration_ms=candidate.duration_ms,
            version_markers=candidate.version_markers,
            identifiers=candidate.identifiers,
            fingerprint=candidate.fingerprint,
            verified_vault_sha256=candidate.verified_vault_sha256,
        )
    )
    document["recording_id"] = str(candidate.recording_id)
    document["origins"] = list(candidate.origins)
    return document


def _benchmark_fingerprint_document(
    fingerprint: FingerprintEvidence | None,
) -> dict[str, JsonValue] | None:
    if fingerprint is None:
        return None
    return {
        "algorithm": fingerprint.algorithm,
        "algorithm_version": fingerprint.algorithm_version,
        "value_hash": fingerprint.value_hash,
        "coverage": fingerprint.coverage,
    }


def _decode(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ImportEnvelopeError("import.encoding_unsupported")


def _parse_csv(text: str) -> list[ParsedImportRow]:
    physical_lines = text.splitlines()
    if not physical_lines:
        raise ImportEnvelopeError("import.csv_header_missing")
    try:
        header = next(csv.reader([physical_lines[0]], strict=True))
    except csv.Error as error:
        raise ImportEnvelopeError("import.csv_header_malformed") from error
    normalized_header = [_normalize_key(value) for value in header]
    if not normalized_header or len(set(normalized_header)) != len(normalized_header):
        raise ImportEnvelopeError("import.csv_header_invalid")
    rows: list[ParsedImportRow] = []
    for number, line in enumerate(physical_lines[1:], start=2):
        if len(line.encode("utf-8")) > MAX_ROW_BYTES:
            rows.append(_malformed_row(number, {"row_present": True}, "import.row_too_large"))
            continue
        try:
            values = next(csv.reader([line], strict=True))
        except csv.Error:
            rows.append(_malformed_row(number, {"row_present": True}, "import.csv_row_malformed"))
            continue
        mapping: dict[str, JsonValue] = {
            key: values[index] if index < len(values) else ""
            for index, key in enumerate(normalized_header)
        }
        if len(values) != len(normalized_header):
            mapping["_column_count"] = len(values)
            rows.append(_malformed_row(number, mapping, "import.csv_column_count"))
            continue
        rows.append(_row_from_mapping(number, mapping))
    return rows


def _parse_json(text: str, declared_schema: str) -> list[ParsedImportRow]:
    try:
        root = json.loads(text)
    except json.JSONDecodeError as error:
        raise ImportEnvelopeError("import.json_malformed") from error
    if isinstance(root, dict):
        schema = root.get("schema_version", declared_schema)
        if str(schema) != declared_schema:
            raise ImportEnvelopeError("import.schema_version_mismatch")
        values = root.get("tracks")
    else:
        values = root
    if not isinstance(values, list):
        raise ImportEnvelopeError("import.json_tracks_missing")
    rows: list[ParsedImportRow] = []
    for number, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            rows.append(
                _malformed_row(
                    number,
                    {"value_type": type(item).__name__},
                    "import.row_not_object",
                )
            )
            continue
        try:
            sanitized = _bounded_json_object(item)
        except ValueError as error:
            rows.append(_malformed_row(number, {"row_present": True}, str(error)))
            continue
        rows.append(_row_from_mapping(number, sanitized))
    return rows


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in {"script", "style"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and lowered == "tr":
            self._row = []
        elif self._ignored_depth == 0 and lowered in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and lowered in {"td", "th"} and self._cell is not None:
            assert self._row is not None
            self._row.append(_SPACE.sub(" ", "".join(self._cell)).strip())
            self._cell = None
        elif self._ignored_depth == 0 and lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and self._cell is not None:
            self._cell.append(data)


def _parse_html(text: str) -> list[ParsedImportRow]:
    parser = _TableParser()
    try:
        parser.feed(text)
        parser.close()
    except AssertionError as error:
        raise ImportEnvelopeError("import.html_malformed") from error
    if len(parser.rows) < 1:
        raise ImportEnvelopeError("import.html_table_missing")
    header = [_normalize_key(value) for value in parser.rows[0]]
    if not header or len(set(header)) != len(header):
        raise ImportEnvelopeError("import.html_header_invalid")
    rows: list[ParsedImportRow] = []
    for number, values in enumerate(parser.rows[1:], start=2):
        mapping: dict[str, JsonValue] = {
            key: values[index] if index < len(values) else "" for index, key in enumerate(header)
        }
        if len(values) != len(header):
            mapping["_column_count"] = len(values)
            rows.append(_malformed_row(number, mapping, "import.html_column_count"))
        else:
            rows.append(_row_from_mapping(number, mapping))
    return rows


def _row_from_mapping(number: int, supplied: Mapping[str, JsonValue]) -> ParsedImportRow:
    mapping = {_normalize_key(key): value for key, value in supplied.items()}
    if len(mapping) > MAX_FIELDS_PER_ROW:
        return _malformed_row(number, {"field_count": len(mapping)}, "import.row_field_limit")
    title = _first_text(mapping, "title", "track", "track_title", "name")
    artist = _first_text(mapping, "artist", "artists", "artist_name")
    album = _first_text(mapping, "album", "release", "release_title") or None
    external_id = _first_text(mapping, "external_id", "track_id", "id") or None
    try:
        duration = _duration_ms(mapping.get("duration_ms", mapping.get("duration")))
    except ValueError:
        return _malformed_row(number, dict(mapping), "import.duration_invalid")
    if not title or not artist:
        return _malformed_row(number, dict(mapping), "import.required_metadata_missing")
    availability = _availability(mapping)
    canonical = _canonical_row_bytes(mapping)
    return ParsedImportRow(
        source_row_key=f"row:{number:08d}:{hashlib.sha256(canonical).hexdigest()[:16]}",
        row_number=number,
        raw_fields=dict(mapping),
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration,
        external_id=external_id,
        availability=availability,
        unknown_field_count=sum(key not in _KNOWN_FIELDS for key in mapping),
    )


def _malformed_row(number: int, fields: dict[str, JsonValue], code: str) -> ParsedImportRow:
    canonical = _canonical_row_bytes(fields)
    return ParsedImportRow(
        source_row_key=f"row:{number:08d}:{hashlib.sha256(canonical).hexdigest()[:16]}",
        row_number=number,
        raw_fields=fields,
        unknown_field_count=sum(key not in _KNOWN_FIELDS for key in fields),
        error_code=code,
    )


def _bounded_json_object(value: Mapping[object, object]) -> dict[str, JsonValue]:
    if len(value) > MAX_FIELDS_PER_ROW:
        raise ValueError("import.row_field_limit")
    output: dict[str, JsonValue] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 200:
            raise ValueError("import.field_name_invalid")
        output[_normalize_key(raw_key)] = _bounded_json_value(item, depth=0)
    if len(_canonical_row_bytes(output)) > MAX_ROW_BYTES:
        raise ValueError("import.row_too_large")
    return output


def _bounded_json_value(value: object, *, depth: int) -> JsonValue:
    if depth > 8:
        raise ValueError("import.row_nesting_limit")
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) > MAX_FIELD_CHARS:
            raise ValueError("import.field_too_large")
        return value
    if isinstance(value, list):
        if len(value) > MAX_FIELDS_PER_ROW:
            raise ValueError("import.row_field_limit")
        return [_bounded_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        return _bounded_json_object(value)
    raise ValueError("import.field_type_invalid")


def _canonical_row_bytes(value: Mapping[str, JsonValue]) -> bytes:
    try:
        return rfc8785.dumps(dict(value))
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise ImportEnvelopeError("import.row_not_canonicalizable") from error


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_") or "field"


def _first_text(mapping: Mapping[str, JsonValue], *keys: str) -> str:
    for key in keys:
        value = _scalar_text(mapping.get(key))
        if value:
            return value
    return ""


def _scalar_text(value: JsonValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:MAX_FIELD_CHARS]
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "; ".join(item for item in value if isinstance(item, str))[:MAX_FIELD_CHARS]
    return ""


def _duration_ms(value: JsonValue | None) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("invalid duration")
    if isinstance(value, int | float):
        duration = int(value)
    elif isinstance(value, str):
        text_value = value.strip()
        if ":" in text_value:
            pieces = text_value.split(":")
            if len(pieces) not in {2, 3} or any(not piece.isdigit() for piece in pieces):
                raise ValueError("invalid duration")
            seconds = sum(int(piece) * (60**index) for index, piece in enumerate(reversed(pieces)))
            duration = seconds * 1000
        else:
            duration = int(float(text_value))
    else:
        raise ValueError("invalid duration")
    if not 0 < duration <= 24 * 60 * 60 * 1000:
        raise ValueError("invalid duration")
    return duration


def _availability(mapping: Mapping[str, JsonValue]) -> str:
    unavailable = mapping.get("unavailable")
    if unavailable is True or _scalar_text(unavailable).casefold() in {"1", "true", "yes"}:
        return "UNAVAILABLE"
    available = mapping.get("available", mapping.get("is_available"))
    if available is False or _scalar_text(available).casefold() in {"0", "false", "no"}:
        return "UNAVAILABLE"
    status = _scalar_text(mapping.get("status")).casefold()
    if status in {"unavailable", "gray", "grey", "removed"}:
        return "UNAVAILABLE"
    if available is True or status in {"available", "active"}:
        return "AVAILABLE"
    return "UNKNOWN"


def _fingerprint_from_raw(raw: Mapping[str, JsonValue]) -> FingerprintEvidence | None:
    value = _scalar_text(raw.get("fingerprint"))
    algorithm = _scalar_text(raw.get("fingerprint_algorithm"))
    version = _scalar_text(raw.get("fingerprint_version"))
    if not value or not algorithm or not version:
        return None
    coverage_value = raw.get("fingerprint_coverage", 0.0)
    try:
        coverage = float(coverage_value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    if not math.isfinite(coverage) or not 0 <= coverage <= 1:
        return None
    return FingerprintEvidence(algorithm.upper(), version, value, coverage)


def _generate_candidates(
    query: IdentityTrack, catalog: Iterable[CatalogCandidate]
) -> list[CatalogCandidate]:
    generated: list[CatalogCandidate] = []
    query_title = _normalized_base_title(query.title)
    query_artists = {normalize_text(value) for value in query.artists}
    for candidate in catalog:
        title_ratio = SequenceMatcher(
            None, query_title, _normalized_base_title(candidate.title)
        ).ratio()
        artist_ratio = max(
            (
                SequenceMatcher(None, query_artist, normalize_text(candidate_artist)).ratio()
                for query_artist in query_artists
                for candidate_artist in candidate.artists
            ),
            default=0.0,
        )
        identifier_match = bool(
            set(query.identifiers.items()).intersection(candidate.identifiers.items())
        )
        sha_match = bool(
            query.verified_vault_sha256
            and candidate.verified_vault_sha256 == query.verified_vault_sha256
        )
        fingerprint_compatible = bool(
            query.fingerprint
            and candidate.fingerprint
            and query.fingerprint.algorithm == candidate.fingerprint.algorithm
            and query.fingerprint.algorithm_version == candidate.fingerprint.algorithm_version
        )
        if (
            identifier_match
            or sha_match
            or fingerprint_compatible
            or (title_ratio >= 0.55 and artist_ratio >= 0.45)
        ):
            generated.append(candidate)
    generated.sort(key=lambda item: str(item.recording_id))
    return generated


def _score(query: IdentityTrack, candidate: CatalogCandidate) -> ScoredCandidate:
    title = SequenceMatcher(
        None, _normalized_base_title(query.title), _normalized_base_title(candidate.title)
    ).ratio()
    artist = max(
        (
            SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()
            for left in query.artists
            for right in candidate.artists
        ),
        default=0.0,
    )
    duration = None
    if query.duration_ms is not None and candidate.duration_ms is not None:
        duration = math.exp(-abs(query.duration_ms - candidate.duration_ms) / 3_000)
    query_markers = set(query.version_markers or extract_version_markers(query.title))
    candidate_markers = set(candidate.version_markers or extract_version_markers(candidate.title))
    conflicts = _version_conflicts(query_markers, candidate_markers)
    identifier_exact = float(
        bool(set(query.identifiers.items()).intersection(candidate.identifiers.items()))
    )
    fingerprint_similarity: float | None = None
    fingerprint_coverage: float | None = None
    if query.fingerprint and candidate.fingerprint:
        if (
            query.fingerprint.algorithm == candidate.fingerprint.algorithm
            and query.fingerprint.algorithm_version == candidate.fingerprint.algorithm_version
        ):
            fingerprint_coverage = min(query.fingerprint.coverage, candidate.fingerprint.coverage)
            fingerprint_similarity = float(
                query.fingerprint.value_hash == candidate.fingerprint.value_hash
            )
            if fingerprint_similarity == 0 and fingerprint_coverage >= 0.8:
                conflicts.append("FINGERPRINT_MISMATCH")
        else:
            conflicts.append("FINGERPRINT_VERSION_CONFLICT")
    sha_exact = float(
        bool(
            query.verified_vault_sha256
            and query.verified_vault_sha256 == candidate.verified_vault_sha256
        )
    )
    metadata_values = [title * 0.46, artist * 0.34]
    metadata_weight = 0.8
    if duration is not None:
        metadata_values.append(duration * 0.12)
        metadata_weight += 0.12
    version_compatibility = 0.0 if conflicts else 1.0
    metadata_values.append(version_compatibility * 0.08)
    metadata_weight += 0.08
    metadata = sum(metadata_values) / metadata_weight
    audio = None
    if fingerprint_similarity is not None and fingerprint_coverage is not None:
        audio = 0.82 * fingerprint_similarity + 0.18 * fingerprint_coverage
    identifier = identifier_exact
    group_values: list[tuple[float, float]] = [(metadata, 0.34)]
    if identifier:
        group_values.append((identifier, 0.24))
    if audio is not None:
        group_values.append((audio, 0.36))
    denominator = sum(weight for _, weight in group_values)
    raw = sum(value * weight for value, weight in group_values) / denominator
    penalty = 0.7 if conflicts else 0.0
    raw = round(min(1.0, max(0.0, raw - penalty)), 6)
    if sha_exact:
        raw = 1.0
        tier = "T4"
    elif fingerprint_similarity is not None:
        tier = "T3"
    elif identifier_exact:
        tier = "T2"
    elif title >= 0.9 and artist >= 0.9 and duration is not None:
        tier = "T1"
    else:
        tier = "T0"
    features: tuple[dict[str, JsonValue], ...] = (
        {"feature": "title_similarity", "value": round(title, 6), "present": True},
        {"feature": "artist_similarity", "value": round(artist, 6), "present": True},
        {
            "feature": "duration_similarity",
            "value": None if duration is None else round(duration, 6),
            "present": duration is not None,
        },
        {
            "feature": "identifier_exact",
            "value": identifier_exact,
            "present": bool(query.identifiers),
        },
        {
            "feature": "sha256_exact",
            "value": sha_exact,
            "present": query.verified_vault_sha256 is not None,
        },
        {
            "feature": "fingerprint_similarity",
            "value": fingerprint_similarity,
            "present": fingerprint_similarity is not None,
        },
        {"feature": "version_compatibility", "value": version_compatibility, "present": True},
    )
    return ScoredCandidate(
        recording_id=candidate.recording_id,
        raw_score=raw,
        confidence=raw,
        evidence_tier=tier,
        feature_scores=features,
        hard_conflicts=tuple(sorted(set(conflicts))),
        candidate_origins=tuple(sorted(set(candidate.origins))),
    )


def _version_conflicts(query: set[str], candidate: set[str]) -> list[str]:
    conflicts: list[str] = []
    for marker in ("LIVE", "REMIX", "INSTRUMENTAL", "KARAOKE", "COVER"):
        if (marker in query) != (marker in candidate):
            conflicts.append(f"VERSION_{marker}")
    edit_group = {"EDIT", "RADIO_EDIT"}
    if bool(query & edit_group) != bool(candidate & edit_group):
        conflicts.append("VERSION_EDIT")
    if ("MONO" in query and "STEREO" in candidate) or ("STEREO" in query and "MONO" in candidate):
        conflicts.append("VERSION_CHANNEL_MIX")
    if ("REMASTER" in query) != ("REMASTER" in candidate):
        conflicts.append("VERSION_REMASTER_REVIEW")
    return conflicts


def _normalized_base_title(value: str) -> str:
    base = value
    for _, pattern in _MARKERS:
        base = pattern.sub(" ", base)
    return normalize_text(base)


def _score_order(item: ScoredCandidate) -> tuple[float, str]:
    return (-item.confidence, str(item.recording_id))


def _counterfactual_prediction(result: MatchEvaluation) -> UUID | None:
    if not result.candidates:
        return None
    top = result.candidates[0]
    if top.hard_conflicts or top.confidence < REVIEW_THRESHOLD:
        return None
    if result.margin is not None and result.margin < MARGIN_THRESHOLD:
        return None
    return top.recording_id


def contains_sensitive_field(value: Mapping[str, JsonValue]) -> bool:
    """Return whether untrusted raw fields contain credential-shaped keys."""

    return any(_SENSITIVE_KEY.search(_normalize_key(key)) is not None for key in value)


__all__ = (
    "CANDIDATE_GENERATION_VERSION",
    "FEATURE_EXTRACTOR_VERSIONS",
    "IMPORT_ADAPTER_ID",
    "IMPORT_ADAPTER_VERSION",
    "IMPORT_ENVELOPE_VERSION",
    "MATCHER_VERSION",
    "MAX_IMPORT_BYTES",
    "NORMALIZATION_VERSION",
    "BenchmarkCase",
    "BenchmarkReport",
    "CatalogCandidate",
    "FingerprintEvidence",
    "IdentityTrack",
    "ImportEnvelope",
    "ImportEnvelopeError",
    "ImportFormat",
    "MatchEvaluation",
    "ParsedImport",
    "ParsedImportRow",
    "ScoredCandidate",
    "contains_sensitive_field",
    "evaluate_identity",
    "extract_version_markers",
    "identity_track_from_row",
    "normalize_text",
    "parse_import",
    "run_benchmark",
)

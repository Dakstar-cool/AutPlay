"""Safe canonical identity-evidence documents.

This module owns serialization and privacy validation only. It deliberately does
not score candidates or decide identity matches.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import rfc8785

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

MAX_IDENTITY_DOCUMENT_BYTES: Final = 131_072
MAX_CANDIDATE_AGGREGATE_BYTES: Final = 4 * 1024 * 1024
MAX_CANDIDATE_COUNT: Final = 100
MAX_FEATURE_ITEMS: Final = 256
MAX_ORIGIN_ITEMS: Final = 256
MAX_CONFLICT_ITEMS: Final = 64

_SNAPSHOT_KEYS: Final = frozenset(
    {
        "normalized_title",
        "normalized_artists",
        "normalized_release",
        "duration_ms",
        "version_markers",
        "language_code",
        "market_scope",
        "evidence_ids",
    }
)
_EVIDENCE_KEYS: Final = frozenset(
    {
        "recording_id",
        "raw_score",
        "confidence",
        "evidence_tier",
        "feature_scores",
        "hard_conflicts",
        "candidate_origins",
        "extractor_versions",
    }
)
_GATE_METADATA_KEYS: Final[frozenset[str]] = frozenset()
_SENSITIVE_TOKEN: Final = re.compile(
    r"(?:^|_)(?:access|auth|refresh)?_?token(?:$|_)|"
    r"(?:^|_)(?:api_key|authorization|cookie|password|passwd|secret|credential|"
    r"private_url|raw_path|"
    r"source_uri|provider_payload|raw_payload)(?:$|_)",
    re.IGNORECASE,
)


class IdentityDocumentError(ValueError):
    """Raised when an identity document violates the closed v1 contract."""


@dataclass(frozen=True, slots=True)
class CanonicalDocument:
    """Canonical bytes plus the persistence fields derived from them."""

    value: dict[str, JsonValue]
    canonical_bytes: bytes
    sha256: bytes

    @property
    def byte_size(self) -> int:
        """Return the exact RFC 8785 byte length."""

        return len(self.canonical_bytes)


def canonical_query_snapshot(value: Mapping[str, JsonValue]) -> CanonicalDocument:
    """Validate and canonicalize a sanitized identity query snapshot."""

    document = _copy_object(value, allowed_keys=_SNAPSHOT_KEYS, path="query_snapshot")
    evidence_ids = document.get("evidence_ids")
    if evidence_ids is not None:
        _require_bounded_string_list(evidence_ids, "query_snapshot.evidence_ids", 256)
    normalized_artists = document.get("normalized_artists")
    if normalized_artists is not None:
        _require_bounded_string_list(normalized_artists, "query_snapshot.normalized_artists", 100)
    version_markers = document.get("version_markers")
    if version_markers is not None:
        _require_bounded_string_list(version_markers, "query_snapshot.version_markers", 100)
    for key in (
        "normalized_title",
        "normalized_release",
        "language_code",
        "market_scope",
    ):
        item = document.get(key)
        if item is not None and (not isinstance(item, str) or not item):
            raise IdentityDocumentError(f"query_snapshot.{key} must be a non-empty string")
    duration_ms = document.get("duration_ms")
    if duration_ms is not None and (
        isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0
    ):
        raise IdentityDocumentError("query_snapshot.duration_ms must be a non-negative integer")
    return _canonical_document(document)


def canonical_candidate_evidence(value: Mapping[str, JsonValue]) -> CanonicalDocument:
    """Validate and canonicalize one sealed candidate evidence document."""

    document = _copy_object(value, allowed_keys=_EVIDENCE_KEYS, path="candidate_evidence")
    for key in ("recording_id", "evidence_tier"):
        if key not in document:
            raise IdentityDocumentError(f"candidate_evidence.{key} is required")
    recording_id = document["recording_id"]
    if not isinstance(recording_id, str) or not recording_id:
        raise IdentityDocumentError("candidate_evidence.recording_id must be a non-empty string")
    evidence_tier = document["evidence_tier"]
    if evidence_tier not in {"T0", "T1", "T2", "T3", "T4"}:
        raise IdentityDocumentError("candidate_evidence.evidence_tier is invalid")
    for key in ("raw_score", "confidence"):
        score = document.get(key)
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1
        ):
            raise IdentityDocumentError(f"candidate_evidence.{key} must be null or 0..1")
    _require_bounded_list(
        document.get("feature_scores"),
        "candidate_evidence.feature_scores",
        MAX_FEATURE_ITEMS,
    )
    _require_object(document.get("extractor_versions"), "candidate_evidence.extractor_versions")
    _require_bounded_list(
        document.get("hard_conflicts"),
        "candidate_evidence.hard_conflicts",
        MAX_CONFLICT_ITEMS,
    )
    _require_bounded_list(
        document.get("candidate_origins"),
        "candidate_evidence.candidate_origins",
        MAX_ORIGIN_ITEMS,
    )
    return _canonical_document(document)


def canonical_gate_metadata(value: Mapping[str, JsonValue]) -> CanonicalDocument:
    """Canonicalize the conservative P02 gate-metadata schema.

    The normative v1 contract does not define any portable metadata keys and
    stores benchmark provenance by immutable report hash.  P02 therefore
    accepts only the empty object for schema version 1 instead of inventing a
    privacy-sensitive provider payload contract.  A later non-empty schema
    requires an explicit versioned allowlist.
    """

    document = _copy_object(
        value,
        allowed_keys=_GATE_METADATA_KEYS,
        path="gate_metadata",
    )
    return _canonical_document(document)


def candidate_aggregate_sha256(
    ranked_hashes: Sequence[tuple[int, bytes]],
) -> tuple[bytes, int]:
    """Hash PostgreSQL-compatible ``int4send(rank) || evidence_sha256`` bytes."""

    if len(ranked_hashes) > MAX_CANDIDATE_COUNT:
        raise IdentityDocumentError("candidate set exceeds 100 rows")
    stream = bytearray()
    for expected_rank, (rank, evidence_hash) in enumerate(ranked_hashes, start=1):
        if rank != expected_rank:
            raise IdentityDocumentError("candidate ranks must be contiguous from one")
        if len(evidence_hash) != 32:
            raise IdentityDocumentError("candidate evidence SHA-256 must be 32 bytes")
        stream.extend(struct.pack("!i", rank))
        stream.extend(evidence_hash)
    if len(stream) > MAX_CANDIDATE_AGGREGATE_BYTES:
        raise IdentityDocumentError("candidate aggregate exceeds 4 MiB")
    return hashlib.sha256(stream).digest(), len(stream)


def validate_candidate_evidence_total(document_sizes: Sequence[int]) -> int:
    """Validate and return the exact canonical-byte total for a candidate set.

    The aggregate SHA-256 input contains only rank/hash pairs and is therefore
    much smaller than the stored evidence documents.  This independent gate
    enforces the decision-level 4 MiB contract over the exact RFC 8785 byte
    sizes recorded by every candidate row.
    """

    if len(document_sizes) > MAX_CANDIDATE_COUNT:
        raise IdentityDocumentError("candidate set exceeds 100 rows")
    total = 0
    for size in document_sizes:
        if isinstance(size, bool) or not isinstance(size, int):
            raise IdentityDocumentError("candidate evidence byte size must be an integer")
        if not 2 <= size <= MAX_IDENTITY_DOCUMENT_BYTES:
            raise IdentityDocumentError("candidate evidence byte size is outside 2..128 KiB")
        total += size
    if total > MAX_CANDIDATE_AGGREGATE_BYTES:
        raise IdentityDocumentError("candidate evidence total exceeds 4 MiB")
    return total


def _canonical_document(value: dict[str, JsonValue]) -> CanonicalDocument:
    try:
        encoded = rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise IdentityDocumentError("document is not RFC 8785 canonicalizable") from exc
    if len(encoded) > MAX_IDENTITY_DOCUMENT_BYTES:
        raise IdentityDocumentError("canonical document exceeds 128 KiB")
    return CanonicalDocument(
        value=value, canonical_bytes=encoded, sha256=hashlib.sha256(encoded).digest()
    )


def _copy_object(
    value: Mapping[str, JsonValue], *, allowed_keys: frozenset[str], path: str
) -> dict[str, JsonValue]:
    document: dict[str, JsonValue] = {}
    for key, item in value.items():
        if _is_sensitive_key(key):
            raise IdentityDocumentError(f"{path}.{key} is sensitive")
        if key not in allowed_keys:
            raise IdentityDocumentError(f"{path}.{key} is not allowed by schema v1")
        _reject_nested_sensitive(item, f"{path}.{key}")
        document[key] = item
    return document


def _reject_nested_sensitive(value: JsonValue, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_key(key):
                raise IdentityDocumentError(f"{path}.{key} is sensitive")
            _reject_nested_sensitive(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nested_sensitive(item, f"{path}[{index}]")


def _require_object(value: JsonValue | None, path: str) -> None:
    if not isinstance(value, dict):
        raise IdentityDocumentError(f"{path} must be an object")


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return _SENSITIVE_TOKEN.search(normalized) is not None


def _require_bounded_list(value: JsonValue | None, path: str, maximum: int) -> None:
    if not isinstance(value, list):
        raise IdentityDocumentError(f"{path} must be an array")
    if len(value) > maximum:
        raise IdentityDocumentError(f"{path} exceeds {maximum} entries")


def _require_bounded_string_list(value: JsonValue, path: str, maximum: int) -> None:
    _require_bounded_list(value, path, maximum)
    assert isinstance(value, list)
    if any(not isinstance(item, str) or not item for item in value):
        raise IdentityDocumentError(f"{path} must contain non-empty strings")

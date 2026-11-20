"""Pure SERVING_HASH_LP_V1 codec for private native-v2 serving evidence.

Digests bind bytes only. They do not establish ownership, serving authority,
approval, or a causal relationship to any interaction.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from hashlib import sha256
from typing import Final, Literal
from uuid import UUID

from autplay.domain.canonical_json_lp import (
    MAX_LP_STRING_BYTES,
    CanonicalJsonLpSchema,
    encode_canonical_json_lp,
    parse_and_encode_canonical_json_lp,
)

MAX_SERVING_ITEMS: Final = 5_000
P11_ITEM_DOMAIN: Final = "autplay.recommendation-p11-item.v2"
SERVED_ITEM_DOMAIN: Final = "autplay.recommendation-served-item.v2"
P11_SET_DOMAIN: Final = "autplay.recommendation-p11-set.v2"
SERVED_SET_DOMAIN: Final = "autplay.recommendation-served-set.v2"
_U32_MAX: Final = 2**32 - 1
_I64_MIN: Final = -(2**63)
_I64_MAX: Final = 2**63 - 1
_U32 = struct.Struct(">I")
_I64 = struct.Struct(">q")


class ServingHashError(ValueError):
    """A value cannot be represented by the frozen serving hash contract."""


def encode_serving_lp(domain: str, fields: Sequence[bytes | None]) -> bytes:
    """Encode an ASCII domain (without NUL) and exactly ordered raw fields."""

    if not isinstance(domain, str) or not domain or not domain.isascii() or "\0" in domain:
        raise ServingHashError("serving_hash_domain_invalid")
    if not isinstance(fields, Sequence) or len(fields) > _U32_MAX:
        raise ServingHashError("serving_hash_fields_invalid")
    parts = [domain.encode("ascii"), b"\0", _U32.pack(len(fields))]
    for value in fields:
        if value is None:
            parts.append(b"\0")
        elif type(value) is bytes and len(value) <= _U32_MAX:
            parts.extend((b"\x01", _U32.pack(len(value)), value))
        else:
            raise ServingHashError("serving_hash_field_invalid")
    return b"".join(parts)


def integer_bytes(value: int) -> bytes:
    """Encode a signed int64; booleans and binary floats are not integers."""

    if type(value) is not int or not _I64_MIN <= value <= _I64_MAX:
        raise ServingHashError("serving_hash_integer_invalid")
    return _I64.pack(value)


def serving_digest(domain: str, fields: Sequence[bytes | None]) -> bytes:
    """Hash exact LP bytes, without textual hex conversion of any field."""

    return sha256(encode_serving_lp(domain, fields)).digest()


def baseline_item_digest(
    request_id: UUID,
    rank: int,
    recording_id: UUID,
    score_units_e8: int,
    candidate_sources_hash: bytes,
    explanation_code: str,
    availability_snapshot_hash: bytes,
    contributions_hash: bytes,
    reason_codes_hash: bytes,
    item_provenance_hash: bytes,
) -> bytes:
    """Bind the plan's exact ten-field baseline P11 item representation."""

    return serving_digest(
        P11_ITEM_DOMAIN,
        (
            _uuid_bytes(request_id),
            _rank_bytes(rank),
            _uuid_bytes(recording_id),
            integer_bytes(score_units_e8),
            _hash_bytes(candidate_sources_hash),
            _text_bytes(explanation_code),
            _hash_bytes(availability_snapshot_hash),
            _hash_bytes(contributions_hash),
            _hash_bytes(reason_codes_hash),
            _hash_bytes(item_provenance_hash),
        ),
    )


def served_item_digest(
    request_id: UUID,
    source_rank: int,
    recording_id: UUID,
    score_units_e8: int,
    score_kind: str,
    attribution_source: str,
    reasons_hash: bytes,
    contributions_hash: bytes,
    original_p11_rank: int | None,
    origin: str,
) -> bytes:
    """Bind the exact served item, preserving a null original P11 rank."""

    return serving_digest(
        SERVED_ITEM_DOMAIN,
        (
            _uuid_bytes(request_id),
            _rank_bytes(source_rank),
            _uuid_bytes(recording_id),
            integer_bytes(score_units_e8),
            _text_bytes(score_kind),
            _text_bytes(attribution_source),
            _hash_bytes(reasons_hash),
            _hash_bytes(contributions_hash),
            None if original_p11_rank is None else _rank_bytes(original_p11_rank),
            _text_bytes(origin),
        ),
    )


def ordered_set_digest(kind: Literal["p11", "served"], items: Sequence[tuple[int, bytes]]) -> bytes:
    """Hash the complete ordered ranks 1..N; do not sort or repair caller data."""

    if kind not in ("p11", "served"):
        raise ServingHashError("serving_hash_set_kind_invalid")
    if not isinstance(items, Sequence) or len(items) > MAX_SERVING_ITEMS:
        raise ServingHashError("serving_hash_set_bound_exceeded")
    fields: list[bytes | None] = [integer_bytes(len(items))]
    for expected_rank, item in enumerate(items, start=1):
        if not isinstance(item, tuple) or len(item) != 2:
            raise ServingHashError("serving_hash_set_item_invalid")
        rank, item_hash = item
        if type(rank) is not int or rank != expected_rank:
            raise ServingHashError("serving_hash_set_ranks_invalid")
        fields.extend((integer_bytes(rank), _hash_bytes(item_hash)))
    domain = P11_SET_DOMAIN if kind == "p11" else SERVED_SET_DOMAIN
    return serving_digest(domain, fields)


def nested_document_digest(value: object, schema: CanonicalJsonLpSchema) -> bytes:
    """Hash bounded typed nested evidence under a caller-declared schema."""

    _require_schema(schema)
    return sha256(encode_canonical_json_lp(value, schema)).digest()


def parse_nested_document_digest(raw: bytes, schema: CanonicalJsonLpSchema) -> bytes:
    """Reject duplicate input keys before hashing bounded canonical evidence."""

    _require_schema(schema)
    return sha256(parse_and_encode_canonical_json_lp(raw, schema)).digest()


def _require_schema(schema: CanonicalJsonLpSchema) -> None:
    if not isinstance(schema, CanonicalJsonLpSchema):
        raise ServingHashError("serving_hash_nested_schema_required")


def _uuid_bytes(value: UUID) -> bytes:
    if type(value) is not UUID:
        raise ServingHashError("serving_hash_uuid_invalid")
    return value.bytes


def _hash_bytes(value: bytes) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise ServingHashError("serving_hash_digest_invalid")
    return value


def _rank_bytes(value: int) -> bytes:
    if type(value) is not int or not 1 <= value <= MAX_SERVING_ITEMS:
        raise ServingHashError("serving_hash_rank_invalid")
    return integer_bytes(value)


def _text_bytes(value: str) -> bytes:
    if not isinstance(value, str):
        raise ServingHashError("serving_hash_text_invalid")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ServingHashError("serving_hash_text_invalid") from error
    if len(encoded) > MAX_LP_STRING_BYTES:
        raise ServingHashError("serving_hash_text_bound_exceeded")
    return encoded

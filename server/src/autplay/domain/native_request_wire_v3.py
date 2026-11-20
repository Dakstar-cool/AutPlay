"""NATIVE_P11_BODY_JSON_I64_V3, separate from every earlier JSON/LP codec.

The raw proof scanner admits closed container paths before JSON hydration. The
encoder is also useful for bounded scalar vectors; proof role semantics belong
to the V3 proof verifier rather than this wire representation.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Final

from autplay.domain.native_request_budget_v3 import (
    MAX_ITEMS,
    MAX_RECORD_BYTES,
    MAX_TRACKS,
    NativeRequestV3Budget,
    NativeRequestV3Error,
    NativeRequestV3Meter,
)
from autplay.domain.native_request_meter_v3 import NativeRequestV3MeterProtocol

MAX_DEPTH: Final = 12
MAX_MEMBERS: Final = 4_096
MAX_STRING_BYTES: Final = 4_096
_I64_MIN: Final = -(2**63)
_I64_MAX: Final = 2**63 - 1


def _keys(names: str) -> frozenset[str]:
    return frozenset(names.split())


_OBJECTS: Final[dict[str, tuple[frozenset[str], ...]]] = {
    "$": (
        _keys("kind proof_version hash_format_version body root_header root_sha256"),
        _keys("kind proof_version hash_format_version body root_header root_sha256 snapshot_ref"),
    ),
    "$.snapshot_ref": (_keys("owner_user_id snapshot_id sha256 track_count"),),
    "$.body": (
        _keys(
            "request_id identity metadata request_document request_features snapshot_header "
            "snapshot_tracks p11_items origin"
        ),
    ),
    "$.root_header": (
        _keys(
            "kind hash_format_version request_id identity_sha256 metadata_sha256 "
            "query_document_sha256 request_features_sha256 snapshot_header_sha256 "
            "snapshot_track_count snapshot_tracks_sha256 policy_tracks_sha256 "
            "availability_tracks_sha256 candidate_universe_count candidate_universe_sha256 "
            "p11_item_count p11_items_sha256 served_item_count served_items_sha256 "
            "original_capture_binding_sha256"
        ),
    ),
    "$.body.identity": (
        _keys("owner_user_id device_id journal_epoch server_instance_id identity_epoch"),
    ),
    "$.body.metadata": (
        _keys(
            "surface context seed effective_limit endpoint_max cutoff_at_ms pipeline_key "
            "pipeline_version pipeline_manifest_sha256 model_bundle_version "
            "candidate_policy_version "
            "filter_policy_version reranker_version interaction_watermark catalog_snapshot "
            "availability_snapshot_ref"
        ),
    ),
    "$.body.request_document": (
        _keys(
            "schema_version canonicalization_version user_id surface context seed shadow limit "
            "exploration_units_e8"
        ),
    ),
    "$.body.request_features": (_keys("limit exploration_units_e8"),),
    "$.body.snapshot_header": (
        _keys("schema_version user_id interaction_watermark selection_policy max_tracks"),
    ),
    "$.body.snapshot_tracks[]": (
        _keys(
            "recording_id user_track_ref_id artist_key release_key metadata_tokens availability "
            "authorized identity_status preference excluded play_count organic_play_count "
            "recommended_play_count last_played_at_ms added_at_ms release_date_ordinal"
        ),
    ),
    "$.body.p11_items[]": (
        _keys(
            "rank recording_id score_units_e8 candidate_sources explanation_code "
            "availability_snapshot contributions reason_codes item_provenance"
        ),
    ),
    "$.body.p11_items[].availability_snapshot": (_keys("snapshot_sha256"),),
    "$.body.p11_items[].item_provenance": (_keys("artist_key release_key section"),),
    "$.body.p11_items[].contributions[]": (
        _keys("source_key source_version source_rank raw_score_units_e8 provenance"),
    ),
    "$.body.p11_items[].contributions[].provenance": (
        _keys("preference"),
        _keys("organic_play_count recommended_play_count_ignored_for_affinity forgotten"),
        _keys("artist_affinity metadata_token_overlap release_key"),
        _keys("release_date_ordinal artist_affinity cold_start_fallback"),
        _keys("seeded previously_unplayed"),
    ),
    "$.body.origin": (
        _keys("kind"),
        _keys("kind original_request_id capture_sha256 consent_generation consent_receipt_sha256"),
    ),
}
_ARRAYS: Final = {
    "$.body.snapshot_tracks": MAX_TRACKS,
    "$.body.p11_items": MAX_ITEMS,
    "$.body.snapshot_tracks[].metadata_tokens": MAX_MEMBERS,
    "$.body.p11_items[].candidate_sources": MAX_MEMBERS,
    "$.body.p11_items[].contributions": MAX_MEMBERS,
    "$.body.p11_items[].reason_codes": MAX_MEMBERS,
}


def _string_lengths(value: str) -> tuple[int, int]:
    if len(value) > MAX_STRING_BYTES:
        raise NativeRequestV3Error("V3_STRING_BYTES")
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise NativeRequestV3Error("V3_STRING_NFC")
    utf8_size = 0
    wire_size = 2
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise NativeRequestV3Error("V3_STRING_UTF8")
        size = 1 if codepoint < 128 else 2 if codepoint < 2_048 else 3 if codepoint < 65_536 else 4
        utf8_size += size
        if codepoint in (8, 9, 10, 12, 13, 34, 92):
            wire_size += 2
        elif codepoint < 32:
            wire_size += 6
        else:
            wire_size += size
        if utf8_size > MAX_STRING_BYTES:
            raise NativeRequestV3Error("V3_STRING_BYTES")
    return utf8_size, wire_size


def _string(value: str, meter: NativeRequestV3MeterProtocol | None) -> bytes:
    utf8_size, _ = _string_lengths(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if meter is not None:
        meter.charge_bytes(utf8_size + len(encoded))
    return encoded


class _Encoder:
    def __init__(
        self, meter: NativeRequestV3MeterProtocol | None, max_bytes: int, *, retain: bool
    ) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECORD_BYTES:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        self.meter = meter
        self.limit = max_bytes
        self.retain = retain
        self.output = bytearray()
        self.size = 0
        self.nodes = 0

    def _write(self, chunk: bytes) -> None:
        self._write_size(len(chunk))
        if self.retain:
            self.output.extend(chunk)

    def _write_size(self, size: int) -> None:
        if self.size + size > self.limit:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        if self.meter is not None:
            self.meter.charge_bytes(size)
        self.size += size

    def _write_string(self, value: str) -> None:
        if self.retain:
            self._write(_string(value, self.meter))
        else:
            _, size = _string_lengths(value)
            self._write_size(size)

    def _node(self, n: int = 1) -> None:
        self.nodes += n
        if self.meter is not None:
            self.meter.charge_nodes(n)

    def encode(self, value: object, path: str = "$", depth: int = 0) -> None:
        self._node()
        if depth > MAX_DEPTH:
            raise NativeRequestV3Error("V3_DEPTH")
        if value is None:
            self._write(b"null")
        elif type(value) is bool:
            self._write(b"true" if value else b"false")
        elif type(value) is int:
            if not _I64_MIN <= value <= _I64_MAX:
                raise NativeRequestV3Error("V3_INTEGER_I64")
            self._write(str(value).encode("ascii"))
        elif type(value) is str:
            self._write_string(value)
        elif type(value) is list:
            limit = _ARRAYS.get(path, MAX_MEMBERS)
            if len(value) > limit:
                raise NativeRequestV3Error("V3_ARRAY_COUNT")
            self._write(b"[")
            for index, child in enumerate(value):
                self._node()
                if index:
                    self._write(b",")
                self.encode(child, path + "[]", depth + 1)
            self._write(b"]")
        elif type(value) is dict:
            if len(value) > MAX_MEMBERS:
                raise NativeRequestV3Error("V3_OBJECT_COUNT")
            if any(
                type(key) is not str or len(key) > MAX_STRING_BYTES or not key.isascii()
                for key in value
            ):
                raise NativeRequestV3Error("V3_KEY_ASCII")
            self._write(b"{")
            for index, key in enumerate(sorted(value)):
                self._node(2)
                if index:
                    self._write(b",")
                self._write_string(key)
                self._write(b":")
                self.encode(value[key], path + "." + key, depth + 1)
            self._write(b"}")
        else:
            raise NativeRequestV3Error("V3_VALUE_TYPE")


def canonical_native_json_v3(
    value: object, *, meter: NativeRequestV3MeterProtocol, max_bytes: int = MAX_RECORD_BYTES
) -> bytes:
    """Emit incrementally bounded canonical bytes, preserving signed int64."""
    encoder = _Encoder(meter, max_bytes, retain=True)
    encoder.encode(value)
    # The final immutable copy is another encoded write into the returned buffer.
    meter.charge_bytes(encoder.size)
    return bytes(encoder.output)


def measure_native_json_v3(
    value: object,
    *,
    meter: NativeRequestV3MeterProtocol | None = None,
    max_bytes: int = MAX_RECORD_BYTES,
) -> tuple[int, int]:
    """Validate an already-owned value and return (wire bytes, node visits).

    No complete output is retained. Supplying an admitted meter charges writes
    and visits as they occur, including work before a refusal. Unmetered callers
    must charge the returned visits/bytes before other work; a proof builder
    should use the admitted meter directly. Untrusted wire input must use
    preflight_native_json_v3 instead.
    """
    encoder = _Encoder(meter, max_bytes, retain=False)
    encoder.encode(value)
    return encoder.size, encoder.nodes


class _Scanner:
    def __init__(self, raw: bytes, meter: NativeRequestV3MeterProtocol) -> None:
        self.raw = raw
        self.meter = meter
        self.position = 0
        self.nodes = 0
        self.tracks = 0
        self.items = 0

    def _node(self, n: int = 1) -> None:
        self.nodes += n
        self.meter.charge_nodes(n)

    def _take(self, expected: int) -> None:
        if self.position >= len(self.raw) or self.raw[self.position] != expected:
            raise NativeRequestV3Error("V3_JSON_SYNTAX")
        self.position += 1

    def _peek(self) -> int:
        if self.position >= len(self.raw):
            raise NativeRequestV3Error("V3_JSON_SYNTAX")
        return self.raw[self.position]

    def _text(self) -> str:
        start = self.position
        self._take(34)
        while self.position < len(self.raw):
            byte = self.raw[self.position]
            self.position += 1
            if byte == 34:
                break
            if byte < 32:
                raise NativeRequestV3Error("V3_STRING_ESCAPE")
            if byte == 92:
                if self.position >= len(self.raw):
                    raise NativeRequestV3Error("V3_JSON_SYNTAX")
                self.position += 1
            if self.position - start > 6 * MAX_STRING_BYTES + 2:
                raise NativeRequestV3Error("V3_STRING_BYTES")
        else:
            raise NativeRequestV3Error("V3_JSON_SYNTAX")
        token = self.raw[start : self.position]
        try:
            text = json.loads(token)
        except ValueError, UnicodeError:
            raise NativeRequestV3Error("V3_STRING_UTF8") from None
        if type(text) is not str:
            raise NativeRequestV3Error("V3_VALUE_TYPE")
        if _string(text, self.meter) != token:
            raise NativeRequestV3Error("V3_STRING_CANONICAL")
        return text

    def value(self, path: str = "$", depth: int = 0) -> None:
        self._node()
        if depth > MAX_DEPTH:
            raise NativeRequestV3Error("V3_DEPTH")
        byte = self._peek()
        if path in _OBJECTS:
            if byte != 123:
                raise NativeRequestV3Error("V3_CONTAINER_TYPE")
            self._object(path, depth)
        elif path in _ARRAYS:
            if byte != 91:
                raise NativeRequestV3Error("V3_CONTAINER_TYPE")
            self._array(path, depth)
        elif byte in (123, 91):
            raise NativeRequestV3Error("V3_CONTAINER_PATH")
        elif byte == 34:
            self._text()
        elif byte in (45, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57):
            start = self.position
            self._integer()
            if path == "$.snapshot_ref.track_count":
                count = int(self.raw[start:self.position])
                if self.tracks or not 0 <= count <= 5000:
                    raise NativeRequestV3Error("V4_SNAPSHOT_REFERENCE")
                self.tracks = count
        else:
            for literal in (b"null", b"true", b"false"):
                if self.raw.startswith(literal, self.position):
                    self.position += len(literal)
                    return
            raise NativeRequestV3Error("V3_JSON_SYNTAX")

    def _integer(self) -> None:
        start = self.position
        if self.raw[self.position] == 45:
            self.position += 1
        if self._peek() == 48:
            self.position += 1
            if self.position - start != 1:
                raise NativeRequestV3Error("V3_INTEGER_CANONICAL")
        else:
            if not 49 <= self._peek() <= 57:
                raise NativeRequestV3Error("V3_INTEGER_CANONICAL")
            while self.position < len(self.raw) and 48 <= self.raw[self.position] <= 57:
                self.position += 1
                if self.position - start > 20:
                    raise NativeRequestV3Error("V3_INTEGER_I64")
        token = self.raw[start : self.position]
        if not _I64_MIN <= int(token) <= _I64_MAX:
            raise NativeRequestV3Error("V3_INTEGER_I64")

    def _object(self, path: str, depth: int) -> None:
        self._take(123)
        variants = _OBJECTS[path]
        allowed = frozenset().union(*variants)
        found: set[str] = set()
        previous: str | None = None
        if self._peek() != 125:
            while True:
                self._node(2)
                key = self._text()
                if not key.isascii():
                    raise NativeRequestV3Error("V3_KEY_ASCII")
                if previous is not None and key <= previous:
                    raise NativeRequestV3Error("V3_KEY_ORDER")
                if key not in allowed:
                    raise NativeRequestV3Error("V3_OBJECT_KEYS")
                found.add(key)
                previous = key
                self._take(58)
                self.value(path + "." + key, depth + 1)
                if self._peek() != 44:
                    break
                self.position += 1
        self._take(125)
        if frozenset(found) not in variants:
            raise NativeRequestV3Error("V3_OBJECT_KEYS")

    def _array(self, path: str, depth: int) -> None:
        self._take(91)
        count = 0
        if self._peek() != 93:
            while True:
                count += 1
                self._node()
                if count > _ARRAYS[path]:
                    raise NativeRequestV3Error("V3_ARRAY_COUNT")
                self.value(path + "[]", depth + 1)
                if self._peek() != 44:
                    break
                self.position += 1
        self._take(93)
        if path == "$.body.snapshot_tracks":
            self.tracks = count
        elif path == "$.body.p11_items":
            self.items = count


def _hydrate(raw: bytes) -> object:
    """Only called after structural admission, canonical tokens and reservation."""
    return json.loads(raw)


def preflight_admitted_native_json_v3(raw: bytes, *, meter: NativeRequestV3MeterProtocol) -> object:
    """Scan and hydrate one already admitted root without opening/closing a record."""
    if type(raw) is not bytes:
        raise NativeRequestV3Error("V3_WIRE_TYPE")
    if not 1 <= len(raw) <= MAX_RECORD_BYTES:
        raise NativeRequestV3Error("V3_RECORD_BYTES")
    try:
        meter.charge_bytes(len(raw))
        scanner = _Scanner(raw, meter)
        scanner.value()
        if scanner.position != len(raw):
            raise NativeRequestV3Error("V3_JSON_CANONICAL")
        meter.set_counts(scanner.tracks, scanner.items)
        meter.charge_bytes(len(raw))
        meter.charge_nodes(scanner.nodes)
        return _hydrate(raw)
    except NativeRequestV3Error:
        raise
    except ValueError, UnicodeError, RecursionError:
        raise NativeRequestV3Error("V3_JSON_SYNTAX") from None


def preflight_native_json_v3(
    raw: bytes, *, budget: NativeRequestV3Budget
) -> tuple[object, NativeRequestV3Meter]:
    """Admit bytes, scan closed raw paths, then hydrate the bounded proof tree."""
    if type(raw) is not bytes:
        raise NativeRequestV3Error("V3_WIRE_TYPE")
    meter = budget.begin_record(len(raw))
    try:
        value = preflight_admitted_native_json_v3(raw, meter=meter)
    except NativeRequestV3Error:
        meter.finish_record()
        raise
    return value, meter

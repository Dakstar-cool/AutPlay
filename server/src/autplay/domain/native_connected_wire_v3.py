"""Closed framed native connected proof V3; large fragments stay opaque.

This codec verifies framing, canonical scalar headers and byte identities only.
The owning reader must preflight/verify each source/root fragment against its
independent locked SQL context before treating the result as connected input.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from hashlib import sha256
from io import BytesIO
from typing import Final, Protocol
from uuid import UUID
from weakref import ReferenceType, WeakKeyDictionary, WeakSet, ref

from autplay.domain.native_connected_request_v3 import (
    MAX_CONNECTED_PROOF_BYTES,
    MAX_CONNECTED_RECORD_BYTES,
    MAX_SAFE_INTEGER,
    OUTCOME_WINDOW_US,
    TRUTH_HORIZON_US,
    NativeConnectedCountsV3,
    NativeConnectedRangeV3,
    NativeConnectedRequestProofV3,
    NativeConnectedSelectionV3,
    NativeConnectedV3Error,
    require_digest_v3,
    require_hex_v3,
    require_int_v3,
    require_uuid_v3,
)

_MAGIC: Final = b"APNC3\x00"
_PREFIX_BYTES: Final = 14
_MAX_HEADER_BYTES: Final = 8192
_MEMBERSHIP_DOMAIN: Final = b"autplay.native-connected-membership.v3"
_RECORD_SET_DOMAIN: Final = b"autplay.native-connected-record-set.v3"
type HeaderScalarV3 = str | int


class NativeConnectedFrameMeterV3(Protocol):
    """Work and live capacity from an already admitted record or wire lease."""

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None: ...
    def retain_digests(self, amount: int) -> None: ...
    def release_digests(self, amount: int) -> None: ...
    def retain_scratch(self, amount: int) -> None: ...
    def release_scratch(self, amount: int) -> None: ...


class NativeConnectedWireMeterV3(NativeConnectedFrameMeterV3, Protocol):
    """A caller-owned child proof meter attached to its active parent lease."""

    def admit_wire(self, total_bytes: int, record_count: int) -> None: ...
    def narrow_wire(self, total_bytes: int, record_count: int) -> None: ...


@dataclass(frozen=True, slots=True)
class NativeConnectedRecordHeaderV3:
    kind: str
    record_version: int
    request_hash_format_version: int
    original_capture_version: int
    serving_contract_version: int
    owner_user_id: UUID
    served_request_id: UUID
    original_request_id: UUID
    device_id: UUID
    journal_epoch: UUID
    server_instance_id: UUID
    identity_epoch: int
    created_at_us: int
    expires_at_us: int
    window_closed_at_us: int
    retained_until_us: int
    cutoff_at_ms: int
    interaction_watermark: int
    native_interaction_watermark: int
    source_snapshot_track_count: int
    candidate_universe_count: int
    p11_item_count: int
    served_item_count: int
    source_event_count: int
    source_evidence_count: int
    profile_dimension_count: int
    outcome_count: int
    outside_window_count: int
    root_proof_sha256: bytes
    request_root_sha256: bytes
    decision_sha256: bytes
    connection_seal_sha256: bytes
    capture_sha256: bytes
    original_request_sha256: bytes
    original_ranking_sha256: bytes
    baseline_snapshot_id: UUID
    baseline_snapshot_sha256: bytes
    temporal_snapshot_id: UUID
    temporal_snapshot_sha256: bytes
    candidate_membership_sha256: bytes
    eligibility_policy_sha256: bytes
    consent_generation: int
    consent_receipt_sha256: bytes

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind != "PRIVATE_CONNECTED_NATIVE_RECORD_V3":
            raise NativeConnectedV3Error("CONNECTED_V3_RECORD_KIND")
        for name, expected in (
            ("record_version", 3),
            ("request_hash_format_version", 3),
            ("original_capture_version", 3),
            ("serving_contract_version", 2),
        ):
            require_int_v3(getattr(self, name), expected, expected)
        for field in fields(self):
            value: object = getattr(self, field.name)
            if field.name.endswith("_id") or field.name == "journal_epoch":
                require_uuid_v3(value)
            elif field.name.endswith("_sha256"):
                require_digest_v3(value)
        if self.original_request_id == self.served_request_id:
            raise NativeConnectedV3Error("CONNECTED_V3_RECORD_IDENTITY")
        require_int_v3(self.identity_epoch, 1, 2**63 - 1)
        created = require_int_v3(self.created_at_us, 0, 2**63 - 1 - TRUTH_HORIZON_US)
        require_int_v3(self.expires_at_us, created + TRUTH_HORIZON_US, created + TRUTH_HORIZON_US)
        require_int_v3(
            self.window_closed_at_us, created + OUTCOME_WINDOW_US, created + OUTCOME_WINDOW_US
        )
        require_int_v3(self.retained_until_us, created + 1, self.expires_at_us)
        require_int_v3(self.cutoff_at_ms, 0, MAX_SAFE_INTEGER)
        require_int_v3(self.cutoff_at_ms, created // 1000, created // 1000)
        require_int_v3(self.interaction_watermark, 0, MAX_SAFE_INTEGER)
        require_int_v3(self.native_interaction_watermark, 0, MAX_SAFE_INTEGER)
        require_int_v3(self.source_snapshot_track_count, 0, 5000)
        require_int_v3(self.candidate_universe_count, 0, self.source_snapshot_track_count)
        require_int_v3(self.p11_item_count, 0, 100)
        require_int_v3(self.served_item_count, self.p11_item_count, self.p11_item_count)
        require_int_v3(self.source_event_count, 0, 10_000)
        require_int_v3(self.source_evidence_count, 0, 2 * self.source_event_count)
        require_int_v3(self.profile_dimension_count, 0, 512)
        require_int_v3(self.outcome_count, 0, 5000)
        require_int_v3(self.outside_window_count, 0, 5000 - self.outcome_count)
        require_int_v3(self.consent_generation, 1, MAX_SAFE_INTEGER)

    @property
    def counts(self) -> NativeConnectedCountsV3:
        return NativeConnectedCountsV3(
            self.source_snapshot_track_count,
            self.p11_item_count,
            self.source_event_count,
            self.source_evidence_count,
            self.outcome_count + self.outside_window_count,
            self.profile_dimension_count,
        )

    def _wire_values(self) -> dict[str, HeaderScalarV3]:
        result: dict[str, HeaderScalarV3] = {}
        for field in fields(self):
            value: object = getattr(self, field.name)
            if type(value) is UUID:
                result[field.name] = str(value)
            elif type(value) is bytes:
                result[field.name] = value.hex()
            elif type(value) is str or type(value) is int:
                result[field.name] = value
            else:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_SCALAR")
        return result


_RECORD_FIELDS: Final = frozenset(field.name for field in fields(NativeConnectedRecordHeaderV3))
_RECORD_DIGEST_BYTES: Final = 32 * sum(name.endswith("_sha256") for name in _RECORD_FIELDS)
_PROOF_FIELDS: Final = frozenset(
    (
        "kind",
        "proof_version",
        "request_hash_format_version",
        "original_capture_version",
        "serving_contract_version",
        "record_count",
        "membership_sha256",
        "record_set_sha256",
    )
)


class _HeaderScanner:
    """Flat ASCII scalar grammar; no general JSON hydration is performed."""

    def __init__(self, raw: memoryview, meter: NativeConnectedWireMeterV3) -> None:
        if not 1 <= len(raw) <= _MAX_HEADER_BYTES:
            raise NativeConnectedV3Error("CONNECTED_V3_HEADER_BYTES")
        self.raw, self.meter, self.position = raw, meter, 0
        meter.charge(bytes_read=len(raw))

    def _take(self, expected: int) -> None:
        self.meter.charge(node_visits=1)
        if self.position >= len(self.raw) or self.raw[self.position] != expected:
            raise NativeConnectedV3Error("CONNECTED_V3_HEADER_SYNTAX")
        self.position += 1

    def _text(self) -> str:
        self._take(34)
        start = self.position
        while self.position < len(self.raw) and self.raw[self.position] != 34:
            self.meter.charge(node_visits=1)
            value = self.raw[self.position]
            # Every allowed string is an ASCII kind, UUID or digest. Escapes
            # cannot represent an accepted canonical value in this grammar.
            if not 32 <= value <= 126 or value == 92 or self.position - start >= 128:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_STRING")
            self.position += 1
        stop = self.position
        self._take(34)
        self.meter.charge(bytes_written=stop - start)
        return self.raw[start:stop].tobytes().decode("ascii")

    def _integer(self) -> int:
        start = self.position
        if self.position < len(self.raw) and self.raw[self.position] == 45:
            self.position += 1
        digit_start = self.position
        while self.position < len(self.raw) and 48 <= self.raw[self.position] <= 57:
            self.meter.charge(node_visits=1)
            self.position += 1
            if self.position - start > 20:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_INTEGER")
        token = self.raw[start : self.position].tobytes()
        digits = self.raw[digit_start : self.position]
        if not digits or (len(digits) > 1 and digits[0] == 48) or token == b"-0":
            raise NativeConnectedV3Error("CONNECTED_V3_HEADER_INTEGER")
        self.meter.charge(bytes_written=len(token))
        value = int(token)
        require_int_v3(value, -(2**63), 2**63 - 1)
        return value

    def scan(self, expected: frozenset[str]) -> dict[str, HeaderScalarV3]:
        self._take(123)
        values: dict[str, HeaderScalarV3] = {}
        previous = ""
        for index in range(len(expected)):
            if index:
                self._take(44)
            name = self._text()
            if name not in expected or name <= previous:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_KEYS")
            previous = name
            self._take(58)
            if self.position < len(self.raw) and self.raw[self.position] == 34:
                value: HeaderScalarV3 = self._text()
            else:
                value = self._integer()
            values[name] = value
        self._take(125)
        if self.position != len(self.raw) or values.keys() != expected:
            raise NativeConnectedV3Error("CONNECTED_V3_HEADER_KEYS")
        return values


def _record_header(
    raw: memoryview, meter: NativeConnectedWireMeterV3
) -> NativeConnectedRecordHeaderV3:
    values = _HeaderScanner(raw, meter).scan(_RECORD_FIELDS)
    instance = object.__new__(NativeConnectedRecordHeaderV3)
    for field in fields(NativeConnectedRecordHeaderV3):
        value: object = values[field.name]
        if field.name.endswith("_id") or field.name == "journal_epoch":
            if type(value) is not str:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_UUID")
            try:
                decoded = UUID(value)
            except ValueError:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_UUID") from None
            if str(decoded) != value:
                raise NativeConnectedV3Error("CONNECTED_V3_HEADER_UUID")
            value = decoded
        elif field.name.endswith("_sha256"):
            value = bytes.fromhex(require_hex_v3(value))
        object.__setattr__(instance, field.name, value)
    instance.__post_init__()
    return instance


def _encode_header(values: dict[str, HeaderScalarV3], meter: NativeConnectedFrameMeterV3) -> bytes:
    # Values are already closed typed owned scalars. Bound before the single
    # small allocation; large fragments never enter this serializer.
    upper = 2 + sum(
        len(name) + (len(value) if type(value) is str else 20) + 6 for name, value in values.items()
    )
    if upper > _MAX_HEADER_BYTES:
        raise NativeConnectedV3Error("CONNECTED_V3_HEADER_BYTES")
    meter.charge(bytes_written=upper, node_visits=2 * len(values) + upper)
    encoded = json.dumps(
        values, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return encoded


@dataclass(frozen=True, slots=True)
class NativeConnectedWireRecordPartsV3:
    header: NativeConnectedRecordHeaderV3
    root_envelope: bytes
    capture_document: bytes
    original_request_document: bytes
    outcomes_document: bytes

    def __post_init__(self) -> None:
        if type(self.header) is not NativeConnectedRecordHeaderV3:
            raise NativeConnectedV3Error("CONNECTED_V3_RECORD_TYPE")
        for raw in self.fragments:
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CONNECTED_RECORD_BYTES:
                raise NativeConnectedV3Error("CONNECTED_V3_FRAGMENT_BYTES")

    @property
    def fragments(self) -> tuple[bytes, bytes, bytes, bytes]:
        return (
            self.root_envelope,
            self.capture_document,
            self.original_request_document,
            self.outcomes_document,
        )


@dataclass(frozen=True, slots=True)
class _FrameState:
    pieces: tuple[bytes, bytes, bytes, bytes, bytes, bytes]
    ranges: tuple[NativeConnectedRangeV3, ...]
    header: NativeConnectedRecordHeaderV3
    counts: NativeConnectedCountsV3
    meter: NativeConnectedFrameMeterV3
    scratch: int


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class NativeConnectedFrameSegmentsV3:
    """One scoped immutable frame projection; its fragments are borrowed bytes."""

    _token: object

    def _state(self) -> _FrameState:
        if type(self) is not NativeConnectedFrameSegmentsV3 or self not in _FRAMES:
            raise NativeConnectedV3Error("CONNECTED_V3_FRAME_STATE")
        state = _FRAMES[self]
        state.meter.charge(node_visits=0)
        return state

    @property
    def pieces(self) -> tuple[bytes, bytes, bytes, bytes, bytes, bytes]:
        return self._state().pieces

    @property
    def ranges(self) -> tuple[NativeConnectedRangeV3, ...]:
        return self._state().ranges

    @property
    def record_length(self) -> int:
        return sum(map(len, self._state().pieces))

    @property
    def header(self) -> NativeConnectedRecordHeaderV3:
        return self._state().header

    @property
    def counts(self) -> NativeConnectedCountsV3:
        return self._state().counts

    def __enter__(self) -> NativeConnectedFrameSegmentsV3:
        self._state()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if type(self) is NativeConnectedFrameSegmentsV3 and self in _CLOSED_FRAMES:
            return
        state = self._state()
        del _FRAMES[self]
        del _ACTIVE_FRAMES[id(state.meter)]
        _CLOSED_FRAMES.add(self)
        state.meter.release_digests(2 * _RECORD_DIGEST_BYTES)
        state.meter.release_scratch(state.scratch)


_FRAMES: WeakKeyDictionary[NativeConnectedFrameSegmentsV3, _FrameState] = WeakKeyDictionary()
_ACTIVE_FRAMES: dict[int, ReferenceType[NativeConnectedFrameSegmentsV3]] = {}
_CLOSED_FRAMES: WeakSet[NativeConnectedFrameSegmentsV3] = WeakSet()


def revoke_native_connected_frame_v3(meter: NativeConnectedFrameMeterV3) -> None:
    """Drop an abandoned projection while its owning lease is still current."""
    reference = _ACTIVE_FRAMES.get(id(meter))
    frame = reference() if reference is not None else None
    if frame is not None:
        frame.close()


def prepare_native_connected_frame_v3(
    parts: NativeConnectedWireRecordPartsV3, *, meter: NativeConnectedFrameMeterV3
) -> NativeConnectedFrameSegmentsV3:
    """Frame without joining the body; close before finishing its admission."""
    if type(parts) is not NativeConnectedWireRecordPartsV3:
        raise NativeConnectedV3Error("CONNECTED_V3_RECORD_TYPE")
    meter.charge(node_visits=0)
    meter_id = id(meter)
    active = _ACTIVE_FRAMES.get(meter_id)
    if active is not None and active() is not None:
        raise NativeConnectedV3Error("CONNECTED_V3_FRAME_ACTIVE")
    parts.__post_init__()
    scratch = 2 * _MAX_HEADER_BYTES + 24 + 4 * 16 + 2 * 6 * 8
    meter.retain_scratch(scratch)
    retained = False
    try:
        # Header hex strings and encoded hex bytes coexist during encoding.
        meter.retain_digests(5 * _RECORD_DIGEST_BYTES)
        retained = True
        encoded = _encode_header(parts.header._wire_values(), meter)
        lengths = tuple(map(len, parts.fragments))
        size = 24 + len(encoded) + sum(lengths)
        if size > MAX_CONNECTED_RECORD_BYTES:
            raise NativeConnectedV3Error("CONNECTED_V3_RECORD_LENGTH")
        views = tuple(memoryview(raw) for raw in parts.fragments)
        _check_fragment_hashes(parts.header, (views[0], views[1], views[2], views[3]), meter)
        meter.charge(bytes_written=24 + 4 * 16 + 2 * 6 * 8, node_visits=16)
        prefix = struct.pack(">6I", size, len(encoded), *lengths)
        ranges: list[NativeConnectedRangeV3] = []
        start = 24 + len(encoded)
        for length in lengths:
            ranges.append(NativeConnectedRangeV3(start, start + length))
            start += length
        segments = NativeConnectedFrameSegmentsV3(object())
        _FRAMES[segments] = _FrameState(
            (prefix, encoded, *parts.fragments),
            tuple(ranges),
            parts.header,
            parts.header.counts,
            meter,
            scratch,
        )

        def dropped(reference: ReferenceType[NativeConnectedFrameSegmentsV3]) -> None:
            if _ACTIVE_FRAMES.get(meter_id) is reference:
                del _ACTIVE_FRAMES[meter_id]

        _ACTIVE_FRAMES[meter_id] = ref(segments, dropped)
        meter.release_digests(3 * _RECORD_DIGEST_BYTES)
        return segments
    except BaseException:
        if retained:
            meter.release_digests(5 * _RECORD_DIGEST_BYTES)
        meter.release_scratch(scratch)
        raise


@dataclass(frozen=True, slots=True)
class NativeConnectedWireRecordViewV3:
    ordinal: int
    frame: NativeConnectedRangeV3
    header: NativeConnectedRangeV3
    root: NativeConnectedRangeV3
    capture: NativeConnectedRangeV3
    request: NativeConnectedRangeV3
    outcomes: NativeConnectedRangeV3

    @contextmanager
    def header_lease(
        self, source: bytes, *, meter: NativeConnectedWireMeterV3
    ) -> Iterator[NativeConnectedRecordHeaderV3]:
        """Materialize one head; retaining it after exit requires a separate charge."""
        # Header tokens and decoded UUIDs coexist during bounded conversion.
        meter.retain_scratch(2 * self.header.size)
        retained_digests = False
        try:
            # Canonical hex text and its raw32 value coexist during decoding.
            meter.retain_digests(3 * _RECORD_DIGEST_BYTES)
            retained_digests = True
            yield _record_header(self.header.view(source), meter)
        finally:
            if retained_digests:
                meter.release_digests(3 * _RECORD_DIGEST_BYTES)
            meter.release_scratch(2 * self.header.size)


@dataclass(frozen=True, slots=True)
class NativeConnectedWireLayoutV3:
    proof: NativeConnectedRequestProofV3
    header: NativeConnectedRangeV3
    records: tuple[NativeConnectedWireRecordViewV3, ...]

    @property
    def index_payload_bytes(self) -> int:
        return 16 + len(self.records) * (8 + 6 * 16)

    def release_index(self, *, meter: NativeConnectedWireMeterV3) -> None:
        """The owning caller releases this charged index after its last use."""
        meter.release_scratch(self.index_payload_bytes)


class _SetDigest:
    def __init__(self, domain: bytes, count: int, meter: NativeConnectedWireMeterV3) -> None:
        self.state = sha256()
        self.meter = meter
        self._piece(domain + b"\x00" + struct.pack(">I", count + 1))
        self._piece(b"\x01" + struct.pack(">I", 8) + struct.pack(">q", count))

    def _piece(self, raw: bytes) -> None:
        self.meter.charge(bytes_written=len(raw), sha_input_bytes=len(raw), node_visits=1)
        self.state.update(raw)

    def add(self, raw: bytes) -> None:
        self._piece(b"\x01" + struct.pack(">I", len(raw)) + raw)

    def digest(self) -> bytes:
        self.meter.charge(bytes_written=32, node_visits=1)
        return self.state.digest()


def _fragment_hash(raw: memoryview, meter: NativeConnectedFrameMeterV3) -> bytes:
    meter.charge(sha_input_bytes=len(raw), bytes_written=32, node_visits=1)
    return sha256(raw).digest()


def _check_fragment_hashes(
    header: NativeConnectedRecordHeaderV3,
    fragments: tuple[memoryview, memoryview, memoryview, memoryview],
    meter: NativeConnectedFrameMeterV3,
) -> None:
    for raw, expected in zip(
        fragments[:3],
        (header.root_proof_sha256, header.capture_sha256, header.original_request_sha256),
        strict=True,
    ):
        if _fragment_hash(raw, meter) != expected:
            raise NativeConnectedV3Error("CONNECTED_V3_FRAGMENT_HASH")


def _membership_entry(ordinal: int, header: NativeConnectedRecordHeaderV3) -> bytes:
    return (
        struct.pack(">q", ordinal)
        + header.owner_user_id.bytes
        + header.served_request_id.bytes
        + header.original_request_id.bytes
    )


def _check_identity(
    header: NativeConnectedRecordHeaderV3,
    member: tuple[UUID, UUID],
    originals: list[int],
    meter: NativeConnectedWireMeterV3,
) -> None:
    if (header.owner_user_id, header.served_request_id) != member:
        raise NativeConnectedV3Error("CONNECTED_V3_MEMBERSHIP")
    # At most64 fixed-width identities. A linear check is bounded and metered,
    # avoids adversarial hash tables, and keeps no decoded per-record digests.
    meter.charge(node_visits=len(originals) + 1)
    if header.original_request_id.int in originals:
        raise NativeConnectedV3Error("CONNECTED_V3_ORIGINAL_MEMBERSHIP")
    originals.append(header.original_request_id.int)


def _check_disjoint(
    originals: list[int], selection: NativeConnectedSelectionV3, meter: NativeConnectedWireMeterV3
) -> None:
    for original in originals:
        for _, served in selection.members:
            meter.charge(node_visits=1)
            if original == served.int:
                raise NativeConnectedV3Error("CONNECTED_V3_ORIGINAL_MEMBERSHIP")


def _proof_header(
    raw: memoryview, count: int, meter: NativeConnectedWireMeterV3
) -> tuple[bytes, bytes]:
    values = _HeaderScanner(raw, meter).scan(_PROOF_FIELDS)
    expected: dict[str, HeaderScalarV3] = {
        "kind": "PRIVATE_CONNECTED_NATIVE_SOURCE_V3",
        "proof_version": 3,
        "request_hash_format_version": 3,
        "original_capture_version": 3,
        "serving_contract_version": 2,
        "record_count": count,
    }
    for key, value in expected.items():
        if type(values[key]) is not type(value) or values[key] != value:
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_HEADER")
    return bytes.fromhex(require_hex_v3(values["membership_sha256"])), bytes.fromhex(
        require_hex_v3(values["record_set_sha256"])
    )


def _proof_header_values(count: int, membership: str, records: str) -> dict[str, HeaderScalarV3]:
    return {
        "kind": "PRIVATE_CONNECTED_NATIVE_SOURCE_V3",
        "proof_version": 3,
        "request_hash_format_version": 3,
        "original_capture_version": 3,
        "serving_contract_version": 2,
        "record_count": count,
        "membership_sha256": membership,
        "record_set_sha256": records,
    }


def preflight_native_connected_wire_v3(
    proof: NativeConnectedRequestProofV3,
    *,
    selection: NativeConnectedSelectionV3,
    meter: NativeConnectedWireMeterV3,
) -> NativeConnectedWireLayoutV3:
    """Charge wire bytes before scanning; do not hydrate any large fragment."""
    if (
        type(proof) is not NativeConnectedRequestProofV3
        or type(selection) is not NativeConnectedSelectionV3
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_TYPED_WIRE")
    raw = proof.document
    count = len(selection.members)
    meter.admit_wire(len(raw), count)
    meter.narrow_wire(len(raw), count)
    meter.charge(bytes_read=min(len(raw), _PREFIX_BYTES), node_visits=1)
    if len(raw) < _PREFIX_BYTES or raw[:6] != _MAGIC:
        raise NativeConnectedV3Error("CONNECTED_V3_PREFIX")
    version, declared_count, header_size = struct.unpack_from(">HHI", raw, 6)
    if version != 3 or declared_count != count or not 1 <= header_size <= _MAX_HEADER_BYTES:
        raise NativeConnectedV3Error("CONNECTED_V3_PREFIX")
    header_end = _PREFIX_BYTES + header_size
    if header_end > len(raw):
        raise NativeConnectedV3Error("CONNECTED_V3_TRUNCATED")
    index_bytes = 16 + count * (8 + 6 * 16)
    identity_bytes = 16 * count
    meter.retain_scratch(index_bytes + identity_bytes)
    retained_digests = False
    keep_index = False
    try:
        meter.retain_digests(320)
        retained_digests = True
        meter.retain_scratch(2 * header_size)
        try:
            expected_membership, expected_records = _proof_header(
                memoryview(raw)[_PREFIX_BYTES:header_end], count, meter
            )
        finally:
            meter.release_scratch(2 * header_size)
        membership, record_set = (
            _SetDigest(_MEMBERSHIP_DOMAIN, count, meter),
            _SetDigest(_RECORD_SET_DOMAIN, count, meter),
        )
        originals: list[int] = []
        records: list[NativeConnectedWireRecordViewV3] = []
        position = header_end
        for ordinal, member in enumerate(selection.members, 1):
            meter.charge(bytes_read=24, node_visits=1)
            if position + 24 > len(raw):
                raise NativeConnectedV3Error("CONNECTED_V3_TRUNCATED")
            size, header_length, *lengths = struct.unpack_from(">6I", raw, position)
            if (
                not 1 <= header_length <= _MAX_HEADER_BYTES
                or any(not 1 <= size <= MAX_CONNECTED_RECORD_BYTES for size in lengths)
                or size != 24 + header_length + sum(lengths)
                or size > MAX_CONNECTED_RECORD_BYTES
                or position + size > len(raw)
            ):
                raise NativeConnectedV3Error("CONNECTED_V3_RECORD_LENGTH")
            start = position + 24
            header_range = NativeConnectedRangeV3(start, start + header_length)
            start += header_length
            ranges = []
            for length in lengths:
                ranges.append(NativeConnectedRangeV3(start, start + length))
                start += length
            record = NativeConnectedWireRecordViewV3(
                ordinal,
                NativeConnectedRangeV3(position, position + size),
                header_range,
                ranges[0],
                ranges[1],
                ranges[2],
                ranges[3],
            )
            with record.header_lease(raw, meter=meter) as header:
                _check_identity(header, member, originals, meter)
                fragments = tuple(
                    value.view(raw)
                    for value in (record.root, record.capture, record.request, record.outcomes)
                )
                _check_fragment_hashes(
                    header, (fragments[0], fragments[1], fragments[2], fragments[3]), meter
                )
                membership.add(_membership_entry(ordinal, header))
                record_set.add(
                    struct.pack(">q", ordinal) + _fragment_hash(record.frame.view(raw), meter)
                )
            del header
            records.append(record)
            position += size
        if position != len(raw):
            raise NativeConnectedV3Error("CONNECTED_V3_TRAILING_BYTES")
        _check_disjoint(originals, selection, meter)
        if membership.digest() != expected_membership or record_set.digest() != expected_records:
            raise NativeConnectedV3Error("CONNECTED_V3_SET_HASH")
        if _fragment_hash(memoryview(raw), meter).hex() != proof.sha256:
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_HASH")
        result = NativeConnectedWireLayoutV3(
            proof, NativeConnectedRangeV3(_PREFIX_BYTES, header_end), tuple(records)
        )
        keep_index = True
        return result
    finally:
        if retained_digests:
            meter.release_digests(320)
        meter.release_scratch(identity_bytes + (0 if keep_index else index_bytes))


def build_native_connected_proof_v3(
    records: Iterable[NativeConnectedWireRecordPartsV3],
    *,
    selection: NativeConnectedSelectionV3,
    total_wire_upper_bound: int,
    meter: NativeConnectedWireMeterV3,
) -> NativeConnectedRequestProofV3:
    """Build from one active owned record projection under prior wire admission."""
    if type(selection) is not NativeConnectedSelectionV3:
        raise NativeConnectedV3Error("CONNECTED_V3_TYPED_WIRE")
    require_int_v3(total_wire_upper_bound, _PREFIX_BYTES + 1, MAX_CONNECTED_PROOF_BYTES)
    count = len(selection.members)
    meter.admit_wire(total_wire_upper_bound, count)
    builder_scratch = 24 * count + 2 * _MAX_HEADER_BYTES
    meter.retain_scratch(builder_scratch)
    retained_digests = False
    output = BytesIO()
    try:
        meter.retain_digests(320 + 3 * _RECORD_DIGEST_BYTES)
        retained_digests = True
        # Changing header values have fixed widths. Determine outer overhead
        # before consuming the first record or allocating its framed body.
        placeholder = _encode_header(_proof_header_values(count, "00" * 32, "00" * 32), meter)
        proof_header_size = len(placeholder)
        if _PREFIX_BYTES + proof_header_size > total_wire_upper_bound:
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_BYTES")
        prefix = _MAGIC + struct.pack(">HHI", 3, count, proof_header_size)
        meter.charge(
            bytes_read=len(prefix) + proof_header_size,
            bytes_written=len(prefix) + proof_header_size,
        )
        output.write(prefix)
        output.write(placeholder)
        iterator = iter(records)
        membership, record_set = (
            _SetDigest(_MEMBERSHIP_DOMAIN, count, meter),
            _SetDigest(_RECORD_SET_DOMAIN, count, meter),
        )
        originals: list[int] = []
        body_bytes = 0
        for ordinal, member in enumerate(selection.members, 1):
            try:
                parts = next(iterator)
            except StopIteration:
                raise NativeConnectedV3Error("CONNECTED_V3_RECORD_COUNT") from None
            if type(parts) is not NativeConnectedWireRecordPartsV3:
                raise NativeConnectedV3Error("CONNECTED_V3_RECORD_TYPE")
            header = parts.header
            _check_identity(header, member, originals, meter)
            with prepare_native_connected_frame_v3(parts, meter=meter) as frame:
                body_bytes += frame.record_length
                if body_bytes + _PREFIX_BYTES + proof_header_size > total_wire_upper_bound:
                    raise NativeConnectedV3Error("CONNECTED_V3_RECORD_LENGTH")
                # Only the admitted final output buffer receives the whole frame.
                frame_hash = sha256()
                for piece in frame.pieces:
                    meter.charge(
                        bytes_read=len(piece),
                        bytes_written=len(piece),
                        sha_input_bytes=len(piece),
                        node_visits=1,
                    )
                    output.write(piece)
                    frame_hash.update(piece)
            membership.add(_membership_entry(ordinal, header))
            meter.charge(bytes_written=32, node_visits=1)
            record_set.add(struct.pack(">q", ordinal) + frame_hash.digest())
            del parts, header
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            raise NativeConnectedV3Error("CONNECTED_V3_RECORD_COUNT")
        _check_disjoint(originals, selection, meter)
        proof_header = _encode_header(
            _proof_header_values(count, membership.digest().hex(), record_set.digest().hex()),
            meter,
        )
        total = _PREFIX_BYTES + len(proof_header) + body_bytes
        if total > total_wire_upper_bound:
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_BYTES")
        meter.narrow_wire(total, count)
        meter.charge(bytes_read=len(proof_header), bytes_written=len(proof_header))
        output.seek(_PREFIX_BYTES)
        output.write(proof_header)
        # Conservatively charge a full snapshot copy; BytesIO may transfer its
        # immutable bytes buffer without copying. Closing prevents further writes.
        meter.charge(bytes_written=total, bytes_read=total, node_visits=1)
        raw = output.getvalue()
        output.close()
        return NativeConnectedRequestProofV3(raw, _fragment_hash(memoryview(raw), meter).hex())
    finally:
        output.close()
        if retained_digests:
            meter.release_digests(320 + 3 * _RECORD_DIGEST_BYTES)
        meter.release_scratch(builder_scratch)

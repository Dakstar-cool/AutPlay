"""Exact typed source-index/cursor projections, never original-time authority.

The new projections describe independently locked SQL rows. They are not root
or temporal hashes. Consumers stream them through an already admitted ledger.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from autplay.domain.native_original_capture_v3 import NativeOriginalCaptureV3Meter
from autplay.domain.native_served_authority import encode_serving_lp, integer_bytes

MAX_SOURCE_INDEX_BYTES_V3: Final = 2_097_152
SOURCE_INDEX_ENTRY_DOMAIN_V3: Final = "autplay.native-source-index-entry.v3"
SOURCE_CURSOR_DOMAIN_V3: Final = "autplay.native-source-cursor.v3"


class NativeSourceDescriptorV3Error(ValueError):
    pass


def _uuid(value: UUID | None, *, nullable: bool = False) -> None:
    if (value is None and not nullable) or (value is not None and type(value) is not UUID):
        raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_UUID")


def _integer(value: int, lower: int = 0, upper: int = 2**63 - 1) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_INTEGER")


def _digest(value: bytes) -> None:
    if type(value) is not bytes or len(value) != 32:
        raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_DIGEST")


def _encode(
    domain: str, fields: tuple[bytes | None, ...], meter: NativeOriginalCaptureV3Meter
) -> bytes:
    size = len(domain) + 5 + sum(1 if value is None else 5 + len(value) for value in fields)
    # Small scalar field encodings, outer framing, joined output and its read
    # are charged before the existing frozen LP encoder allocates anything.
    meter.reserve(byte_work_bound=3 * size, node_visit_bound=1 + len(fields))
    meter.charge(bytes_read=size, bytes_written=2 * size, node_visits=1 + len(fields))
    raw = encode_serving_lp(domain, fields)
    if len(raw) != size:
        raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_LENGTH")
    return raw


@dataclass(frozen=True, slots=True)
class NativeSourceIndexEntryV3:
    original_request_id: UUID
    owner_user_id: UUID
    source_event_id: UUID
    source_request_sha256: bytes
    normalized_payload_sha256: bytes
    device_id: UUID
    journal_epoch: UUID
    device_sequence: int
    server_sequence: int
    event_type: str
    association_id: UUID | None
    causal_request_id: UUID | None
    impression_event_id: UUID | None
    retained_until_us: int

    def __post_init__(self) -> None:
        for value in (
            self.original_request_id,
            self.owner_user_id,
            self.source_event_id,
            self.device_id,
            self.journal_epoch,
        ):
            _uuid(value)
        _digest(self.source_request_sha256)
        _digest(self.normalized_payload_sha256)
        _integer(self.device_sequence, 1, 9_007_199_254_740_991)
        _integer(self.server_sequence, 1, 9_007_199_254_740_991)
        _integer(self.retained_until_us)
        if self.retained_until_us % 1000:
            raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_TIME")
        for causal_value in (self.association_id, self.causal_request_id, self.impression_event_id):
            _uuid(causal_value, nullable=True)
        if type(self.event_type) is not str or self.event_type not in (
            "LISTENING_EVENT_RECORDED",
            "RECOMMENDATION_FEEDBACK_RECORDED",
            "RECOMMENDATION_IMPRESSION_RECORDED",
        ):
            raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_EVENT")
        causal = (
            self.association_id is not None,
            self.causal_request_id is not None,
            self.impression_event_id is not None,
        )
        allowed = {
            "LISTENING_EVENT_RECORDED": ((False, False, False), (True, True, True)),
            "RECOMMENDATION_IMPRESSION_RECORDED": ((True, True, False),),
            "RECOMMENDATION_FEEDBACK_RECORDED": ((True, True, True),),
        }
        if causal not in allowed[self.event_type]:
            raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_CAUSALITY")

    @property
    def encoded_length(self) -> int:
        nulls = sum(
            value is None
            for value in (self.association_id, self.causal_request_id, self.impression_event_id)
        )
        # Five UUIDs, two digests, three I64s, one event string and three
        # nullable UUIDs; each non-null field has its five-byte outer tag.
        payload = 5 * 16 + 2 * 32 + 3 * 8 + len(self.event_type) + (3 - nulls) * 16
        return len(SOURCE_INDEX_ENTRY_DOMAIN_V3) + 5 + 5 * (14 - nulls) + nulls + payload

    def encode(self, *, meter: NativeOriginalCaptureV3Meter) -> bytes:
        meter.reserve(byte_work_bound=3 * self.encoded_length, node_visit_bound=15)
        return _encode(
            SOURCE_INDEX_ENTRY_DOMAIN_V3,
            (
                self.original_request_id.bytes,
                self.owner_user_id.bytes,
                self.source_event_id.bytes,
                self.source_request_sha256,
                self.normalized_payload_sha256,
                self.device_id.bytes,
                self.journal_epoch.bytes,
                integer_bytes(self.device_sequence),
                integer_bytes(self.server_sequence),
                self.event_type.encode("ascii"),
                None if self.association_id is None else self.association_id.bytes,
                None if self.causal_request_id is None else self.causal_request_id.bytes,
                None if self.impression_event_id is None else self.impression_event_id.bytes,
                integer_bytes(self.retained_until_us),
            ),
            meter,
        )


def native_source_index_prefix_v3(count: int, *, meter: NativeOriginalCaptureV3Meter) -> bytes:
    _integer(count, 0, 10_000)
    meter.reserve(byte_work_bound=14, node_visit_bound=1)
    meter.charge(bytes_written=14, node_visits=1)
    return b"APNI3\0" + struct.pack(">I", count)


@dataclass(frozen=True, slots=True)
class NativeSourceCursorV3:
    original_request_id: UUID
    owner_user_id: UUID
    source_event_count: int
    source_membership_sha256: bytes
    cutoff_at_ms: int
    native_interaction_watermark: int
    source_generation: int
    created_transaction: str
    created_at_us: int
    expires_at_us: int

    def __post_init__(self) -> None:
        _uuid(self.original_request_id)
        _uuid(self.owner_user_id)
        _integer(self.source_event_count, 0, 10_000)
        _digest(self.source_membership_sha256)
        _integer(self.cutoff_at_ms, 0, 9_007_199_254_740_991)
        _integer(self.native_interaction_watermark, 0, 9_007_199_254_740_991)
        _integer(self.source_generation, 1, 9_007_199_254_740_991)
        transaction = self.created_transaction
        if (
            type(transaction) is not str
            or not 1 <= len(transaction) <= 20
            or not transaction.isascii()
            or not transaction.isdigit()
            or transaction.startswith("0")
            or int(transaction) > 2**64 - 1
        ):
            raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_XID8")
        _integer(self.created_at_us)
        _integer(self.expires_at_us)
        if (
            self.expires_at_us != self.created_at_us + 15_552_000_000_000
            or self.cutoff_at_ms != self.created_at_us // 1000
        ):
            raise NativeSourceDescriptorV3Error("NATIVE_V3_DESCRIPTOR_TIME")

    @property
    def encoded_length(self) -> int:
        return (
            len(SOURCE_CURSOR_DOMAIN_V3)
            + 5
            + 11 * 5
            + 2 * 16
            + 6
            + 6 * 8
            + 32
            + len(self.created_transaction)
        )

    def encode(self, *, meter: NativeOriginalCaptureV3Meter) -> bytes:
        meter.reserve(byte_work_bound=3 * self.encoded_length, node_visit_bound=12)
        return _encode(
            SOURCE_CURSOR_DOMAIN_V3,
            (
                self.original_request_id.bytes,
                self.owner_user_id.bytes,
                b"ACTIVE",
                integer_bytes(self.source_event_count),
                self.source_membership_sha256,
                integer_bytes(self.cutoff_at_ms),
                integer_bytes(self.native_interaction_watermark),
                integer_bytes(self.source_generation),
                self.created_transaction.encode("ascii"),
                integer_bytes(self.created_at_us),
                integer_bytes(self.expires_at_us),
            ),
            meter,
        )

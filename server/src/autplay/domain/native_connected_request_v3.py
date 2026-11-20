"""Closed native connected-source V3 values; types never confer SQL authority."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, fields
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from typing import Final
from uuid import UUID

from autplay.domain.adaptive_recommendations import DimensionKind, TemporalDimension
from autplay.domain.native_original_capture_v3 import MAX_ORIGINAL_BASELINE_V4_BYTES
from autplay.domain.native_request_budget_v3 import NativeRequestV3Error
from autplay.domain.native_request_roots_v3 import (
    ConnectedP11InputOriginV3,
    NativeP11ExpectedValuesV3,
    VerifiedNativeP11RootV3,
)
from autplay.domain.native_temporal import NativeTemporalCausality, NativeTemporalFact
from autplay.domain.recommendations import SUPPORTED_CONTEXTS

MAX_CONNECTED_RECORD_BYTES: Final = 2_097_152
MAX_CONNECTED_PROOF_BYTES: Final = 33_554_432
MAX_CONNECTED_RECORDS: Final = 64
MAX_CONNECTED_PACKETS: Final = 16
MAX_CONNECTED_AGGREGATE_RECORDS: Final = 1024
MAX_CONNECTED_AGGREGATE_BYTES: Final = 536_870_912
MAX_CONNECTED_DEPENDENCY_BYTES: Final = 33_554_432
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
TRUTH_HORIZON_US: Final = 15_552_000_000_000
OUTCOME_WINDOW_US: Final = 604_800_000_000


class NativeConnectedV3Error(NativeRequestV3Error):
    """Stable refusal code without private source values."""


def require_int_v3(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise NativeConnectedV3Error("CONNECTED_V3_INTEGER")
    return value


def require_uuid_v3(value: object) -> UUID:
    if type(value) is not UUID:
        raise NativeConnectedV3Error("CONNECTED_V3_UUID")
    return value


def require_digest_v3(value: object) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise NativeConnectedV3Error("CONNECTED_V3_DIGEST")
    return value


def require_hex_v3(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_DIGEST")
    return value


def require_text_v3(value: object, *, maximum: int = 4096) -> str:
    if type(value) is not str or len(value) > maximum:
        raise NativeConnectedV3Error("CONNECTED_V3_TEXT")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise NativeConnectedV3Error("CONNECTED_V3_TEXT") from None
    if size > maximum or unicodedata.normalize("NFC", value) != value:
        raise NativeConnectedV3Error("CONNECTED_V3_TEXT")
    return value


@dataclass(frozen=True, slots=True)
class NativeConnectedSelectionV3:
    members: tuple[tuple[UUID, UUID], ...]

    def __post_init__(self) -> None:
        if type(self.members) is not tuple or not 1 <= len(self.members) <= 64:
            raise NativeConnectedV3Error("CONNECTED_V3_SELECTION")
        previous: tuple[int, int] | None = None
        requests: list[int] = []
        for member in self.members:
            if type(member) is not tuple or len(member) != 2:
                raise NativeConnectedV3Error("CONNECTED_V3_SELECTION")
            key = tuple(require_uuid_v3(value).int for value in member)
            if previous is not None and key <= previous:
                raise NativeConnectedV3Error("CONNECTED_V3_SELECTION")
            previous = (key[0], key[1])
            requests.append(key[1])
        ordered = sorted(requests)
        if any(left == right for left, right in pairwise(ordered)):
            raise NativeConnectedV3Error("CONNECTED_V3_SELECTION")


@dataclass(frozen=True, slots=True)
class NativeConnectedRequestProofV3:
    """Retained personal wire bytes requiring fresh replay before every use."""

    document: bytes
    sha256: str

    def __post_init__(self) -> None:
        if type(self.document) is not bytes or not 1 <= len(self.document) <= 33_554_432:
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_BYTES")
        require_hex_v3(self.sha256)


@dataclass(frozen=True, slots=True)
class NativeConnectedProofInputV3:
    """A selected immutable packet; its locator and bytes supply no SQL grant."""

    packet_identity: bytes
    selection: NativeConnectedSelectionV3
    proof: NativeConnectedRequestProofV3

    def __post_init__(self) -> None:
        require_digest_v3(self.packet_identity)
        if (
            type(self.selection) is not NativeConnectedSelectionV3
            or type(self.proof) is not NativeConnectedRequestProofV3
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_PROOF_INPUT")


@dataclass(frozen=True, slots=True)
class NativeConnectedRangeV3:
    """A half-open range within its already admitted immutable source buffer."""

    start: int
    stop: int

    def __post_init__(self) -> None:
        require_int_v3(self.start, 0, MAX_CONNECTED_PROOF_BYTES)
        require_int_v3(self.stop, self.start, MAX_CONNECTED_PROOF_BYTES)

    @property
    def size(self) -> int:
        return self.stop - self.start

    def view(self, source: bytes) -> memoryview:
        if type(source) is not bytes or self.stop > len(source):
            raise NativeConnectedV3Error("CONNECTED_V3_RANGE")
        return memoryview(source)[self.start : self.stop]


@dataclass(frozen=True, slots=True)
class NativeConnectedCountsV3:
    tracks: int
    items: int
    facts: int
    evidence: int
    outcomes: int
    dimensions: int

    def __post_init__(self) -> None:
        require_int_v3(self.tracks, 0, 5000)
        require_int_v3(self.items, 0, 100)
        require_int_v3(self.facts, 0, 10_000)
        require_int_v3(self.evidence, 0, 2 * self.facts)
        require_int_v3(self.outcomes, 0, 5000)
        require_int_v3(self.dimensions, 0, 512)


@dataclass(frozen=True, slots=True)
class NativeP11UnvalidatedHeadV3:
    """Size-only locked SQL descriptor; consumers must independently validate it."""

    owner_user_id: UUID
    served_request_id: UUID
    original_request_id: UUID
    device_id: UUID
    journal_epoch: UUID
    server_instance_id: UUID
    identity_epoch: int
    request_hash_format_version: int
    root_proof_version: int
    original_capture_version: int
    serving_contract_version: int
    attribution_wire_version: int
    normalized_event_wire_version: int
    state: str
    serving_provenance: str
    inference_evidence_sha256: None
    fallback_reason: None
    root_proof_sha256: bytes
    request_root_sha256: bytes
    decision_sha256: bytes
    connection_seal_sha256: bytes
    capture_sha256: bytes
    original_request_sha256: bytes
    original_ranking_sha256: bytes
    baseline_snapshot_id: UUID
    temporal_snapshot_id: UUID
    baseline_snapshot_sha256: bytes
    temporal_snapshot_sha256: bytes
    candidate_membership_sha256: bytes
    original_policy_snapshot_sha256: bytes
    original_availability_snapshot_sha256: bytes
    eligibility_policy_sha256: bytes
    pipeline_manifest_sha256: bytes
    source_codec_sha256: bytes
    original_profile_document_sha256: bytes
    original_profile_sha256: bytes
    original_profile_binary64_sha256: bytes
    source_generation: int
    pipeline_key: str
    pipeline_version: str
    surface: str
    context: str
    seed: int
    interaction_watermark: int
    native_interaction_watermark: int
    catalog_snapshot: int
    cutoff_at_ms: int
    effective_limit: int
    endpoint_max: int
    exploration_units_e8: int
    source_snapshot_track_count: int
    candidate_universe_count: int
    p11_item_count: int
    served_item_count: int
    source_event_count: int
    source_evidence_count: int
    profile_dimension_count: int
    original_created_at_us: int
    served_created_at_us: int
    original_expires_at_us: int
    replay_expires_at_us: int
    served_truth_expires_at_us: int
    created_transaction: str
    consent_generation: int
    consent_receipt_sha256: bytes
    root_byte_length: int
    capture_byte_length: int
    original_request_byte_length: int
    original_ranking_byte_length: int
    baseline_byte_length: int
    original_profile_byte_length: int
    source_cursor_byte_length: int
    pipeline_byte_length: int
    source_index_byte_length: int
    original_items_byte_length: int
    served_items_byte_length: int

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name.endswith("_sha256") and field.name != "inference_evidence_sha256":
                require_digest_v3(value)
            elif field.name.endswith("_id") or field.name == "journal_epoch":
                require_uuid_v3(value)
        if self.original_request_id == self.served_request_id:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_IDENTITY")
        require_int_v3(self.root_proof_version, 3, 4)
        versions = (
            (self.request_hash_format_version, 3),
            (self.original_capture_version, 3),
            (self.serving_contract_version, 2),
            (self.attribution_wire_version, 1),
            (self.normalized_event_wire_version, 2),
        )
        for actual, expected in versions:
            require_int_v3(actual, expected, expected)
        if (
            self.state != "COMMITTED_P11"
            or type(self.state) is not str
            or self.serving_provenance != "P11"
            or type(self.serving_provenance) is not str
            or self.inference_evidence_sha256 is not None
            or self.fallback_reason is not None
            or self.surface != "recommendations"
            or type(self.surface) is not str
            or require_text_v3(self.context) not in SUPPORTED_CONTEXTS
            or self.pipeline_key != "cpu-baseline"
            or type(self.pipeline_key) is not str
            or self.pipeline_version != "1"
            or type(self.pipeline_version) is not str
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_CONTRACT")
        require_int_v3(self.identity_epoch, 1, 2**63 - 1)
        for name in (
            "seed",
            "interaction_watermark",
            "native_interaction_watermark",
            "catalog_snapshot",
            "cutoff_at_ms",
        ):
            require_int_v3(getattr(self, name), 0, MAX_SAFE_INTEGER)
        require_int_v3(self.effective_limit, 1, 100)
        require_int_v3(self.endpoint_max, 100, 100)
        require_int_v3(self.exploration_units_e8, 0, 100_000_000)
        require_int_v3(self.source_snapshot_track_count, 0, 5000)
        require_int_v3(self.candidate_universe_count, 0, self.source_snapshot_track_count)
        require_int_v3(self.p11_item_count, 0, self.effective_limit)
        require_int_v3(self.served_item_count, self.p11_item_count, self.p11_item_count)
        require_int_v3(self.source_event_count, 0, 10_000)
        require_int_v3(self.source_evidence_count, 0, min(2 * self.source_event_count, 10_000))
        require_int_v3(self.profile_dimension_count, 0, 512)
        created = require_int_v3(self.original_created_at_us, 0, 2**63 - 1 - TRUTH_HORIZON_US)
        require_int_v3(self.served_created_at_us, created, created)
        if self.cutoff_at_ms != created // 1000:
            raise NativeConnectedV3Error("CONNECTED_V3_HEAD_TIME")
        for name in (
            "original_expires_at_us",
            "replay_expires_at_us",
            "served_truth_expires_at_us",
        ):
            require_int_v3(
                getattr(self, name), created + TRUTH_HORIZON_US, created + TRUTH_HORIZON_US
            )
        transaction = require_text_v3(self.created_transaction, maximum=20)
        if not transaction.isascii() or not transaction.isdigit() or transaction.startswith("0"):
            raise NativeConnectedV3Error("CONNECTED_V3_XID8")
        require_int_v3(int(transaction), 1, 2**64 - 1)
        require_int_v3(self.consent_generation, 1, MAX_SAFE_INTEGER)
        require_int_v3(self.source_generation, 1, MAX_SAFE_INTEGER)
        for field in fields(self):
            if field.name.endswith("_byte_length"):
                lower = (
                    0
                    if field.name
                    in (
                        "source_index_byte_length",
                        "original_items_byte_length",
                        "served_items_byte_length",
                    )
                    else 1
                )
                maximum = (
                    MAX_ORIGINAL_BASELINE_V4_BYTES
                    if field.name == "baseline_byte_length"
                    else MAX_CONNECTED_RECORD_BYTES
                )
                require_int_v3(getattr(self, field.name), lower, maximum)


class NativeConnectedDependencyKindV3(StrEnum):
    ROOT = "ROOT"
    ORIGINAL = "ORIGINAL"
    CAPTURE = "CAPTURE"
    ORIGINAL_REQUEST = "ORIGINAL_REQUEST"
    ORIGINAL_BASELINE = "ORIGINAL_BASELINE"
    ORIGINAL_RANKING = "ORIGINAL_RANKING"
    ORIGINAL_NUMERIC_PROFILE = "ORIGINAL_NUMERIC_PROFILE"
    SOURCE_INDEX = "SOURCE_INDEX"
    CURSOR = "CURSOR"
    ACCEPTED_EVENT = "ACCEPTED_EVENT"
    INBOX_ACK = "INBOX_ACK"
    TARGET = "TARGET"
    ASSOCIATION = "ASSOCIATION"
    PRIOR_ROOT = "PRIOR_ROOT"
    DECISION = "DECISION"
    P11_ITEMS = "P11_ITEMS"
    SERVED_ITEMS = "SERVED_ITEMS"
    PIPELINE = "PIPELINE"
    MODEL = "MODEL"
    LICENSE = "LICENSE"
    TOKENIZER = "TOKENIZER"
    VECTORS = "VECTORS"
    SQL_COMPILER = "SQL_COMPILER"
    NEGATIVE_LEDGER = "NEGATIVE_LEDGER"


class NativeConnectedDependencySizeModeV3(StrEnum):
    EXACT = "EXACT"
    UPPER_BOUND = "UPPER_BOUND"


@dataclass(frozen=True, slots=True)
class NativeConnectedDependencyDescriptorV3:
    kind: NativeConnectedDependencyKindV3
    owner_user_id: UUID
    entity_id: UUID
    version: int
    byte_length: int
    rows: int
    sha256: bytes | None = None
    size_mode: NativeConnectedDependencySizeModeV3 = NativeConnectedDependencySizeModeV3.EXACT

    def __post_init__(self) -> None:
        if type(self.kind) is not NativeConnectedDependencyKindV3:
            raise NativeConnectedV3Error("CONNECTED_V3_DEPENDENCY_KIND")
        if type(self.size_mode) is not NativeConnectedDependencySizeModeV3:
            raise NativeConnectedV3Error("CONNECTED_V3_DEPENDENCY_SIZE_MODE")
        if self.size_mode is NativeConnectedDependencySizeModeV3.UPPER_BOUND and (
            self.kind is not NativeConnectedDependencyKindV3.PRIOR_ROOT
            or self.version != 2
            or self.sha256 is not None
        ):
            # This represents accounting only. The owning adapter must prove
            # the immediate immutable whole-value bound before serialization.
            raise NativeConnectedV3Error("CONNECTED_V3_DEPENDENCY_SIZE_MODE")
        require_uuid_v3(self.owner_user_id)
        require_uuid_v3(self.entity_id)
        require_int_v3(self.version, 0, 2**63 - 1)
        maximum = (
            16_777_216
            if self.kind is NativeConnectedDependencyKindV3.VECTORS
            else (
                8_388_608
                if self.kind is NativeConnectedDependencyKindV3.SQL_COMPILER
                else (
                    65_536
                    if self.kind
                    in (
                        NativeConnectedDependencyKindV3.MODEL,
                        NativeConnectedDependencyKindV3.LICENSE,
                    )
                    else 2_097_152
                )
            )
        )
        if self.kind is NativeConnectedDependencyKindV3.ORIGINAL_BASELINE:
            maximum = MAX_ORIGINAL_BASELINE_V4_BYTES
        require_int_v3(self.byte_length, 0, maximum)
        require_int_v3(
            self.rows,
            0,
            200_000 if self.kind is NativeConnectedDependencyKindV3.NEGATIVE_LEDGER else 20_000,
        )
        if self.sha256 is not None:
            require_digest_v3(self.sha256)


@dataclass(frozen=True, slots=True)
class FreshNativeConnectedContextV3:
    """Independent locked SQL source retained by its owning transaction only."""

    head: NativeP11UnvalidatedHeadV3
    expected: NativeP11ExpectedValuesV3
    capture_document: bytes
    original_request_document: bytes
    original_ranking_document: bytes
    facts: tuple[NativeTemporalFact, ...]
    transaction_binding: tuple[int, int]
    original_profile_document: bytes

    def __post_init__(self) -> None:
        if (
            type(self.head) is not NativeP11UnvalidatedHeadV3
            or type(self.expected) is not NativeP11ExpectedValuesV3
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT")
        if self.expected.served_request_id != self.head.served_request_id:
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_IDENTITY")
        identity, query, baseline = (
            self.expected.identity,
            self.expected.query,
            self.expected.baseline,
        )
        ref, pipeline = baseline.reference, self.expected.pipeline
        if (
            identity.device != self.head.device_id
            or identity.journal_epoch != self.head.journal_epoch
            or identity.server_instance != self.head.server_instance_id
            or identity.identity_epoch != self.head.identity_epoch
            or query.user_id != self.head.owner_user_id
            or query.surface.value != self.head.surface
            or query.context != self.head.context
            or query.seed != self.head.seed
            or query.limit != self.head.effective_limit
            or self.expected._created_at_us != self.head.original_created_at_us
            or self.expected._reference_snapshot != (self.head.root_proof_version == 4)
            or ref.snapshot_id != self.head.baseline_snapshot_id
            or ref.input_snapshot_sha256 != self.head.baseline_snapshot_sha256.hex()
            or ref.interaction_watermark != self.head.interaction_watermark
            or ref.catalog_snapshot != self.head.catalog_snapshot
            or ref.policy_snapshot_sha256 != self.head.original_policy_snapshot_sha256.hex()
            or ref.availability_snapshot != self.head.original_availability_snapshot_sha256.hex()
            or pipeline.pipeline_key != self.head.pipeline_key
            or pipeline.version != self.head.pipeline_version
            or pipeline.manifest_sha256 != self.head.pipeline_manifest_sha256.hex()
            or len(baseline.tracks) != self.head.source_snapshot_track_count
            or len(self.expected._template.p11_items) != self.head.p11_item_count
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_EXPECTED")
        origin = self.expected.origin
        if (
            type(origin) is not ConnectedP11InputOriginV3
            or origin.original_request_id != self.head.original_request_id
            or origin.capture_sha256 != self.head.capture_sha256.hex()
            or origin.consent_generation != self.head.consent_generation
            or origin.consent_receipt_sha256 != self.head.consent_receipt_sha256.hex()
            or self.expected.identity.owner != self.head.owner_user_id
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_ORIGIN")
        for value in (
            self.capture_document,
            self.original_request_document,
            self.original_ranking_document,
            self.original_profile_document,
        ):
            if type(value) is not bytes or not 1 <= len(value) <= MAX_CONNECTED_RECORD_BYTES:
                raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_BYTES")
        if type(self.facts) is not tuple or len(self.facts) > 10_000:
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
        if any(
            type(fact) is not NativeTemporalFact or type(fact.dimensions) is not tuple
            for fact in self.facts
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
        for fact in self.facts:
            _immutable_fact(fact, owner=self.head.owner_user_id)
        if len(self.facts) != self.head.source_event_count:
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
        for document, length in (
            (self.capture_document, self.head.capture_byte_length),
            (self.original_request_document, self.head.original_request_byte_length),
            (self.original_ranking_document, self.head.original_ranking_byte_length),
            (
                self.original_profile_document,
                self.head.original_profile_byte_length,
            ),
        ):
            if len(document) != length:
                raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_BYTES")
        if type(self.transaction_binding) is not tuple or len(self.transaction_binding) != 2:
            raise NativeConnectedV3Error("CONNECTED_V3_TRANSACTION")
        for transaction_part in self.transaction_binding:
            require_int_v3(transaction_part, 1, 2**64 - 1)


def _immutable_fact(fact: NativeTemporalFact, *, owner: UUID) -> None:
    """Reject mutable nested legacy values before retaining an independent source.

    This seals representation only. The owning normalizer still validates the
    accepted event, target, parent and time semantics under current SQL authority.
    """
    for name in ("event_id", "owner", "device", "journal_epoch", "recording_id"):
        require_uuid_v3(getattr(fact, name))
    if fact.owner != owner or len(fact.dimensions) > 32:
        raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
    for name in (
        "device_sequence",
        "server_sequence",
        "occurred_at_ms",
        "received_at_ms",
        "retained_until_ms",
    ):
        require_int_v3(getattr(fact, name), 0, MAX_SAFE_INTEGER)
    for dimension in fact.dimensions:
        if type(dimension) is not TemporalDimension or type(dimension.kind) is not DimensionKind:
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
        if not require_text_v3(dimension.key, maximum=800) or len(dimension.key) > 200:
            raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
    for name in ("event_type", "explicit_feedback"):
        require_text_v3(getattr(fact, name))
    for name in ("source_request_sha256", "normalized_payload_sha256"):
        require_hex_v3(getattr(fact, name))
    for value in (fact.event_origin, fact.feedback):
        if value is not None:
            require_text_v3(value)
    if fact.played_ms is not None:
        require_int_v3(fact.played_ms, 0, MAX_SAFE_INTEGER)
    if fact.completion_ratio is not None and (
        type(fact.completion_ratio) not in (int, float)
        or not isfinite(fact.completion_ratio)
        or not 0 <= fact.completion_ratio <= 1
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
    if type(fact.excluded_from_taste) is not bool:
        raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
    causal = fact.causality
    if causal is None:
        return
    if type(causal) is not NativeTemporalCausality:
        raise NativeConnectedV3Error("CONNECTED_V3_CONTEXT_FACTS")
    for name in ("association_id", "request_id", "presentation_id"):
        require_uuid_v3(getattr(causal, name))
    for optional_uuid in (causal.impression_association_id, causal.impression_event_id):
        if optional_uuid is not None:
            require_uuid_v3(optional_uuid)
    require_int_v3(causal.served_rank, 1, 100)
    for name in ("association_sha256", "decision_sha256", "served_item_sha256"):
        require_hex_v3(getattr(causal, name))
    for name in (
        "serving_provenance",
        "request_surface",
        "presentation_source",
        "presentation_surface",
    ):
        require_text_v3(getattr(causal, name))


@dataclass(frozen=True, slots=True)
class VerifiedConnectedRecordV3:
    """Completed comparisons within an owning transaction, never a transferable grant."""

    context: FreshNativeConnectedContextV3
    root: VerifiedNativeP11RootV3
    decision_sha256: bytes
    connection_seal_sha256: bytes
    record_ordinal: int

    def __post_init__(self) -> None:
        if type(self.context) is not FreshNativeConnectedContextV3:
            raise NativeConnectedV3Error("CONNECTED_V3_VERIFIED_CONTEXT")
        if type(self.root) is not VerifiedNativeP11RootV3:
            raise NativeConnectedV3Error("CONNECTED_V3_VERIFIED_ROOT")
        require_digest_v3(self.decision_sha256)
        require_digest_v3(self.connection_seal_sha256)
        require_int_v3(self.record_ordinal, 1, MAX_CONNECTED_RECORDS)
        head = self.context.head
        if (
            self.root.body.request_id != str(head.served_request_id)
            or self.root.root_sha256 != head.request_root_sha256.hex()
            or self.decision_sha256 != head.decision_sha256
            or self.connection_seal_sha256 != head.connection_seal_sha256
        ):
            raise NativeConnectedV3Error("CONNECTED_V3_VERIFIED_BINDING")

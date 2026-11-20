"""Closed source3 capture codec and values; never original-time SQL authority.

RFC8785 serialization is injected by the owning application. This domain module
uses only stdlib and existing pure R1 projection rules. Every operation uses the
caller's already admitted source meter; no ledger or allowance is created here.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum, IntEnum
from hashlib import sha256
from types import MappingProxyType, TracebackType
from typing import Any, Final, Protocol, cast
from uuid import UUID

from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    AdaptiveProfile,
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalSignal,
    TimeClassification,
    project_temporal_profile,
)
from autplay.domain.native_temporal import (
    NativeTemporalCausality,
    NativeTemporalFact,
    normalize_native_temporal_facts,
)

MAX_SOURCE3_BYTES: Final = 2_097_152
# Only original P11 baseline storage has a successor admission envelope. Both
# envelopes retain the exact RFC 8785 bytes bound by the original snapshot.
MAX_ORIGINAL_BASELINE_V4_BYTES: Final = 4_194_304
MAX_SOURCE3_EVENTS: Final = 10_000
MAX_SOURCE3_EVIDENCE: Final = 20_000
MAX_SOURCE3_CAUSAL_REQUESTS: Final = 64
MAX_SAFE53: Final = 9_007_199_254_740_991
SOURCE3_ELIGIBILITY_POLICY_SHA256: Final = (
    "683a44ed369682c9e2e08d10d932ba09a537a3d18992cdbb00f0418c0a93519c"
)
NATIVE_SOURCE3_ELIGIBILITY_POLICY_SHA256: Final = SOURCE3_ELIGIBILITY_POLICY_SHA256
NATIVE_SOURCE3_CODEC: Final = "NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1"
NATIVE_SOURCE3_CODEC_SHA256: Final = (
    "eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2"
)
_SOURCE_TYPES: Final = (
    "LISTENING_EVENT_RECORDED",
    "RECOMMENDATION_FEEDBACK_RECORDED",
    "RECOMMENDATION_IMPRESSION_RECORDED",
)
type Source3Canonicalizer = Callable[[object], bytes]


class NativeOriginalCaptureV3Error(ValueError):
    """A stable refusal code, containing no private input or decoder details."""


class NativeOriginalCaptureV3Meter(Protocol):
    """An existing G source lease, including its parent and record limits."""

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None:
        """Assert/reserve remaining existing allowance before the next work."""

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        """Consume actual work without creating/resetting any allowance."""


class NativeOriginalCaptureV3DigestMeter(NativeOriginalCaptureV3Meter, Protocol):
    """The same admitted source lease, including live decoded digest payloads."""

    def retain_digests(self, n: int) -> None:
        """Admit payload ownership before allocating or hydrating it."""

    def release_digests(self, n: int) -> None:
        """Release only payloads whose owned references have been dropped."""


class OriginalCaptureFormatVersion(IntEnum):
    FROZEN_V2 = 2
    SOURCE_V3 = 3


def _fail(code: str) -> None:
    raise NativeOriginalCaptureV3Error(code)


def _integer(value: object, lower: int = 0, upper: int = MAX_SAFE53) -> int:
    if type(value) is not int or not lower <= value <= upper:
        _fail("SOURCE3_INTEGER")
    return cast(int, value)


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        _fail("SOURCE3_UUID")
    try:
        parsed = UUID(cast(str, value))
    except ValueError, AttributeError:
        raise NativeOriginalCaptureV3Error("SOURCE3_UUID") from None
    if str(parsed) != value:
        _fail("SOURCE3_UUID")
    return parsed


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _fail("SOURCE3_DIGEST")
    return cast(str, value)


@dataclass(frozen=True, slots=True)
class NativeCaptureServerIdentityV3:
    server_instance_id: UUID
    identity_epoch: int

    def __post_init__(self) -> None:
        if type(self.server_instance_id) is not UUID:
            _fail("SOURCE3_UUID")
        _integer(self.identity_epoch, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.identity_epoch,
        }


@dataclass(frozen=True, slots=True)
class NativeCausalAuthorityV3:
    request_id: UUID
    serving_contract_version: int
    hash_format_version: int
    authority_document_sha256: str
    decision_sha256: str
    served_items_sha256: str
    pipeline_manifest_sha256: str

    def __post_init__(self) -> None:
        if type(self.request_id) is not UUID:
            _fail("SOURCE3_UUID")
        _integer(self.serving_contract_version, 2, 2)
        _integer(self.hash_format_version, 2, 3)
        for value in (
            self.authority_document_sha256,
            self.decision_sha256,
            self.served_items_sha256,
            self.pipeline_manifest_sha256,
        ):
            _digest(value)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "request_id": str(self.request_id)}


def _role(definition: str) -> dict[str, str]:
    return dict(field.split(":", 1) for field in definition.split())


_T: Final = "$.temporal_snapshot"
_F: Final = _T + ".facts[]"
_E: Final = _T + ".source_evidence[]"
_P: Final = _T + ".adaptive_profile"
_PD: Final = _P + ".dimensions[]"
_OBJECTS: Final = {
    "$": _role(
        "schema_version:v3 kind:capture original_capture_format_version:v3 quality_eligible:false "
        "admission_blocker:blocker recommendation_request_id:u owner_user_id:u request_sha256:d "
        "pipeline_manifest_sha256:d baseline_snapshot_id:u baseline_snapshot_sha256:d "
        "temporal_snapshot_id:u temporal_snapshot_sha256:d temporal_snapshot:object "
        "feature_policy_sha256:d cutoff_at_ms:q interaction_watermark:q "
        "native_interaction_watermark:q candidate_recording_ids:array "
        "candidate_membership_sha256:d "
        "p11_ranking_sha256:d source_snapshot_track_count:t candidate_universe_count:t "
        "ineligibility_reason:reason consent_receipt_sha256:d consent_generation:p"
    ),
    _T: _role(
        "schema_version:v3 kind:temporal temporal_snapshot_id:u owner_user_id:u "
        "baseline_snapshot_id:u baseline_snapshot_sha256:d server_identity:object "
        "artifact_authority:object cutoff_at_ms:q "
        "native_interaction_watermark:q source_scope:scope supported_source_types:array "
        "source_event_ids:array source_membership_sha256:d facts:array source_evidence:array "
        "adaptive_profile:object feature_policy_sha256:d expires_at:s"
    ),
    _T + ".server_identity": _role("server_instance_id:u identity_epoch:p"),
    _T + ".artifact_authority": _role(
        "kind:artifact pipeline_manifest_sha256:d required_artifacts:array causal_requests:array"
    ),
    _T + ".artifact_authority.causal_requests[]": _role(
        "request_id:u serving_contract_version:v2 hash_format_version:format "
        "authority_document_sha256:d decision_sha256:d served_items_sha256:d "
        "pipeline_manifest_sha256:d"
    ),
    _F: _role(
        "event_id:u owner:u device:u journal_epoch:u device_sequence:p server_sequence:p "
        "event_type:event recording_id:u dimensions:array occurred_at_ms:q received_at_ms:q "
        "source_request_sha256:d normalized_payload_sha256:d retained_until_ms:q "
        "event_origin:origin? played_ms:q? completion_ratio:unit? excluded_from_taste:b "
        "feedback:feedback? causality:object? explicit_feedback:explicit"
    ),
    _F + ".causality": _role(
        "association_id:u association_sha256:d request_id:u served_rank:rank decision_sha256:d "
        "served_item_sha256:d serving_provenance:p11 request_surface:surface presentation_id:u "
        "presentation_source:s presentation_surface:s impression_association_id:u? "
        "impression_event_id:u?"
    ),
    _E: _role(
        "schema_version:v2 evidence_id:u source_event_id:u owner_user_id:u server_profile_id:u "
        "device_id:u device_sequence:p server_sequence:p? source_event_type:event signal:signal "
        "derivation_key:s recording_id:u dimensions:array occurred_at_ms:q received_at_ms:q "
        "effective_at_ms:q time_classification:time origin_lane:lane signed_strength:signed "
        "quality_weight:unit excluded_from_taste:b source_request_sha256:d association_id:u?"
    ),
    _F + ".dimensions[]": _role("kind:dimension key:dimensionkey"),
    _E + ".dimensions[]": _role("kind:dimension key:dimensionkey"),
    _P: _role(
        "schema_version:v1 owner_user_id:u cutoff_at_ms:q interaction_watermark:q "
        "feature_policy:object "
        "maturity:object dimensions:array profile_sha256:d"
    ),
    _P + ".feature_policy": _role("key:s version:s content_sha256:d"),
    _P + ".maturity": _role(
        "score:unit effective_signal_mass:nonnegative track_coverage:q artist_coverage:q "
        "observation_span_ms:q recency_age_ms:q recency:unit consistency:unit organic_share:unit "
        "plasticity_multiplier:plasticity"
    ),
    _PD: _role(
        "kind:dimension key:dimensionkey long_term:object recent:object persistence:unit "
        "momentum:signed fatigue:unit recent_adjustment:signed source_event_ids:array"
    ),
    _PD + ".recent": _role("EPISODE:object H12:object H24:object D3:object D7:object"),
}
for _horizon_path in (
    _PD + ".long_term",
    *(_PD + ".recent." + h for h in ("EPISODE", "H12", "H24", "D3", "D7")),
):
    _OBJECTS[_horizon_path] = _role("score:signed confidence:unit evidence_mass:nonnegative")
_ARRAYS: Final = {
    "$.candidate_recording_ids": (5000, "u"),
    _T + ".supported_source_types": (3, "event"),
    _T + ".source_event_ids": (10_000, "u"),
    _T + ".facts": (10_000, "object"),
    _T + ".source_evidence": (20_000, "object"),
    _T + ".artifact_authority.required_artifacts": (0, "s"),
    _T + ".artifact_authority.causal_requests": (64, "object"),
    _F + ".dimensions": (32, "object"),
    _E + ".dimensions": (32, "object"),
    _P + ".dimensions": (512, "object"),
    _PD + ".source_event_ids": (64, "u"),
}
_LITERALS: Final[dict[str, object]] = {
    "v1": 1,
    "v2": 2,
    "v3": 3,
    "false": False,
    "capture": "SONA_CAPTURE_BUNDLE_V3",
    "temporal": "ORIGINAL_REQUEST_NATIVE_TEMPORAL_V3",
    "artifact": "P11_CPU_ONLY_FORMAT_BOUND_V3",
    "blocker": "NATIVE_SOURCE3_REQUIRES_INDEPENDENT_PRODUCER_ADMISSION",
    "scope": "ALL_VISIBLE_APPLIED_NORMALIZED_V2_AT_CUTOFF",
    "p11": "P11",
}
_ENUMS: Final = {
    "event": frozenset(_SOURCE_TYPES),
    "origin": frozenset(("ORGANIC", "PLAYLIST", "SEARCH", "WAVE", "RECOMMENDED")),
    "feedback": frozenset(("SELECTED", "DISMISSED")),
    "explicit": frozenset(("NONE", "LIKE", "DISLIKE")),
    "dimension": frozenset(value.value for value in DimensionKind),
    "signal": frozenset(value.value for value in TemporalSignal),
    "lane": frozenset(value.value for value in OriginLane),
    "time": frozenset(value.value for value in TimeClassification),
    "surface": frozenset(("recommendations", "home", "offline_pack")),
}
_FLOAT_RULES: Final = frozenset(("unit", "signed", "nonnegative", "plasticity"))
_ENUM_TYPES: Final = {
    "dimension": DimensionKind,
    "signal": TemporalSignal,
    "lane": OriginLane,
    "time": TimeClassification,
}
# These are schema paths, never a pattern applied to arbitrary input strings.
NATIVE_SOURCE3_FLOAT_PATHS_V3: Final[Mapping[str, tuple[str, bool]]] = MappingProxyType(
    {
        path + "." + key: (rule.removesuffix("?"), rule.endswith("?"))
        for path, fields in _OBJECTS.items()
        for key, rule in fields.items()
        if rule.removesuffix("?") in _FLOAT_RULES
    }
)


def _number(value: object, rule: str) -> float:
    if rule not in _FLOAT_RULES or type(value) not in (int, float):
        _fail("SOURCE3_NUMBER")
    if type(value) is int:
        _integer(value, -MAX_SAFE53)
    number = float(cast(int | float, value))
    if not math.isfinite(number):
        _fail("SOURCE3_NUMBER")
    minimum = -1.0 if rule == "signed" else 0.5 if rule == "plasticity" else 0.0
    maximum = 2.0 if rule == "plasticity" else math.inf if rule == "nonnegative" else 1.0
    if not minimum <= number <= maximum:
        _fail("SOURCE3_NUMBER")
    return number


def native_source3_float_to_bits_v3(
    value: object,
    *,
    meter: NativeOriginalCaptureV3Meter,
    rule: str = "unit",
    nullable: bool = False,
) -> str | None:
    """Encode one approved-path numeric value, preserving both zero signs."""
    meter.reserve(byte_work_bound=24, node_visit_bound=1)
    meter.charge(node_visits=1)
    if rule not in _FLOAT_RULES or type(nullable) is not bool:
        _fail("SOURCE3_NUMBER")
    if nullable and value is None:
        return None
    number = _number(value, rule)
    meter.charge(bytes_written=24)
    return struct.pack(">d", number).hex()


def native_source3_float_from_bits_v3(
    value: object,
    *,
    meter: NativeOriginalCaptureV3Meter,
    rule: str = "unit",
    nullable: bool = False,
) -> float | None:
    """Decode a bounded scalar; numeric JSON and nonfinite bit patterns refuse."""
    meter.reserve(byte_work_bound=32, node_visit_bound=1)
    meter.charge(node_visits=1)
    if rule not in _FLOAT_RULES or type(nullable) is not bool:
        _fail("SOURCE3_NUMBER")
    if nullable and value is None:
        return None
    if (
        type(value) is not str
        or len(value) != 16
        or any(c not in "0123456789abcdef" for c in value)
    ):
        _fail("SOURCE3_FLOAT_BITS")
    meter.charge(bytes_read=16, bytes_written=16)
    number = struct.unpack(">d", bytes.fromhex(cast(str, value)))[0]
    return _number(number, rule)


def _scalar(value: object, rule: str) -> None:
    if rule.endswith("?"):
        if value is None:
            return
        rule = rule[:-1]
    if rule in _LITERALS:
        expected = _LITERALS[rule]
        if type(value) is not type(expected) or value != expected:
            _fail("SOURCE3_LITERAL")
    elif rule in _ENUMS:
        if type(value) is not str or value not in _ENUMS[rule]:
            _fail("SOURCE3_ENUM")
    elif rule == "u":
        _uuid(value)
    elif rule == "d":
        _digest(value)
    elif rule in ("q", "p", "t", "rank", "format"):
        lower = 1 if rule in ("p", "rank") else 2 if rule == "format" else 0
        upper = (
            5000
            if rule == "t"
            else 100
            if rule == "rank"
            else 3
            if rule == "format"
            else MAX_SAFE53
        )
        _integer(value, lower, upper)
    elif rule == "b":
        if type(value) is not bool:
            _fail("SOURCE3_BOOLEAN")
    elif rule in ("s", "dimensionkey"):
        if type(value) is not str or (rule == "dimensionkey" and not 1 <= len(value) <= 200):
            _fail("SOURCE3_STRING")
    elif rule == "reason":
        if value is not None and (type(value) is not str or value != "UNIVERSE_OVER_CAP"):
            _fail("SOURCE3_REASON")
    elif rule in _FLOAT_RULES:
        _number(value, rule)
    else:
        _fail("SOURCE3_CONTAINER_PATH")


def _encode_float_tree(
    value: Any, path: str, rule: str, meter: NativeOriginalCaptureV3Meter, depth: int = 0
) -> Any:
    meter.charge(node_visits=1)
    if depth > 12:
        _fail("SOURCE3_DEPTH")
    if path in NATIVE_SOURCE3_FLOAT_PATHS_V3:
        float_rule, nullable = NATIVE_SOURCE3_FLOAT_PATHS_V3[path]
        return native_source3_float_to_bits_v3(
            value, meter=meter, rule=float_rule, nullable=nullable
        )
    if rule == "object?" and value is None:
        return None
    if rule in ("object", "object?"):
        if type(value) is not dict or value.keys() != _OBJECTS[path].keys():
            _fail("SOURCE3_KEYS")
        return {
            key: _encode_float_tree(value[key], path + "." + key, child_rule, meter, depth + 1)
            for key, child_rule in _OBJECTS[path].items()
        }
    if rule == "array":
        limit, child_rule = _ARRAYS[path]
        if type(value) is not list or len(value) > limit:
            _fail("SOURCE3_COUNT")
        return [
            _encode_float_tree(child, path + "[]", child_rule, meter, depth + 1) for child in value
        ]
    if rule in _ENUM_TYPES and type(value) is _ENUM_TYPES[rule]:
        value = value.value
    _scalar(value, rule)
    if type(value) is str:
        if len(value) > 4096:
            _fail("SOURCE3_STRING")
        meter.reserve(byte_work_bound=4 * len(value), node_visit_bound=0)
        size = len(value.encode("utf-8"))
        meter.charge(bytes_written=size)
        if size > 4096:
            _fail("SOURCE3_STRING")
    return value


def native_source3_encode_float_tree_v3(
    document: dict[str, Any], *, meter: NativeOriginalCaptureV3DigestMeter
) -> dict[str, Any]:
    """Copy closed capture containers and encode only the approved 31 float paths.

    Digest strings are borrowed from the caller's already retained construction
    tree. The caller must keep that ownership through use of the returned tree.
    """
    return cast(dict[str, Any], _encode_float_tree(document, "$", "object", meter))


def _decode_float_tree(value: Any, path: str, meter: NativeOriginalCaptureV3Meter) -> None:
    meter.charge(node_visits=1)
    if type(value) is dict:
        for key in value:
            meter.charge(node_visits=1)
            child_path = path + "." + key
            if child_path in NATIVE_SOURCE3_FLOAT_PATHS_V3:
                rule, nullable = NATIVE_SOURCE3_FLOAT_PATHS_V3[child_path]
                value[key] = native_source3_float_from_bits_v3(
                    value[key], meter=meter, rule=rule, nullable=nullable
                )
            elif type(value[key]) in (dict, list):
                _decode_float_tree(value[key], child_path, meter)
    elif type(value) is list:
        for child in value:
            meter.charge(node_visits=1)
            if type(child) in (dict, list):
                _decode_float_tree(child, path + "[]", meter)


def _clear_tree(value: Any) -> None:
    if type(value) is dict:
        for child in value.values():
            _clear_tree(child)
        value.clear()
    elif type(value) is list:
        for child in value:
            _clear_tree(child)
        value.clear()


@dataclass(frozen=True, slots=True)
class NativeOriginalCaptureV3Range:
    start: int
    stop: int

    @property
    def size(self) -> int:
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class NativeOriginalCaptureV3Layout:
    raw_bytes: int
    node_visits: int
    temporal_range: NativeOriginalCaptureV3Range
    candidates_range: NativeOriginalCaptureV3Range
    source_ids_range: NativeOriginalCaptureV3Range
    source_snapshot_track_count: int
    candidate_universe_count: int
    fact_count: int
    evidence_count: int
    dimension_count: int
    digest_payload_bytes: int
    evidence_range: NativeOriginalCaptureV3Range
    profile_range: NativeOriginalCaptureV3Range
    profile_digest_payload_bytes: int


class _Scan:
    def __init__(
        self, raw: bytes, meter: NativeOriginalCaptureV3Meter, canonicalize: Source3Canonicalizer
    ) -> None:
        self.raw, self.meter, self.canonicalize = raw, meter, canonicalize
        self.at = 0
        self.nodes = 0
        self.ranges: dict[str, NativeOriginalCaptureV3Range] = {}
        self.counts: dict[str, int] = {}
        self.scalars: dict[str, object] = {}
        self.ordered: dict[str, int] = {}
        self.digest_payload_bytes = 0
        self.profile_digest_payload_bytes = 0

    def node(self) -> None:
        self.meter.charge(node_visits=1)
        self.nodes += 1

    def peek(self) -> int:
        if self.at >= len(self.raw):
            _fail("SOURCE3_JSON")
        return self.raw[self.at]

    def take(self, byte: int) -> None:
        if self.peek() != byte:
            _fail("SOURCE3_JSON")
        self.at += 1

    def text(self) -> str:
        start = self.at
        self.take(34)
        while self.at < len(self.raw):
            byte = self.raw[self.at]
            self.at += 1
            if byte == 34:
                break
            if byte < 32:
                _fail("SOURCE3_STRING")
            if byte == 92:
                self.at += 1
            if self.at - start > 24_578:
                _fail("SOURCE3_STRING")
        else:
            _fail("SOURCE3_JSON")
        token = self.raw[start : self.at]
        try:
            value = json.loads(token)
            if type(value) is not str or len(value) > 4096:
                _fail("SOURCE3_STRING")
            encoded = value.encode("utf-8")
            if len(encoded) > 4096:
                _fail("SOURCE3_STRING")
            canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except ValueError, UnicodeError:
            raise NativeOriginalCaptureV3Error("SOURCE3_STRING") from None
        self.meter.charge(bytes_written=len(encoded) + len(canonical))
        if canonical != token:
            _fail("SOURCE3_CANONICAL")
        return cast(str, value)

    def value(self, path: str = "$", rule: str = "object", depth: int = 0) -> None:
        self.node()
        if depth > 12:
            _fail("SOURCE3_DEPTH")
        start = self.at
        digest_start = self.digest_payload_bytes
        byte = self.peek()
        if rule == "object?" and self.raw.startswith(b"null", self.at):
            self.at += 4
            return
        if rule in ("object", "object?"):
            if path not in _OBJECTS or byte != 123:
                _fail("SOURCE3_CONTAINER_PATH")
            self.object(path, depth)
        elif rule == "array":
            if path not in _ARRAYS or byte != 91:
                _fail("SOURCE3_CONTAINER_PATH")
            self.array(path, depth)
        else:
            if byte == 34:
                value: object = self.text()
            elif byte in (45, *range(48, 58)):
                while self.at < len(self.raw) and self.raw[self.at] in b"-+0123456789.eE":
                    self.at += 1
                    if self.at - start > 32:
                        _fail("SOURCE3_NUMBER")
                token = self.raw[start : self.at]
                try:
                    value = json.loads(token)
                    if type(value) is int:
                        _integer(value, -MAX_SAFE53)
                    elif type(value) is not float or not math.isfinite(value):
                        _fail("SOURCE3_NUMBER")
                    self.meter.charge(bytes_written=len(token))
                    if self.canonicalize(value) != token:
                        _fail("SOURCE3_CANONICAL")
                except ValueError, OverflowError:
                    raise NativeOriginalCaptureV3Error("SOURCE3_NUMBER") from None
            else:
                for literal, decoded in ((b"null", None), (b"true", True), (b"false", False)):
                    if self.raw.startswith(literal, self.at):
                        self.at += len(literal)
                        value = decoded
                        break
                else:
                    _fail("SOURCE3_JSON")
            if path in NATIVE_SOURCE3_FLOAT_PATHS_V3:
                float_rule, nullable = NATIVE_SOURCE3_FLOAT_PATHS_V3[path]
                native_source3_float_from_bits_v3(
                    value, meter=self.meter, rule=float_rule, nullable=nullable
                )
            else:
                _scalar(value, rule)
            if rule == "d":
                self.digest_payload_bytes += 64
            if path in ("$.source_snapshot_track_count", "$.candidate_universe_count"):
                self.scalars[path] = value
            if path in (
                "$.candidate_recording_ids[]",
                _T + ".source_event_ids[]",
                _T + ".artifact_authority.causal_requests[].request_id",
            ):
                order = _uuid(value).int
                if path in self.ordered and order <= self.ordered[path]:
                    _fail("SOURCE3_ORDER")
                self.ordered[path] = order
        if path in (
            _T,
            "$.candidate_recording_ids",
            _T + ".source_event_ids",
            _T + ".source_evidence",
            _P,
        ):
            self.ranges[path] = NativeOriginalCaptureV3Range(start, self.at)
        if path == _P:
            self.profile_digest_payload_bytes = self.digest_payload_bytes - digest_start

    def object(self, path: str, depth: int) -> None:
        self.take(123)
        schema = _OBJECTS[path]
        found: set[str] = set()
        previous = ""
        if self.peek() != 125:
            while True:
                self.node()
                key = self.text()
                if not key.isascii() or key <= previous or key not in schema:
                    _fail("SOURCE3_KEYS")
                previous = key
                found.add(key)
                self.take(58)
                self.value(path + "." + key, schema[key], depth + 1)
                if self.peek() != 44:
                    break
                self.at += 1
        self.take(125)
        if found != schema.keys():
            _fail("SOURCE3_KEYS")

    def array(self, path: str, depth: int) -> None:
        self.take(91)
        limit, rule = _ARRAYS[path]
        count = 0
        if self.peek() != 93:
            while True:
                self.node()
                count += 1
                if count > limit:
                    _fail("SOURCE3_COUNT")
                self.value(path + "[]", rule, depth + 1)
                if self.peek() != 44:
                    break
                self.at += 1
        self.take(93)
        self.counts[path] = count


def preflight_native_original_capture_v3(
    raw: bytes, *, meter: NativeOriginalCaptureV3DigestMeter, canonicalize: Source3Canonicalizer
) -> NativeOriginalCaptureV3Layout:
    """Bound and scan all closed source paths before hydrating an object tree."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE3_BYTES:
        _fail("SOURCE3_BYTES")
    # One current digest token, its UTF8/canonical forms and decoder scratch.
    meter.retain_digests(512)
    layout: NativeOriginalCaptureV3Layout | None = None
    error: Exception | None = None
    try:
        layout = _preflight(raw, meter=meter, canonicalize=canonicalize)
    except Exception as caught:
        caught.__traceback__ = None
        caught.__context__ = None
        caught.__cause__ = None
        error = caught
    finally:
        meter.release_digests(512)
    if error is not None:
        raise error
    return cast(NativeOriginalCaptureV3Layout, layout)


def _preflight(
    raw: bytes, *, meter: NativeOriginalCaptureV3Meter, canonicalize: Source3Canonicalizer
) -> NativeOriginalCaptureV3Layout:
    meter.reserve(byte_work_bound=4 * len(raw), node_visit_bound=4 * (len(raw) + 1))
    meter.charge(bytes_read=len(raw))
    scan = _Scan(raw, meter, canonicalize)
    scan.value()
    if scan.at != len(raw):
        _fail("SOURCE3_CANONICAL")
    tracks = cast(int, scan.scalars["$.source_snapshot_track_count"])
    candidates = cast(int, scan.scalars["$.candidate_universe_count"])
    facts = scan.counts[_T + ".facts"]
    evidence = scan.counts[_T + ".source_evidence"]
    dimensions = scan.counts[_P + ".dimensions"]
    if (
        candidates != scan.counts["$.candidate_recording_ids"]
        or candidates > tracks
        or facts != scan.counts[_T + ".source_event_ids"]
        or evidence > 2 * facts
    ):
        _fail("SOURCE3_COUNT")
    meter.reserve(
        byte_work_bound=32 * len(raw) + 65_536,
        node_visit_bound=16 * (len(raw) + 1)
        + 128 * (8 * facts + 8 * evidence + 32 * dimensions + 16),
    )
    return NativeOriginalCaptureV3Layout(
        len(raw),
        scan.nodes,
        scan.ranges[_T],
        scan.ranges["$.candidate_recording_ids"],
        scan.ranges[_T + ".source_event_ids"],
        tracks,
        candidates,
        facts,
        evidence,
        dimensions,
        scan.digest_payload_bytes,
        scan.ranges[_T + ".source_evidence"],
        scan.ranges[_P],
        scan.profile_digest_payload_bytes,
    )


def _json_value(value: Any) -> Any:
    if type(value) is UUID:
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(child) for child in value]
    return value


def _canonical(
    value: object,
    canonicalize: Source3Canonicalizer,
    meter: NativeOriginalCaptureV3Meter,
    *,
    maximum: int,
) -> bytes:
    # Only bounded scalars reach the injected formatter. Containers stream into
    # this bounded buffer; a rich derived projection cannot allocate an unbounded
    # complete RFC8785 result before its byte refusal.
    meter.reserve(byte_work_bound=2 * maximum, node_visit_bound=4 * (maximum + 1))
    output = bytearray()

    def write(chunk: bytes) -> None:
        if len(output) + len(chunk) > maximum:
            _fail("SOURCE3_BYTES")
        meter.charge(bytes_written=len(chunk))
        output.extend(chunk)

    def emit(item: Any, depth: int = 0) -> None:
        meter.charge(node_visits=1)
        if depth > 12:
            _fail("SOURCE3_DEPTH")
        if type(item) is dict:
            write(b"{")
            for index, key in enumerate(sorted(item)):
                meter.charge(node_visits=1)
                if index:
                    write(b",")
                emit(key, depth + 1)
                write(b":")
                emit(item[key], depth + 1)
            write(b"}")
        elif type(item) is list:
            write(b"[")
            for index, child in enumerate(item):
                meter.charge(node_visits=1)
                if index:
                    write(b",")
                emit(child, depth + 1)
            write(b"]")
        else:
            if isinstance(item, str):
                if len(item) > 4096:
                    _fail("SOURCE3_STRING")
                utf8_size = len(item.encode("utf-8"))
                meter.charge(bytes_written=utf8_size)
                if utf8_size > 4096:
                    _fail("SOURCE3_STRING")
                scalar_bound = 24_578
            elif item is None or type(item) in (bool, int, float):
                scalar_bound = 32
            else:
                _fail("SOURCE3_CANONICAL")
            meter.reserve(byte_work_bound=scalar_bound, node_visit_bound=1)
            encoded = canonicalize(item)
            if type(encoded) is not bytes or len(encoded) > scalar_bound:
                _fail("SOURCE3_CANONICAL")
            meter.charge(bytes_written=len(encoded))
            write(encoded)

    emit(value)
    meter.charge(bytes_written=len(output))
    return bytes(output)


def _hash(raw: bytes | memoryview, meter: NativeOriginalCaptureV3Meter) -> str:
    meter.charge(sha_input_bytes=len(raw), node_visits=1)
    return sha256(raw).hexdigest()


def _profile_document(
    profile: AdaptiveProfile,
    canonicalize: Source3Canonicalizer,
    meter: NativeOriginalCaptureV3Meter,
    maximum: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "owner_user_id": str(profile.owner_user_id),
        "cutoff_at_ms": profile.cutoff_at_ms,
        "interaction_watermark": profile.interaction_watermark,
        "feature_policy": {
            "key": profile.feature_policy_key,
            "version": profile.feature_policy_version,
            "content_sha256": profile.feature_policy_sha256,
        },
        "maturity": asdict(profile.maturity),
        "dimensions": [
            {
                "kind": row.dimension.kind.value,
                "key": row.dimension.key,
                "long_term": asdict(row.long_term),
                "recent": {horizon.value: asdict(value) for horizon, value in row.recent},
                "persistence": row.persistence,
                "momentum": row.momentum,
                "fatigue": row.fatigue,
                "recent_adjustment": row.recent_adjustment,
                "source_event_ids": [str(value) for value in row.source_event_ids],
            }
            for row in profile.dimensions
        ],
    }
    encoded = _canonical(result, canonicalize, meter, maximum=maximum)
    result["profile_sha256"] = _hash(encoded, meter)
    return result


def native_source3_profile_document(
    profile: AdaptiveProfile,
    *,
    canonicalize: Source3Canonicalizer,
    meter: NativeOriginalCaptureV3Meter,
    max_bytes: int = MAX_SOURCE3_BYTES,
) -> dict[str, Any]:
    """Serialize/hash the unchanged R1 profile under an existing source lease."""
    _integer(max_bytes, 1, MAX_SOURCE3_BYTES)
    if type(profile) is not AdaptiveProfile or len(profile.dimensions) > 512:
        _fail("SOURCE3_PROFILE")
    meter.reserve(byte_work_bound=0, node_visit_bound=1 + len(profile.dimensions))
    meter.charge(node_visits=1 + len(profile.dimensions))
    references = 0
    for row in profile.dimensions:
        if len(row.source_event_ids) > 64:
            _fail("SOURCE3_COUNT")
        if not 1 <= len(row.dimension.key) <= 200:
            _fail("SOURCE3_STRING")
        references += len(row.source_event_ids)
        meter.reserve(byte_work_bound=0, node_visit_bound=len(row.source_event_ids))
        meter.charge(node_visits=len(row.source_event_ids))
    # Fixed policy/header/maturity fields fit 4096 bytes. A dimension's fixed
    # field names, bounded key and numeric horizons fit 8192; UUID references
    # cost at most 39 bytes each. This avoids a fictitious 2-MiB profile on a
    # small already narrowed recheck lease while retaining the fragment cap.
    upper = min(max_bytes, 4096 + 8192 * len(profile.dimensions) + 39 * references)
    meter.reserve(byte_work_bound=4 * upper, node_visit_bound=4 * (upper + 1))
    return _profile_document(profile, canonicalize, meter, upper)


@dataclass(frozen=True, slots=True)
class _Boundary:
    owner: UUID
    event_ids: tuple[UUID, ...]
    cutoff_at_ms: int
    interaction_watermark: int


def _validate_semantics(
    document: dict[str, Any],
    raw: bytes,
    layout: NativeOriginalCaptureV3Layout,
    canonicalize: Source3Canonicalizer,
    meter: NativeOriginalCaptureV3Meter,
) -> None:
    temporal = document["temporal_snapshot"]
    profile = temporal["adaptive_profile"]
    owner = _uuid(document["owner_user_id"])
    for key in (
        "temporal_snapshot_id",
        "owner_user_id",
        "baseline_snapshot_id",
        "baseline_snapshot_sha256",
        "feature_policy_sha256",
        "cutoff_at_ms",
        "native_interaction_watermark",
    ):
        if temporal[key] != document[key]:
            _fail("SOURCE3_BINDING")
    if (
        temporal["artifact_authority"]["pipeline_manifest_sha256"]
        != document["pipeline_manifest_sha256"]
        or temporal["supported_source_types"] != list(_SOURCE_TYPES)
        or document["feature_policy_sha256"] != DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256
        or profile["owner_user_id"] != document["owner_user_id"]
        or profile["cutoff_at_ms"] != document["cutoff_at_ms"]
        or profile["interaction_watermark"] != document["native_interaction_watermark"]
        or document["ineligibility_reason"]
        != ("UNIVERSE_OVER_CAP" if layout.candidate_universe_count > 1024 else None)
    ):
        _fail("SOURCE3_BINDING")
    for name, byte_range in (
        ("temporal_snapshot_sha256", layout.temporal_range),
        ("candidate_membership_sha256", layout.candidates_range),
    ):
        if _hash(memoryview(raw)[byte_range.start : byte_range.stop], meter) != document[name]:
            _fail("SOURCE3_HASH")
    source_range = layout.source_ids_range
    if (
        _hash(memoryview(raw)[source_range.start : source_range.stop], meter)
        != temporal["source_membership_sha256"]
    ):
        _fail("SOURCE3_HASH")
    try:
        expires = datetime.fromisoformat(temporal["expires_at"])
        utc_offset = expires.utcoffset()
        if (
            utc_offset is None
            or utc_offset.total_seconds() != 0
            or expires.isoformat() != temporal["expires_at"]
        ):
            _fail("SOURCE3_EXPIRY")
        elapsed = expires - datetime(1970, 1, 1, tzinfo=UTC)
        expires_ms = (
            elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
        )
        if expires_ms != document["cutoff_at_ms"] + 15_552_000_000:
            _fail("SOURCE3_EXPIRY")
    except ValueError, OverflowError:
        raise NativeOriginalCaptureV3Error("SOURCE3_EXPIRY") from None
    facts: list[NativeTemporalFact] = []
    associations: dict[UUID, str | None] = {}
    causal_ids: set[UUID] = set()
    for index, value in enumerate(temporal["facts"]):
        meter.charge(node_visits=1 + len(value["dimensions"]))
        event_id = _uuid(value["event_id"])
        if value["event_id"] != temporal["source_event_ids"][index] or value["owner"] != str(owner):
            _fail("SOURCE3_MEMBERSHIP")
        dimensions = tuple(
            TemporalDimension(DimensionKind(row["kind"]), row["key"]) for row in value["dimensions"]
        )
        if not dimensions or len(set(dimensions)) != len(dimensions):
            _fail("SOURCE3_DIMENSIONS")
        causal = value["causality"]
        if causal is not None:
            causal_ids.add(_uuid(causal["request_id"]))
            causal_values: dict[str, Any] = {
                key: _uuid(item) if key.endswith("_id") and item is not None else item
                for key, item in causal.items()
            }
            causal = NativeTemporalCausality(**causal_values)
        associations[event_id] = str(causal.association_id) if causal else None
        facts.append(
            NativeTemporalFact(
                **{
                    **value,
                    "event_id": event_id,
                    "owner": owner,
                    "device": _uuid(value["device"]),
                    "journal_epoch": _uuid(value["journal_epoch"]),
                    "recording_id": _uuid(value["recording_id"]),
                    "dimensions": dimensions,
                    "causality": causal,
                }
            )
        )
    authorities = temporal["artifact_authority"]["causal_requests"]
    if {_uuid(row["request_id"]) for row in authorities} != causal_ids or _uuid(
        document["recommendation_request_id"]
    ) in causal_ids:
        _fail("SOURCE3_CAUSAL_CLOSURE")
    authority_by_id = {_uuid(row["request_id"]): row for row in authorities}
    for fact in facts:
        if fact.causality is not None and (
            fact.causality.decision_sha256
            != authority_by_id[fact.causality.request_id]["decision_sha256"]
        ):
            _fail("SOURCE3_CAUSAL_BINDING")
    boundary = _Boundary(
        owner,
        tuple(_uuid(value) for value in temporal["source_event_ids"]),
        document["cutoff_at_ms"],
        document["native_interaction_watermark"],
    )
    # The unchanged normalizer creates at most two UUIDv5 identities per fact.
    # Include its bounded name encodings/SHA reads before invoking it, even if
    # the hostile serialized evidence array understates the derived count.
    normalization_bytes = 2_176 * layout.fact_count
    # Its support comprehension also probes the complete event-ID tuple for
    # each fact, in the validated exact order, before testing causality.
    normalization_visits = (
        128 * (layout.fact_count + 1) + layout.fact_count * (layout.fact_count + 1) // 2
    )
    meter.reserve(byte_work_bound=normalization_bytes, node_visit_bound=normalization_visits)
    meter.charge(
        bytes_written=normalization_bytes // 2,
        sha_input_bytes=normalization_bytes // 2,
        node_visits=normalization_visits,
    )
    try:
        evidence = normalize_native_temporal_facts(tuple(facts), boundary)
        if len(evidence) != layout.evidence_count:
            _fail("SOURCE3_EVIDENCE")
        dimension_counts: dict[TemporalDimension, int] = {}
        for item in evidence:
            if not item.excluded_from_taste:
                for dimension in item.dimensions:
                    dimension_counts[dimension] = dimension_counts.get(dimension, 0) + 1
        # R1 checks membership in a growing reference list before keeping its
        # final 64 references. Charge those comparisons; do not silently treat
        # the unchanged projector as linear or truncate its input to repair it.
        reference_visits = sum(count * (count - 1) // 2 for count in dimension_counts.values())
        projection_visits = (
            128
            * (8 * layout.fact_count + 8 * layout.evidence_count + 32 * layout.dimension_count + 16)
            + reference_visits
        )
        meter.reserve(byte_work_bound=0, node_visit_bound=projection_visits)
        meter.charge(node_visits=projection_visits)
        projected = project_temporal_profile(
            owner_user_id=owner,
            cutoff_at_ms=boundary.cutoff_at_ms,
            interaction_watermark=boundary.interaction_watermark,
            evidence=evidence,
        )
    except ValueError:
        raise NativeOriginalCaptureV3Error("SOURCE3_PROJECTION") from None
    expected_evidence = [
        {
            "schema_version": 2,
            **_json_value(
                {
                    key: value
                    for key, value in asdict(row).items()
                    if key
                    not in {
                        "recommendation_request_id",
                        "impression_event_id",
                        "recommendation_source_rank",
                    }
                }
            ),
            "association_id": associations[row.source_event_id],
        }
        for row in evidence
    ]
    expected_evidence = _encode_float_tree(
        expected_evidence, _T + ".source_evidence", "array", meter
    )
    evidence_range = layout.evidence_range
    if (
        _canonical(expected_evidence, canonicalize, meter, maximum=len(raw))
        != memoryview(raw)[evidence_range.start : evidence_range.stop]
    ):
        _fail("SOURCE3_EVIDENCE")
    expected_profile = _profile_document(projected, canonicalize, meter, len(raw))
    expected_profile = _encode_float_tree(expected_profile, _P, "object", meter)
    profile_range = layout.profile_range
    if (
        _canonical(expected_profile, canonicalize, meter, maximum=len(raw))
        != memoryview(raw)[profile_range.start : profile_range.stop]
    ):
        _fail("SOURCE3_PROFILE")


_CARRIER_DIGESTS: Final = (
    "baseline_snapshot_sha256",
    "temporal_snapshot_sha256",
    "candidate_membership_sha256",
    "p11_ranking_sha256",
    "bundle_sha256",
    "consent_receipt_sha256",
)


@dataclass(slots=True)
class _DigestOwnership:
    meter: NativeOriginalCaptureV3DigestMeter
    values: tuple[str, ...] | None

    def get(self, index: int) -> str:
        if self.values is None:
            _fail("SOURCE3_CLOSED")
        return cast(tuple[str, ...], self.values)[index]

    def close(self) -> None:
        if self.values is not None:
            self.values = None
            self.meter.release_digests(384)


@dataclass(frozen=True, slots=True, init=False)
class SonaCaptureBundleV3:
    """Exact validated source bytes; this value grants no consent/SQL authority."""

    recommendation_request_id: UUID
    owner_user_id: UUID
    _digests: _DigestOwnership
    consent_generation: int
    cutoff_at_ms: int
    interaction_watermark: int
    native_interaction_watermark: int
    source_snapshot_track_count: int
    candidate_universe_count: int
    ineligibility_reason: str | None
    document: bytes
    layout: NativeOriginalCaptureV3Layout

    def __new__(cls) -> SonaCaptureBundleV3:
        raise NativeOriginalCaptureV3Error("SOURCE3_FACTORY")

    @property
    def baseline_snapshot_sha256(self) -> str:
        return self._digests.get(0)

    @property
    def temporal_snapshot_sha256(self) -> str:
        return self._digests.get(1)

    @property
    def candidate_membership_sha256(self) -> str:
        return self._digests.get(2)

    @property
    def p11_ranking_sha256(self) -> str:
        return self._digests.get(3)

    @property
    def bundle_sha256(self) -> str:
        return self._digests.get(4)

    @property
    def consent_receipt_sha256(self) -> str:
        return self._digests.get(5)

    def close(self) -> None:
        """Drop the six owned digest payloads and release their original lease."""
        self._digests.close()

    def __enter__(self) -> SonaCaptureBundleV3:
        self._digests.get(0)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        *,
        meter: NativeOriginalCaptureV3DigestMeter,
        canonicalize: Source3Canonicalizer,
    ) -> SonaCaptureBundleV3:
        layout = preflight_native_original_capture_v3(raw, meter=meter, canonicalize=canonicalize)
        # P counts every serialized digest occurrence. Scratch covers simultaneous
        # SHA digest/hex temporaries, the computed profile hash and final bundle
        # hash; typed replay/asdict/encoded copies borrow existing digest strings.
        retained = layout.digest_payload_bytes + 512
        meter.retain_digests(retained)
        document: dict[str, Any] = {}
        transferred = False
        error: Exception | None = None
        bundle: SonaCaptureBundleV3 | None = None
        try:
            meter.charge(bytes_read=len(raw), node_visits=layout.node_visits)
            document = _hydrate_source3(raw)
            if _canonical(document, canonicalize, meter, maximum=len(raw)) != raw:
                _fail("SOURCE3_CANONICAL")
            _decode_float_tree(document, "$", meter)
            _validate_semantics(document, raw, layout, canonicalize, meter)
            bundle = object.__new__(cls)
            for name in cls.__dataclass_fields__:
                if name == "_digests":
                    continue
                if name == "document":
                    value: object = raw
                elif name == "layout":
                    value = layout
                elif name in ("recommendation_request_id", "owner_user_id"):
                    value = _uuid(document[name])
                else:
                    value = document[name]
                object.__setattr__(bundle, name, value)
            owned = tuple(
                _hash(raw, meter) if name == "bundle_sha256" else document[name]
                for name in _CARRIER_DIGESTS
            )
            object.__setattr__(bundle, "_digests", _DigestOwnership(meter, owned))
            transferred = True
        except Exception as caught:
            # Refusal tracebacks must not keep the replay's temporary digest
            # structures alive after their logical ownership has been released.
            caught.__traceback__ = None
            caught.__context__ = None
            caught.__cause__ = None
            error = caught
        finally:
            _clear_tree(document)
            meter.release_digests(retained - (384 if transferred else 0))
        if error is not None:
            raise error
        return cast(SonaCaptureBundleV3, bundle)

    @contextmanager
    def projection(self, *, meter: NativeOriginalCaptureV3DigestMeter) -> Iterator[dict[str, Any]]:
        """Scope raw bits projection ownership; nested containers clear on exit.

        Consumers must not export digest scalar aliases beyond the context.
        """
        self._digests.get(0)
        retained = self.layout.digest_payload_bytes
        meter.retain_digests(retained)
        document: dict[str, Any] = {}
        try:
            meter.reserve(
                byte_work_bound=len(self.document), node_visit_bound=self.layout.node_visits
            )
            meter.charge(bytes_read=len(self.document), node_visits=self.layout.node_visits)
            document = _hydrate_source3(self.document)
            yield document
        finally:
            _clear_tree(document)
            meter.release_digests(retained)

    @contextmanager
    def numeric_projection(
        self, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> Iterator[dict[str, Any]]:
        """Scope a projection decoded only at the closed 31 numeric paths."""
        with self.projection(meter=meter) as document:
            _decode_float_tree(document, "$", meter)
            yield document

    @contextmanager
    def verified_numeric_profile(
        self, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> Iterator[dict[str, Any]]:
        """Scope only the numeric R1 profile already verified against its facts."""
        self._digests.get(0)
        byte_range = self.layout.profile_range
        retained = self.layout.profile_digest_payload_bytes
        meter.retain_digests(retained)
        profile: dict[str, Any] = {}
        try:
            meter.reserve(
                byte_work_bound=3 * byte_range.size, node_visit_bound=self.layout.node_visits
            )
            meter.charge(
                bytes_read=2 * byte_range.size,
                bytes_written=byte_range.size,
                node_visits=self.layout.node_visits,
            )
            profile = _hydrate_source3(self.document[byte_range.start : byte_range.stop])
            _decode_float_tree(profile, _P, meter)
            yield profile
        finally:
            _clear_tree(profile)
            meter.release_digests(retained)


def _hydrate_source3(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(raw))

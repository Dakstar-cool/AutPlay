"""Separate original source3 assembly from actual, unchanged P11/R1 values.

The caller supplies transaction-owned facts, consent and prior authority. This
creator preserves their normalized projection; it establishes no SQL authority,
connected-record admission, model score coverage or permission to run Sona.
"""

from __future__ import annotations

from collections.abc import Buffer
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from hashlib import sha256
from io import BytesIO
from typing import Any, cast
from uuid import UUID

import rfc8785

from autplay.application.native_capture import NativeCaptureBoundary
from autplay.application.recommendations import (
    baseline_pipeline_definition,
    recommendation_input_snapshot_document,
    request_document,
)
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.application.training_consent import TrainingCaptureGrant
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    MAX_SOURCE_EVENTS,
    AdaptiveProfile,
    TemporalDimension,
    TemporalProjectionError,
    project_temporal_profile,
)
from autplay.domain.native_original_capture_v3 import (
    MAX_ORIGINAL_BASELINE_V4_BYTES,
    NativeCaptureServerIdentityV3,
    NativeCausalAuthorityV3,
    NativeOriginalCaptureV3DigestMeter,
    NativeOriginalCaptureV3Meter,
    SonaCaptureBundleV3,
    native_source3_encode_float_tree_v3,
    native_source3_profile_document,
)
from autplay.domain.native_profile_binary64_v3 import native_profile_binary64_digest_v3
from autplay.domain.native_request_roots_v3 import NativeP11CandidateAuthorityV3
from autplay.domain.native_temporal import NativeTemporalFact, normalize_native_temporal_facts
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationResponse,
    RecommendationSurface,
    SnapshotTrack,
)

_SAFE53 = 2**53 - 1
_MAX_BYTES = 2_097_152
_SOURCE_TYPES = (
    "LISTENING_EVENT_RECORDED",
    "RECOMMENDATION_FEEDBACK_RECORDED",
    "RECOMMENDATION_IMPRESSION_RECORDED",
)


@dataclass(frozen=True, slots=True)
class NativeOriginalProfilePayloadV3:
    """Original numeric R1 bytes created before the source3 bits projection."""

    document: bytes
    document_sha256: str
    profile_sha256: str
    profile_binary64_sha256: str
    owner_user_id: UUID
    cutoff_at_ms: int
    native_interaction_watermark: int
    feature_policy_sha256: str
    source_membership_sha256: str
    _meter: NativeOriginalCaptureV3DigestMeter = field(repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.document) is not bytes
            or not 1 <= len(self.document) <= _MAX_BYTES
            or type(self.owner_user_id) is not UUID
            or not _q53(self.cutoff_at_ms)
            or not _q53(self.native_interaction_watermark)
            or any(
                not _sha(value)
                for value in (
                    self.document_sha256,
                    self.profile_sha256,
                    self.profile_binary64_sha256,
                    self.feature_policy_sha256,
                    self.source_membership_sha256,
                )
            )
        ):
            raise ValueError("NATIVE_SOURCE3_ORIGINAL_PROFILE")

    def assert_open(self) -> None:
        if self._closed:
            raise ValueError("NATIVE_SOURCE3_ORIGINAL_PROFILE_CLOSED")

    def close(self) -> None:
        if not self._closed:
            object.__setattr__(self, "document_sha256", "")
            object.__setattr__(self, "profile_sha256", "")
            object.__setattr__(self, "profile_binary64_sha256", "")
            object.__setattr__(self, "source_membership_sha256", "")
            self._meter.release_digests(256)
            object.__setattr__(self, "_closed", True)

    def __enter__(self) -> NativeOriginalProfilePayloadV3:
        self.assert_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class PreparedOriginalNativeCaptureV3:
    capture: SonaCaptureBundleV3
    original_profile: NativeOriginalProfilePayloadV3

    def close(self) -> None:
        self.capture.close()
        self.original_profile.close()

    def __enter__(self) -> PreparedOriginalNativeCaptureV3:
        self.original_profile.assert_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class _AssemblyDigests:
    """Own newly computed hexadecimal payloads through assembly or transfer."""

    def __init__(self, meter: NativeOriginalCaptureV3DigestMeter) -> None:
        self.meter = meter
        self.owned = 0

    def retain(self) -> None:
        self.meter.retain_digests(64)
        self.owned += 64

    def digest(self, value: object, *, max_bytes: int = _MAX_BYTES) -> str:
        self.retain()
        return _hash(value, self.meter, max_bytes=max_bytes)

    def release(self) -> None:
        self.meter.release_digests(self.owned)
        self.owned = 0


def _sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _profile_binary64_hex(
    profile: AdaptiveProfile,
    source_membership_sha256: str,
    original_profile_sha256: str,
    meter: NativeOriginalCaptureV3DigestMeter,
    digests: _AssemblyDigests,
) -> str:
    """Own raw argument temporaries and transfer the independent hex result."""
    meter.retain_digests(64)
    source_raw: bytes | None = None
    old_raw: bytes | None = None
    try:
        meter.reserve(byte_work_bound=192, node_visit_bound=2)
        meter.charge(bytes_read=128, bytes_written=64, node_visits=2)
        source_raw = bytes.fromhex(source_membership_sha256)
        old_raw = bytes.fromhex(original_profile_sha256)
        with native_profile_binary64_digest_v3(
            profile,
            source_membership_sha256=source_raw,
            original_profile_sha256=old_raw,
            meter=meter,
        ) as raw:
            digests.retain()
            meter.reserve(byte_work_bound=64, node_visit_bound=1)
            meter.charge(bytes_written=64, node_visits=1)
            result = raw.hex()
            del raw
        return result
    finally:
        source_raw = old_raw = None
        meter.release_digests(64)


def _q53(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and int(positive) <= value <= _SAFE53


def _canonical(value: object) -> bytes:
    """Use the original source codec, independently from root integer wire."""

    return rfc8785.dumps(cast(Any, value))


class _CanonicalBuffer(BytesIO):
    """Charge each bounded RFC8785 write before retaining its bytes."""

    def __init__(self, meter: NativeOriginalCaptureV3Meter, maximum: int) -> None:
        super().__init__()
        self._meter = meter
        self._maximum = maximum

    def write(self, value: Buffer) -> int:
        size = len(memoryview(value))
        if self.tell() + size > self._maximum:
            raise ValueError("NATIVE_SOURCE3_DOCUMENT_BOUND")
        self._meter.reserve(byte_work_bound=size, node_visit_bound=1)
        self._meter.charge(bytes_written=size, node_visits=1)
        return super().write(value)


def _encode(
    value: object, meter: NativeOriginalCaptureV3Meter, *, max_bytes: int = _MAX_BYTES
) -> bytes:
    def bounded(child: object, depth: int = 0) -> None:
        meter.reserve(byte_work_bound=0, node_visit_bound=1)
        meter.charge(node_visits=1)
        if depth > 12:
            raise ValueError("NATIVE_SOURCE3_DEPTH")
        if isinstance(child, str):
            if len(child) > 4096:
                raise ValueError("NATIVE_SOURCE3_STRING_BOUND")
            meter.reserve(byte_work_bound=4 * len(child), node_visit_bound=0)
            size = len(child.encode("utf-8"))
            meter.charge(bytes_written=size)
            if size > 4096:
                raise ValueError("NATIVE_SOURCE3_STRING_BOUND")
        elif isinstance(child, dict):
            for key, item in child.items():
                bounded(key, depth + 1)
                bounded(item, depth + 1)
        elif isinstance(child, tuple | list):
            for item in child:
                bounded(item, depth + 1)

    bounded(value)
    with _CanonicalBuffer(meter, max_bytes) as target:
        rfc8785.dump(cast(Any, value), target)
        meter.reserve(byte_work_bound=target.tell(), node_visit_bound=1)
        meter.charge(bytes_written=target.tell(), node_visits=1)
        return target.getvalue()


def _hash(
    value: object, meter: NativeOriginalCaptureV3Meter, *, max_bytes: int = _MAX_BYTES
) -> str:
    raw = _encode(value, meter, max_bytes=max_bytes)
    meter.reserve(byte_work_bound=len(raw), node_visit_bound=1)
    meter.charge(sha_input_bytes=len(raw), node_visits=1)
    return sha256(raw).hexdigest()


def _source_json(value: Any, meter: NativeOriginalCaptureV3Meter) -> Any:
    """Preserve RFC scalar values while removing typed str-enum wrappers."""

    meter.reserve(byte_work_bound=0, node_visit_bound=1)
    meter.charge(node_visits=1)
    if isinstance(value, UUID):
        meter.reserve(byte_work_bound=36, node_visit_bound=0)
        meter.charge(bytes_written=36)
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if type(value) is dict:
        return {key: _source_json(child, meter) for key, child in value.items()}
    if type(value) in (list, tuple):
        return [_source_json(child, meter) for child in value]
    return value


def _prior_authorities(
    facts: tuple[NativeTemporalFact, ...],
    authorities: tuple[NativeCausalAuthorityV3, ...],
    pipeline_sha256: str,
    original_request_id: UUID,
) -> list[dict[str, Any]]:
    """Check the bounded typed closure before any capture payload is assembled."""

    if type(authorities) is not tuple or len(authorities) > 64:
        raise ValueError("NATIVE_SOURCE3_CAUSAL_AUTHORITY_BOUND")
    if any(type(authority) is not NativeCausalAuthorityV3 for authority in authorities):
        raise ValueError("NATIVE_SOURCE3_CAUSAL_AUTHORITY_TYPE")
    requests = tuple(authority.request_id for authority in authorities)
    if requests != tuple(sorted(set(requests), key=lambda value: value.bytes)):
        raise ValueError("NATIVE_SOURCE3_CAUSAL_AUTHORITY_ORDER")
    actual = {fact.causality.request_id for fact in facts if fact.causality is not None}
    if len(actual) > 64 or set(requests) != actual or original_request_id in actual:
        raise ValueError("NATIVE_SOURCE3_CAUSAL_AUTHORITY_MEMBERSHIP")
    by_request = {authority.request_id: authority for authority in authorities}
    for fact in facts:
        causal = fact.causality
        if causal is not None and (
            causal.serving_provenance != "P11"
            or by_request[causal.request_id].pipeline_manifest_sha256 != pipeline_sha256
            or by_request[causal.request_id].decision_sha256 != causal.decision_sha256
        ):
            raise ValueError("NATIVE_SOURCE3_CAUSAL_AUTHORITY_BINDING")
    return [authority.to_dict() for authority in authorities]


def _candidates(
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    authority: NativeP11CandidateAuthorityV3,
) -> list[str]:
    """Consume the actual complete mandatory filter without another filter."""

    tracks = authority.filter_snapshot_tracks(response.request.query, baseline)
    by_id = {track.recording_id: track for track in baseline.tracks}
    if (
        type(tracks) is not tuple
        or len(tracks) > len(baseline.tracks)
        or any(type(track) is not SnapshotTrack for track in tracks)
        or len({track.recording_id for track in tracks}) != len(tracks)
        or any(by_id.get(track.recording_id) != track for track in tracks)
    ):
        raise ValueError("NATIVE_SOURCE3_CANDIDATE_BINDING")
    candidate_ids = {track.recording_id for track in tracks}
    items = response.items
    if (
        type(items) is not tuple
        or len(items) > response.request.query.limit
        or len({item.recording_id for item in items}) != len(items)
        or any(
            type(item.source_rank) is not int
            or item.source_rank != ordinal
            or item.recording_id not in candidate_ids
            or item.artist_key != by_id[item.recording_id].artist_key
            or item.release_key != by_id[item.recording_id].release_key
            for ordinal, item in enumerate(items, 1)
        )
    ):
        raise ValueError("NATIVE_SOURCE3_P11_ITEM_BINDING")
    return [str(value) for value in sorted(candidate_ids, key=lambda value: value.bytes)]


def prepare_original_native_capture_v3(
    *,
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    grant: TrainingCaptureGrant,
    boundary: NativeCaptureBoundary,
    facts: tuple[NativeTemporalFact, ...],
    temporal_id: UUID,
    server_identity: NativeCaptureServerIdentityV3,
    causal_authority: tuple[NativeCausalAuthorityV3, ...],
    candidate_authority: NativeP11CandidateAuthorityV3,
    meter: NativeOriginalCaptureV3DigestMeter,
) -> PreparedOriginalNativeCaptureV3:
    """Create both independent original numeric profile and source3 bits bytes."""

    digests = _AssemblyDigests(meter)
    error: Exception | None = None
    result: PreparedOriginalNativeCaptureV3 | None = None
    try:
        result = _prepare_original_native_capture_v3(
            baseline=baseline,
            response=response,
            grant=grant,
            boundary=boundary,
            facts=facts,
            temporal_id=temporal_id,
            server_identity=server_identity,
            causal_authority=causal_authority,
            candidate_authority=candidate_authority,
            meter=meter,
            digests=digests,
        )
        # Profile SHA, independent binary64 SHA, original document SHA and
        # source membership SHA transfer
        # to the independently retained profile payload. The decoder separately
        # owns the returned capture's parsed digests on this same meter.
        digests.owned -= 256
    except Exception as caught:
        caught.__traceback__ = None
        caught.__context__ = None
        caught.__cause__ = None
        error = caught
    finally:
        digests.release()
    if error is not None:
        raise error
    assert result is not None
    return result


def _prepare_original_native_capture_v3(
    *,
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    grant: TrainingCaptureGrant,
    boundary: NativeCaptureBoundary,
    facts: tuple[NativeTemporalFact, ...],
    temporal_id: UUID,
    server_identity: NativeCaptureServerIdentityV3,
    causal_authority: tuple[NativeCausalAuthorityV3, ...],
    candidate_authority: NativeP11CandidateAuthorityV3,
    meter: NativeOriginalCaptureV3DigestMeter,
    digests: _AssemblyDigests,
) -> PreparedOriginalNativeCaptureV3:
    """Preserve source3 independently of Sona and connected-record admission."""

    if (
        type(facts) is not tuple
        or len(facts) > MAX_SOURCE_EVENTS
        or any(
            type(fact) is not NativeTemporalFact
            or type(fact.dimensions) is not tuple
            or not 1 <= len(fact.dimensions) <= 32
            or any(type(dimension) is not TemporalDimension for dimension in fact.dimensions)
            or len(set(fact.dimensions)) != len(fact.dimensions)
            for fact in facts
        )
        or type(server_identity) is not NativeCaptureServerIdentityV3
        or type(boundary) is not NativeCaptureBoundary
        or type(temporal_id) is not UUID
        or type(baseline.tracks) is not tuple
        or len(baseline.tracks) > 5000
        or any(
            type(track) is not SnapshotTrack
            or type(track.metadata_tokens) is not tuple
            or len(track.metadata_tokens) > 4096
            for track in baseline.tracks
        )
    ):
        raise ValueError("NATIVE_SOURCE3_INPUT_TYPE")
    trace, ref = response.request, baseline.reference
    if type(trace.created_at) is not datetime or trace.created_at.tzinfo is None:
        raise ValueError("NATIVE_SOURCE3_P11_BINDING")
    created = trace.created_at.astimezone(UTC)
    expires = created + timedelta(days=180)
    cutoff = (created - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
    if (
        type(grant) is not TrainingCaptureGrant
        or not _q53(grant.revision, positive=True)
        or not _sha(grant.receipt_sha256)
    ):
        raise ValueError("NATIVE_SOURCE3_CONSENT_GRANT")
    if not _q53(trace.query.seed):
        raise ValueError("NATIVE_SOURCE3_SEED")
    if (
        boundary.owner != trace.query.user_id
        or trace.query.surface is not RecommendationSurface.RECOMMENDATIONS
        or boundary.cutoff_at_ms != cutoff
        or trace.query.shadow is not False
        or trace.snapshot != ref
        or baseline.retained_until.tzinfo is None
        or baseline.retained_until < expires
        or tuple(fact.event_id for fact in facts) != boundary.event_ids
    ):
        raise ValueError("NATIVE_SOURCE3_P11_BINDING")
    pipeline = baseline_pipeline_definition(baseline.policy_version)
    if trace.pipeline != pipeline:
        raise ValueError("NATIVE_SOURCE3_PIPELINE_AUTHORITY")
    prior = _prior_authorities(
        facts, causal_authority, pipeline.manifest_sha256, trace.recommendation_request_id
    )
    # This is a grouped linear-work bound, not another allowance. The caller's
    # existing lease may refuse it. Actual canonical writes remain metered below.
    event_count = len(facts)
    dimension_bound = min(512, sum(len(fact.dimensions) for fact in facts))
    normalization_visits = 128 * (8 * event_count + 16 * event_count + 32 * dimension_bound + 16)
    input_visits = (
        64 * len(baseline.tracks)
        + 128 * len(response.items)
        + 2 * sum(len(track.metadata_tokens) for track in baseline.tracks)
        + 1024
    )
    meter.reserve(
        byte_work_bound=2 * _MAX_BYTES,
        node_visit_bound=normalization_visits + input_visits,
    )
    meter.charge(node_visits=normalization_visits + input_visits)
    canonical_request = request_document(trace.query, pipeline, ref)
    if (
        trace.canonical_request != canonical_request
        or digests.digest(canonical_request) != trace.request_sha256
    ):
        raise ValueError("NATIVE_SOURCE3_P11_BINDING")
    baseline_document = recommendation_input_snapshot_document(
        boundary.owner, interaction_watermark=ref.interaction_watermark, tracks=baseline.tracks,
        policy_version=baseline.policy_version,
    )
    if digests.digest(baseline_document, max_bytes=MAX_ORIGINAL_BASELINE_V4_BYTES) != ref.input_snapshot_sha256:
        raise ValueError("NATIVE_SOURCE3_BASELINE_HASH")
    candidates = _candidates(baseline, response, candidate_authority)
    # The frozen normalizer probes the E-entry membership tuple for every fact
    # and creates at most two bounded UUIDv5 identities. Charge before either.
    normalization_bytes = 2176 * event_count
    membership_comparisons = event_count * (event_count + 1) // 2
    meter.reserve(byte_work_bound=normalization_bytes, node_visit_bound=membership_comparisons)
    meter.charge(
        bytes_written=normalization_bytes // 2,
        sha_input_bytes=normalization_bytes // 2,
        node_visits=membership_comparisons,
    )
    evidence = normalize_native_temporal_facts(facts, boundary)
    if len(evidence) <= MAX_SOURCE_EVENTS:
        dimension_counts: dict[TemporalDimension, int] = {}
        for item in evidence:
            if item.excluded_from_taste:
                continue
            for dimension in item.dimensions:
                if dimension not in dimension_counts and len(dimension_counts) == 512:
                    raise TemporalProjectionError("temporal snapshot dimension limit exceeded")
                dimension_counts[dimension] = dimension_counts.get(dimension, 0) + 1
        # R1 scans a growing reference list before retaining its final 64 IDs.
        # This preserves that work and refusal behavior rather than truncating.
        reference_comparisons = sum(count * (count - 1) // 2 for count in dimension_counts.values())
        meter.reserve(byte_work_bound=0, node_visit_bound=reference_comparisons)
        meter.charge(node_visits=reference_comparisons)
    profile = project_temporal_profile(
        owner_user_id=boundary.owner,
        cutoff_at_ms=boundary.cutoff_at_ms,
        interaction_watermark=boundary.interaction_watermark,
        evidence=evidence,
    )
    digests.retain()
    profile_document = native_source3_profile_document(
        profile, canonicalize=_canonical, meter=meter
    )
    # Freeze the original numeric eight-member profile before encoding any
    # source3 float bits. This is an independent BYTEA authority for the writer.
    profile_raw = _encode(profile_document, meter)
    digests.retain()
    meter.reserve(byte_work_bound=len(profile_raw), node_visit_bound=1)
    meter.charge(sha_input_bytes=len(profile_raw), node_visits=1)
    profile_raw_sha256 = sha256(profile_raw).hexdigest()
    source_membership_sha256 = digests.digest([str(value) for value in boundary.event_ids])
    profile_binary64_sha256 = _profile_binary64_hex(
        profile, source_membership_sha256, profile_document["profile_sha256"], meter, digests
    )
    associations = {
        fact.event_id: str(fact.causality.association_id) if fact.causality else None
        for fact in facts
    }
    temporal = {
        "schema_version": 3,
        "kind": "ORIGINAL_REQUEST_NATIVE_TEMPORAL_V3",
        "temporal_snapshot_id": str(temporal_id),
        "owner_user_id": str(boundary.owner),
        "baseline_snapshot_id": str(ref.snapshot_id),
        "baseline_snapshot_sha256": ref.input_snapshot_sha256,
        "server_identity": server_identity.to_dict(),
        "artifact_authority": {
            "kind": "P11_CPU_ONLY_FORMAT_BOUND_V3",
            "pipeline_manifest_sha256": pipeline.manifest_sha256,
            "required_artifacts": [],
            "causal_requests": prior,
        },
        "cutoff_at_ms": boundary.cutoff_at_ms,
        "native_interaction_watermark": boundary.interaction_watermark,
        "source_scope": "ALL_VISIBLE_APPLIED_NORMALIZED_V2_AT_CUTOFF",
        "supported_source_types": list(_SOURCE_TYPES),
        "source_event_ids": [str(value) for value in boundary.event_ids],
        "source_membership_sha256": source_membership_sha256,
        "facts": [_source_json(asdict(fact), meter) for fact in facts],
        "source_evidence": [
            {
                "schema_version": 2,
                **_source_json(
                    {
                        key: value
                        for key, value in asdict(item).items()
                        if key
                        not in {
                            "recommendation_request_id",
                            "impression_event_id",
                            "recommendation_source_rank",
                        }
                    },
                    meter,
                ),
                "association_id": associations[item.source_event_id],
            }
            for item in evidence
        ],
        "adaptive_profile": profile_document,
        "feature_policy_sha256": DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256,
        "expires_at": expires.isoformat(),
    }
    digests.retain()
    document = {
        "schema_version": 3,
        "kind": "SONA_CAPTURE_BUNDLE_V3",
        "original_capture_format_version": 3,
        "quality_eligible": False,
        "admission_blocker": "NATIVE_SOURCE3_REQUIRES_INDEPENDENT_PRODUCER_ADMISSION",
        "recommendation_request_id": str(trace.recommendation_request_id),
        "owner_user_id": str(boundary.owner),
        "request_sha256": trace.request_sha256,
        "pipeline_manifest_sha256": pipeline.manifest_sha256,
        "baseline_snapshot_id": str(ref.snapshot_id),
        "baseline_snapshot_sha256": ref.input_snapshot_sha256,
        "temporal_snapshot_id": str(temporal_id),
        "temporal_snapshot_sha256": "0" * 64,
        "temporal_snapshot": temporal,
        "feature_policy_sha256": DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256,
        "cutoff_at_ms": boundary.cutoff_at_ms,
        "interaction_watermark": ref.interaction_watermark,
        "native_interaction_watermark": boundary.interaction_watermark,
        "candidate_recording_ids": candidates,
        "candidate_membership_sha256": digests.digest(candidates),
        "p11_ranking_sha256": digests.digest(recommendation_ranking_document(response.items)),
        "source_snapshot_track_count": len(baseline.tracks),
        "candidate_universe_count": len(candidates),
        "ineligibility_reason": "UNIVERSE_OVER_CAP" if len(candidates) > 1024 else None,
        "consent_receipt_sha256": grant.receipt_sha256,
        "consent_generation": grant.revision,
    }
    encoded = native_source3_encode_float_tree_v3(document, meter=meter)
    encoded["temporal_snapshot_sha256"] = digests.digest(encoded["temporal_snapshot"])
    raw = _encode(encoded, meter)
    if not 1 <= len(raw) <= _MAX_BYTES:
        raise ValueError("NATIVE_SOURCE3_DOCUMENT_BOUND")
    capture = SonaCaptureBundleV3.from_bytes(raw, meter=meter, canonicalize=_canonical)
    try:
        original_profile = NativeOriginalProfilePayloadV3(
            profile_raw,
            profile_raw_sha256,
            profile_document["profile_sha256"],
            profile_binary64_sha256,
            boundary.owner,
            boundary.cutoff_at_ms,
            boundary.interaction_watermark,
            DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256,
            source_membership_sha256,
            meter,
        )
        return PreparedOriginalNativeCaptureV3(capture, original_profile)
    except BaseException:
        capture.close()
        raise

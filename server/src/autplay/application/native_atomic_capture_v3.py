"""Original P11 origin bytes and acyclic V3 identity, without SQL authority.

These values must be written by the private same-transaction SQL writer. No
connection, consent, persistence, score coverage or quality is implied here.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from autplay.application.native_original_capture_v3 import (
    NativeOriginalProfilePayloadV3,
    _candidates,
    _encode,
)
from autplay.application.recommendations import (
    baseline_pipeline_definition,
    pipeline_manifest_document,
    recommendation_input_snapshot_document,
    request_document,
)
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.domain.native_capture_creation_budget_v3 import NativeCaptureCreationLeaseV3
from autplay.domain.native_original_capture_v3 import (
    MAX_ORIGINAL_BASELINE_V4_BYTES,
    MAX_SOURCE3_BYTES,
    NativeOriginalCaptureV3DigestMeter,
    NativeOriginalCaptureV3Error,
    NativeOriginalCaptureV3Meter,
    SonaCaptureBundleV3,
)
from autplay.domain.native_profile_binary64_v3 import native_profile_binary64_digest_v3
from autplay.domain.native_request_budget_v3 import NativeRequestV3Budget
from autplay.domain.native_request_roots_v3 import (
    ConnectedP11InputOriginV3,
    NativeP11CandidateAuthorityV3,
    NativeP11ExpectedValuesV3,
    NativeServedRequestProofV3,
    VerifiedNativeP11RootV3,
    build_admitted_native_p11_request_proof_v3,
    build_native_p11_request_proof_v3,
    native_p11_decision_digest_v3,
    origin_connection_seal_v3,
    verify_admitted_native_p11_request_proof_v3,
    verify_native_p11_request_proof_v3,
)
from autplay.domain.native_served_authority_values import NativeServedIdentity
from autplay.domain.recommendations import (
    CandidateContribution,
    ComponentVersionRef,
    PipelineDefinition,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationResponse,
    RecommendationSurface,
    SnapshotTrack,
)

_MAX_PIPELINE_BYTES = 65_536


def _bounded_input(value: object, meter: NativeOriginalCaptureV3Meter, depth: int = 0) -> None:
    """Bound retained nested input before an original serializer can copy it."""

    meter.reserve(byte_work_bound=0, node_visit_bound=1)
    meter.charge(node_visits=1)
    if depth > 12:
        raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_DEPTH")
    if value is None or type(value) in (bool, int, float, UUID):
        return
    if isinstance(value, str):
        if len(value) > 4096:
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_STRING")
        meter.reserve(byte_work_bound=4 * len(value), node_visit_bound=0)
        size = len(value.encode("utf-8"))
        meter.charge(bytes_written=size)
        if size > 4096:
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_STRING")
        return
    if isinstance(value, tuple | list | dict):
        if type(value) not in (tuple, list, dict):
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_CONTAINER")
        if len(value) > 4096:
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_CONTAINER")
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_KEYS")
                _bounded_input(key, meter, depth + 1)
                _bounded_input(child, meter, depth + 1)
        else:
            for child in value:
                _bounded_input(child, meter, depth + 1)
        return
    raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_VALUE")


def _admit_original_inputs(
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    meter: NativeOriginalCaptureV3Meter,
) -> None:
    """Reserve and validate all variable input before materializing JSON trees."""

    if (
        type(baseline) is not RecommendationInputSnapshot
        or type(baseline.tracks) is not tuple
        or len(baseline.tracks) > 5000
        or type(response) is not RecommendationResponse
        or type(response.items) is not tuple
        or len(response.items) > 100
        or any(type(track) is not SnapshotTrack for track in baseline.tracks)
        or any(type(item) is not RankedRecommendation for item in response.items)
    ):
        raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_TYPED_INPUT")
    assembly_visits = 64 * len(baseline.tracks) + 256 * len(response.items) + 128
    meter.reserve(byte_work_bound=4 * MAX_SOURCE3_BYTES, node_visit_bound=assembly_visits)
    meter.charge(node_visits=assembly_visits)
    for track in baseline.tracks:
        if type(track.metadata_tokens) is not tuple:
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_TYPED_INPUT")
        for field in fields(track):
            _bounded_input(getattr(track, field.name), meter)
    for item in response.items:
        if (
            type(item.contributions) is not tuple
            or len(item.contributions) > 4096
            or type(item.reason_codes) is not tuple
            or any(type(row) is not CandidateContribution for row in item.contributions)
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_TYPED_INPUT")
        for field in fields(item):
            if field.name != "contributions":
                _bounded_input(getattr(item, field.name), meter)
        for contribution in item.contributions:
            for field in fields(contribution):
                _bounded_input(getattr(contribution, field.name), meter)


def _digest(raw: bytes, meter: NativeOriginalCaptureV3DigestMeter) -> str:
    meter.reserve(byte_work_bound=len(raw), node_visit_bound=1)
    meter.charge(sha_input_bytes=len(raw), node_visits=1)
    return sha256(raw).hexdigest()


def _admit_pipeline_input(
    pipeline: PipelineDefinition, meter: NativeOriginalCaptureV3Meter
) -> None:
    """Bound configured text before the unchanged manifest serializer copies it."""

    frozen = baseline_pipeline_definition(pipeline.version)
    if (
        type(pipeline) is not PipelineDefinition
        or type(pipeline.components) is not tuple
        or len(pipeline.components) != len(frozen.components)
        or any(type(component) is not ComponentVersionRef for component in pipeline.components)
        or type(pipeline.generator_budgets) is not tuple
        or len(pipeline.generator_budgets) != len(frozen.generator_budgets)
        or any(type(row) is not tuple or len(row) != 2 for row in pipeline.generator_budgets)
        or pipeline != frozen
    ):
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PIPELINE_INPUT")
    visits = 128 + 64 * (len(pipeline.components) + len(pipeline.generator_budgets))
    meter.reserve(byte_work_bound=_MAX_PIPELINE_BYTES, node_visit_bound=visits)
    meter.charge(node_visits=visits)
    for item in fields(pipeline):
        if item.name != "components":
            _bounded_input(getattr(pipeline, item.name), meter)
    for component in pipeline.components:
        for item in fields(component):
            _bounded_input(getattr(component, item.name), meter)


@dataclass(frozen=True, slots=True)
class NativeOriginalOriginPayloadsV3:
    """Independent original canonical bytes, never reconstructed from a root."""

    request_document: bytes
    baseline_document: bytes
    ranking_document: bytes
    profile_document: bytes
    pipeline_document: bytes
    request_sha256: str
    baseline_sha256: str
    ranking_sha256: str
    profile_document_sha256: str
    profile_sha256: str
    profile_binary64_sha256: str
    pipeline_document_sha256: str
    _meter: NativeOriginalCaptureV3DigestMeter = dataclass_field(repr=False, compare=False)
    _closed: bool = dataclass_field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.baseline_document) is not bytes
            or not 1 <= len(self.baseline_document) <= MAX_ORIGINAL_BASELINE_V4_BYTES
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_DOCUMENT_BOUND")
        for raw in (
            self.request_document,
            self.ranking_document,
            self.profile_document,
        ):
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE3_BYTES:
                raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_DOCUMENT_BOUND")
        if (
            type(self.pipeline_document) is not bytes
            or not 1 <= len(self.pipeline_document) <= _MAX_PIPELINE_BYTES
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_PIPELINE_BOUND")
        for value in (
            self.request_sha256,
            self.baseline_sha256,
            self.ranking_sha256,
            self.profile_document_sha256,
            self.profile_sha256,
            self.profile_binary64_sha256,
            self.pipeline_document_sha256,
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise NativeOriginalCaptureV3Error("SOURCE3_ORIGIN_DIGEST")

    def close(self) -> None:
        if not self._closed:
            object.__setattr__(self, "request_sha256", "")
            object.__setattr__(self, "baseline_sha256", "")
            object.__setattr__(self, "ranking_sha256", "")
            object.__setattr__(self, "profile_document_sha256", "")
            object.__setattr__(self, "profile_sha256", "")
            object.__setattr__(self, "profile_binary64_sha256", "")
            object.__setattr__(self, "pipeline_document_sha256", "")
            self._meter.release_digests(256)
            object.__setattr__(self, "_closed", True)


@dataclass(frozen=True, slots=True)
class PreparedNativeAtomicCaptureV3:
    """A representation awaiting independent SQL validation and atomic commit."""

    capture: SonaCaptureBundleV3
    original_profile: NativeOriginalProfilePayloadV3
    original: NativeOriginalOriginPayloadsV3
    proof: NativeServedRequestProofV3
    verified: VerifiedNativeP11RootV3
    decision_sha256: bytes
    connection_seal_sha256: bytes

    def __post_init__(self) -> None:
        if (
            type(self.capture) is not SonaCaptureBundleV3
            or type(self.original_profile) is not NativeOriginalProfilePayloadV3
            or type(self.original) is not NativeOriginalOriginPayloadsV3
            or type(self.proof) is not NativeServedRequestProofV3
            or type(self.verified) is not VerifiedNativeP11RootV3
            or type(self.decision_sha256) is not bytes
            or len(self.decision_sha256) != 32
            or type(self.connection_seal_sha256) is not bytes
            or len(self.connection_seal_sha256) != 32
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_TYPED_RESULT")

    def close(self) -> None:
        self.original.close()
        self.capture.close()
        self.original_profile.close()

    def __enter__(self) -> PreparedNativeAtomicCaptureV3:
        self.original_profile.assert_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def prepare_native_atomic_capture_v3(
    *,
    capture: SonaCaptureBundleV3,
    original_profile: NativeOriginalProfilePayloadV3,
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    identity: NativeServedIdentity,
    served_request_id: UUID,
    candidate_authority: NativeP11CandidateAuthorityV3,
    root_budget: NativeRequestV3Budget | NativeCaptureCreationLeaseV3,
    source_meter: NativeOriginalCaptureV3DigestMeter,
    transaction_binding: tuple[int, int] | None = None,
) -> PreparedNativeAtomicCaptureV3:
    """Bind the actual original response before constructing any downstream root.

    Original P11 creation shares one transaction-bound lease across the source,
    root builder, verifier and finalization. Standalone callers retain their
    existing independent root admissions.
    Ordinary C>1024 P11 remains representable; Sona admission happens elsewhere.
    """
    if type(capture) is not SonaCaptureBundleV3:
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_CAPTURE_TYPE")
    if type(original_profile) is not NativeOriginalProfilePayloadV3:
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PROFILE_TYPE")
    original_profile.assert_open()
    if isinstance(root_budget, NativeCaptureCreationLeaseV3):
        if (
            root_budget is not source_meter
            or original_profile._meter is not root_budget
            or transaction_binding is None
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_CREATION_BINDING")
        root_budget.assert_binding(
            transaction_binding=transaction_binding,
            owner_user_id=capture.owner_user_id,
            original_request_id=capture.recommendation_request_id,
            served_request_id=served_request_id,
        )
    _admit_original_inputs(baseline, response, source_meter)
    trace = response.request
    identity.validate()
    if (
        type(served_request_id) is not UUID
        or served_request_id == trace.recommendation_request_id
        or type(trace.created_at) is not datetime
        or trace.created_at.tzinfo is None
        or baseline.retained_until.tzinfo is None
        or trace.query.surface is not RecommendationSurface.RECOMMENDATIONS
        or trace.query.shadow is not False
        or trace.pipeline != baseline_pipeline_definition(baseline.policy_version)
        or trace.snapshot != baseline.reference
        or capture.recommendation_request_id != trace.recommendation_request_id
        or capture.owner_user_id != trace.query.user_id
        or identity.owner != trace.query.user_id
        or capture.baseline_snapshot_sha256 != baseline.reference.input_snapshot_sha256
        or capture.source_snapshot_track_count != len(baseline.tracks)
        or type(trace.query.seed) is not int
        or not 0 <= trace.query.seed <= 9_007_199_254_740_991
    ):
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_ORIGINAL_BINDING")

    created_at = trace.created_at.astimezone(UTC)
    expires_at = created_at + timedelta(days=180)
    cutoff = (created_at - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
    canonical_request = request_document(trace.query, trace.pipeline, baseline.reference)
    candidates = _candidates(baseline, response, candidate_authority)
    with capture.projection(meter=source_meter) as captured:
        _check_capture_projection_v3(
            captured,
            original_profile,
            baseline,
            response,
            identity,
            candidates,
            canonical_request,
            cutoff,
            expires_at,
            source_meter,
        )
    with capture.verified_numeric_profile(meter=source_meter) as numeric_profile:
        if (
            numeric_profile["profile_sha256"] != original_profile.profile_sha256
            or _encode(numeric_profile, source_meter) != original_profile.document
        ):
            raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PROFILE_BINDING")
        _check_profile_binary64_binding_v3(numeric_profile, original_profile, source_meter)

    # Independent origin construction precedes expected/root creation. Values
    # come from the actual original trace, baseline and returned P11 items.
    baseline_value = recommendation_input_snapshot_document(
        trace.query.user_id,
        interaction_watermark=baseline.reference.interaction_watermark,
        tracks=baseline.tracks,
        policy_version=baseline.policy_version,
    )
    ranking_value = recommendation_ranking_document(response.items)
    _admit_pipeline_input(trace.pipeline, source_meter)
    pipeline_value = pipeline_manifest_document(trace.pipeline)
    request_raw = _encode(canonical_request, source_meter)
    baseline_raw = _encode(baseline_value, source_meter, max_bytes=MAX_ORIGINAL_BASELINE_V4_BYTES)
    ranking_raw = _encode(ranking_value, source_meter)
    pipeline_raw = _encode(pipeline_value, source_meter, max_bytes=_MAX_PIPELINE_BYTES)
    source_meter.retain_digests(256)
    try:
        original = NativeOriginalOriginPayloadsV3(
            request_raw,
            baseline_raw,
            ranking_raw,
            original_profile.document,
            pipeline_raw,
            _digest(request_raw, source_meter),
            _digest(baseline_raw, source_meter),
            _digest(ranking_raw, source_meter),
            original_profile.document_sha256,
            original_profile.profile_sha256,
            original_profile.profile_binary64_sha256,
            _digest(pipeline_raw, source_meter),
            source_meter,
        )
    except BaseException:
        source_meter.release_digests(256)
        raise
    if (
        original.request_sha256 != trace.request_sha256
        or original.baseline_sha256 != capture.baseline_snapshot_sha256
        or original.ranking_sha256 != capture.p11_ranking_sha256
        or original.pipeline_document_sha256 != trace.pipeline.manifest_sha256
    ):
        original.close()
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_ORIGINAL_DIGEST")
    try:
        origin = ConnectedP11InputOriginV3(
            capture.recommendation_request_id,
            capture.bundle_sha256,
            capture.consent_generation,
            capture.consent_receipt_sha256,
        )
        expected = NativeP11ExpectedValuesV3(
            served_request_id, identity, trace.query, baseline, response, trace.pipeline, origin,
            # The inline proof contains both tracks and returned items plus fixed
            # descriptors. Reserve room for the root header even if the original
            # baseline alone still fits its V3 storage format.
            reference_snapshot=(
                baseline.policy_version == "2"
                or len(original.baseline_document) + len(original.ranking_document) + 65_536
                > MAX_SOURCE3_BYTES
            ),
        )
        if isinstance(root_budget, NativeCaptureCreationLeaseV3):
            proof = build_admitted_native_p11_request_proof_v3(
                expected=expected,
                candidate_authority=candidate_authority,
                meter=root_budget.root_builder_meter(
                    tracks=len(baseline.tracks), items=len(response.items)
                ),
            )
            verified = verify_admitted_native_p11_request_proof_v3(
                proof,
                expected=expected,
                candidate_authority=candidate_authority,
                meter=root_budget.root_verifier_meter(raw_bytes=len(proof.document)),
            )
        else:
            proof = build_native_p11_request_proof_v3(
                expected=expected, candidate_authority=candidate_authority, budget=root_budget
            )
            verified = verify_native_p11_request_proof_v3(
                proof,
                expected=expected,
                candidate_authority=candidate_authority,
                budget=root_budget,
            )
        decision = native_p11_decision_digest_v3(
            verified,
            created_at=created_at,
            served_truth_expires_at=expires_at,
            budget=root_budget,
        )
        seal = origin_connection_seal_v3(verified, decision, budget=root_budget)
        return PreparedNativeAtomicCaptureV3(
            capture, original_profile, original, proof, verified, decision, seal
        )
    except BaseException:
        original.close()
        raise


def _check_capture_projection_v3(
    captured: dict[str, Any],
    original_profile: NativeOriginalProfilePayloadV3,
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    identity: NativeServedIdentity,
    candidates: list[str],
    canonical_request: dict[str, Any],
    cutoff: int,
    expires_at: datetime,
    source_meter: NativeOriginalCaptureV3DigestMeter,
) -> None:
    trace = response.request
    temporal = captured["temporal_snapshot"]
    if (
        trace.canonical_request != canonical_request
        or baseline.retained_until < expires_at
        or captured["request_sha256"] != trace.request_sha256
        or captured["pipeline_manifest_sha256"] != trace.pipeline.manifest_sha256
        or captured["baseline_snapshot_id"] != str(baseline.reference.snapshot_id)
        or captured["interaction_watermark"] != baseline.reference.interaction_watermark
        or captured["cutoff_at_ms"] != cutoff
        or temporal["expires_at"] != expires_at.isoformat()
        or temporal["server_identity"]
        != {
            "server_instance_id": str(identity.server_instance),
            "identity_epoch": identity.identity_epoch,
        }
        or captured["candidate_recording_ids"] != candidates
        or captured["candidate_universe_count"] != len(candidates)
    ):
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_CAPTURE_BINDING")
    if (
        original_profile.owner_user_id != trace.query.user_id
        or original_profile.cutoff_at_ms != cutoff
        or original_profile.native_interaction_watermark != temporal["native_interaction_watermark"]
        or original_profile.feature_policy_sha256 != temporal["feature_policy_sha256"]
        or original_profile.source_membership_sha256 != temporal["source_membership_sha256"]
        or original_profile.profile_sha256 != temporal["adaptive_profile"]["profile_sha256"]
    ):
        raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PROFILE_BINDING")
    source_meter.retain_digests(64)
    try:
        if _digest(original_profile.document, source_meter) != original_profile.document_sha256:
            raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PROFILE_BINDING")
    finally:
        source_meter.release_digests(64)


def _check_profile_binary64_binding_v3(
    numeric_profile: dict[str, Any],
    original_profile: NativeOriginalProfilePayloadV3,
    meter: NativeOriginalCaptureV3DigestMeter,
) -> None:
    """Compare a fact-validated scoped projection with independent original S."""
    meter.retain_digests(96)
    source_raw: bytes | None = None
    old_raw: bytes | None = None
    expected_raw: bytes | None = None
    error: BaseException | None = None
    try:
        meter.reserve(byte_work_bound=288, node_visit_bound=3)
        meter.charge(bytes_read=192, bytes_written=96, node_visits=3)
        source_raw = bytes.fromhex(original_profile.source_membership_sha256)
        old_raw = bytes.fromhex(original_profile.profile_sha256)
        expected_raw = bytes.fromhex(original_profile.profile_binary64_sha256)
        with native_profile_binary64_digest_v3(
            numeric_profile,
            source_membership_sha256=source_raw,
            original_profile_sha256=old_raw,
            meter=meter,
        ) as raw:
            meter.reserve(byte_work_bound=64, node_visit_bound=1)
            meter.charge(bytes_read=64, node_visits=1)
            matches = raw == expected_raw
            del raw
        if not matches:
            raise NativeOriginalCaptureV3Error("SOURCE3_ATOMIC_PROFILE_BINDING")
    except BaseException as caught:
        caught.__traceback__ = None
        caught.__context__ = None
        caught.__cause__ = None
        error = caught
    finally:
        source_raw = old_raw = expected_raw = None
        meter.release_digests(96)
    if error is not None:
        raise error

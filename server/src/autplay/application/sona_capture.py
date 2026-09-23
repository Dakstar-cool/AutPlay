"""Canonical, model-independent native Sona capture preparation.

This module has no persistence or activation side effects. The caller must obtain
the temporal snapshot and independent consent in the P11 database transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import NoReturn, cast
from uuid import UUID

import rfc8785

from autplay.application.adaptive_recommendations import (
    canonical_sha256,
    temporal_evidence_from_document,
)
from autplay.application.recommendations import (
    RECOMMENDATION_MAX_SNAPSHOT_TRACKS,
    RecommendationPipelineRunner,
    recommendation_input_snapshot_document,
    request_document,
)
from autplay.application.sona_shadow import recommendation_ranking_sha256
from autplay.domain.recommendations import (
    JsonValue,
    RecommendationInputSnapshot,
    RecommendationResponse,
    RecommendationSurface,
)
from autplay.domain.sona import SonaTemporalSnapshot

MAX_SONA_CAPTURE_BUNDLE_BYTES = 16_777_216
MAX_SONA_SHADOW_UNIVERSE = 1_024


@dataclass(frozen=True, slots=True)
class SonaCaptureBundleV1:
    recommendation_request_id: UUID
    owner_user_id: UUID
    baseline_snapshot_sha256: str
    temporal_snapshot_sha256: str
    candidate_membership_sha256: str
    p11_ranking_sha256: str
    bundle_sha256: str
    consent_receipt_sha256: str
    consent_generation: int
    cutoff_at_ms: int
    interaction_watermark: int
    universe_count: int
    eligible_count: int
    ineligibility_reason: str | None
    document: bytes


def prepare_sona_capture_bundle(
    *,
    baseline: RecommendationInputSnapshot,
    response: RecommendationResponse,
    temporal: SonaTemporalSnapshot,
    temporal_document: dict[str, JsonValue],
    consent_receipt_sha256: str,
    consent_generation: int,
    candidate_authority: RecommendationPipelineRunner | None = None,
) -> SonaCaptureBundleV1:
    """Validate complete same-cutoff inputs before any capture SQL is attempted."""

    trace = response.request
    query = trace.query
    ref = baseline.reference
    if query.surface is not RecommendationSurface.RECOMMENDATIONS or query.shadow:
        raise ValueError("native Sona capture requires a public recommendations request")
    if trace.created_at.tzinfo is None or baseline.retained_until.tzinfo is None:
        raise ValueError("native Sona capture times must be timezone-aware")
    expires_at = trace.created_at + timedelta(days=180)
    if baseline.retained_until < expires_at or temporal.retained_until < expires_at:
        raise ValueError("native Sona capture requires 180-day retained inputs")
    if trace.snapshot != ref or temporal.baseline_snapshot_id != ref.snapshot_id:
        raise ValueError("native Sona capture baseline reference mismatch")
    canonical_request = request_document(query, trace.pipeline, ref)
    if (
        trace.canonical_request != canonical_request
        or canonical_sha256(canonical_request) != trace.request_sha256
    ):
        raise ValueError("native Sona capture request hash mismatch")
    if (
        temporal.owner_user_id != query.user_id
        or temporal.baseline_input_snapshot_sha256 != ref.input_snapshot_sha256
        or temporal.interaction_watermark != ref.interaction_watermark
        or temporal.catalog_snapshot != ref.catalog_snapshot
        or temporal.availability_snapshot_sha256 != ref.availability_snapshot
    ):
        raise ValueError("native Sona capture temporal ancestry mismatch")
    if temporal.cutoff_at_ms != int(trace.created_at.timestamp() * 1000):
        raise ValueError("native Sona capture temporal cutoff differs from the P11 request")
    if not _valid_digest(consent_receipt_sha256) or consent_generation < 1:
        raise ValueError("native Sona capture consent receipt is invalid")
    if len(baseline.tracks) > RECOMMENDATION_MAX_SNAPSHOT_TRACKS:
        raise ValueError("native Sona capture baseline exceeds the P11 bound")

    baseline_document = recommendation_input_snapshot_document(
        query.user_id,
        interaction_watermark=ref.interaction_watermark,
        tracks=baseline.tracks,
    )
    if canonical_sha256(baseline_document) != ref.input_snapshot_sha256:
        raise ValueError("native Sona capture baseline hash mismatch")
    _validate_temporal_document(temporal, temporal_document)

    authority = candidate_authority or RecommendationPipelineRunner()
    candidate_ids = [
        str(track.recording_id)
        for track in sorted(
            authority.filter_snapshot_tracks(query, baseline),
            key=lambda value: value.recording_id.hex,
        )
    ]
    universe_count = len(baseline.tracks)
    eligible_count = len(candidate_ids)
    reason = "UNIVERSE_OVER_CAP" if universe_count > MAX_SONA_SHADOW_UNIVERSE else None
    membership_digest = canonical_sha256(cast(JsonValue, candidate_ids))
    ranking_digest = recommendation_ranking_sha256(response.items)
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "kind": "SONA_CAPTURE_BUNDLE_V1",
        "recommendation_request_id": str(trace.recommendation_request_id),
        "owner_user_id": str(query.user_id),
        "request_sha256": trace.request_sha256,
        "pipeline_manifest_sha256": trace.pipeline.manifest_sha256,
        "baseline_snapshot_id": str(ref.snapshot_id),
        "baseline_snapshot_sha256": ref.input_snapshot_sha256,
        "temporal_snapshot_id": str(temporal.temporal_snapshot_id),
        "temporal_snapshot_sha256": temporal.temporal_snapshot_sha256,
        "feature_policy_sha256": temporal.feature_policy_sha256,
        "cutoff_at_ms": temporal.cutoff_at_ms,
        "interaction_watermark": ref.interaction_watermark,
        "candidate_recording_ids": cast(list[JsonValue], candidate_ids),
        "candidate_membership_sha256": membership_digest,
        "p11_ranking_sha256": ranking_digest,
        "universe_count": universe_count,
        "eligible_count": eligible_count,
        "ineligibility_reason": reason,
        "consent_receipt_sha256": consent_receipt_sha256,
        "consent_generation": consent_generation,
    }
    encoded = rfc8785.dumps(document)
    if not 1 <= len(encoded) <= MAX_SONA_CAPTURE_BUNDLE_BYTES:
        raise ValueError("native Sona capture bundle exceeds the SQL byte bound")
    return SonaCaptureBundleV1(
        recommendation_request_id=trace.recommendation_request_id,
        owner_user_id=query.user_id,
        baseline_snapshot_sha256=ref.input_snapshot_sha256,
        temporal_snapshot_sha256=temporal.temporal_snapshot_sha256,
        candidate_membership_sha256=membership_digest,
        p11_ranking_sha256=ranking_digest,
        bundle_sha256=sha256(encoded).hexdigest(),
        consent_receipt_sha256=consent_receipt_sha256,
        consent_generation=consent_generation,
        cutoff_at_ms=temporal.cutoff_at_ms,
        interaction_watermark=ref.interaction_watermark,
        universe_count=universe_count,
        eligible_count=eligible_count,
        ineligibility_reason=reason,
        document=encoded,
    )


def _validate_temporal_document(
    temporal: SonaTemporalSnapshot, document: dict[str, JsonValue]
) -> None:
    declared = document.get("snapshot_sha256")
    unhashed = {key: value for key, value in document.items() if key != "snapshot_sha256"}
    if (
        declared != temporal.temporal_snapshot_sha256
        or canonical_sha256(unhashed) != declared
        or document.get("schema_version") != 2
        or document.get("snapshot_kind") != "RECOMMENDATION_TEMPORAL_SNAPSHOT_V2"
        or document.get("snapshot_id") != str(temporal.temporal_snapshot_id)
        or document.get("owner_user_id") != str(temporal.owner_user_id)
        or document.get("baseline_input_snapshot_sha256") != temporal.baseline_input_snapshot_sha256
        or document.get("cutoff_at_ms") != temporal.cutoff_at_ms
        or document.get("interaction_watermark") != temporal.interaction_watermark
        or document.get("catalog_snapshot") != temporal.catalog_snapshot
        or document.get("availability_snapshot_sha256") != temporal.availability_snapshot_sha256
        or document.get("retained_until_ms") != int(temporal.retained_until.timestamp() * 1000)
    ):
        raise ValueError("native Sona capture temporal document mismatch")
    policy = document.get("feature_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("content_sha256") != temporal.feature_policy_sha256
    ):
        raise ValueError("native Sona capture feature policy mismatch")
    raw = document.get("source_evidence")
    if (
        not isinstance(raw, list)
        or document.get("source_evidence_sha256") != canonical_sha256(raw)
        or len(raw) != len(temporal.evidence)
    ):
        raise ValueError("native Sona capture temporal evidence mismatch")
    parsed = tuple(
        temporal_evidence_from_document(cast(dict[str, object], value))
        if isinstance(value, dict)
        else _invalid_event()
        for value in raw
    )
    if parsed != temporal.evidence:
        raise ValueError("native Sona capture temporal evidence differs from retained snapshot")
    if any(value.effective_at_ms > temporal.cutoff_at_ms for value in parsed):
        raise ValueError("native Sona capture temporal evidence crosses the P11 cutoff")


def _invalid_event() -> NoReturn:
    raise ValueError("native Sona capture temporal event is invalid")


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)

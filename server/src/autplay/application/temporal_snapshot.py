"""Canonical temporal schema 2 document, shared by capture and offline adaptation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from autplay.application.adaptive_recommendations import canonical_sha256
from autplay.domain.adaptive_recommendations import AdaptiveFeaturePolicy
from autplay.domain.recommendations import JsonValue, RecommendationInputSnapshot


def build_temporal_snapshot_document(
    *,
    snapshot_id: UUID,
    baseline: RecommendationInputSnapshot,
    profile_document: dict[str, JsonValue],
    evidence_documents: Sequence[dict[str, JsonValue]],
    retained_until: datetime,
    policy: AdaptiveFeaturePolicy,
) -> dict[str, JsonValue]:
    event_time_policy: dict[str, JsonValue] = {
        "episode_inactivity_gap_ms": policy.episode_inactivity_gap_ms,
        "future_clock_tolerance_ms": policy.future_clock_tolerance_ms,
        "maximum_recent_backfill_ms": policy.maximum_recent_backfill_ms,
        "stable_order": [
            "effective_at_ms",
            "received_at_ms",
            "server_sequence",
            "evidence_id",
        ],
    }
    derived_features: dict[str, JsonValue] = {
        "maturity": profile_document["maturity"],
        "dimensions": profile_document["dimensions"],
    }
    source_evidence = cast(list[JsonValue], list(evidence_documents))
    document: dict[str, JsonValue] = {
        "schema_version": 2,
        "snapshot_kind": "RECOMMENDATION_TEMPORAL_SNAPSHOT_V2",
        "snapshot_id": str(snapshot_id),
        "owner_user_id": cast(str, profile_document["owner_user_id"]),
        "cutoff_at_ms": cast(int, profile_document["cutoff_at_ms"]),
        "interaction_watermark": cast(int, profile_document["interaction_watermark"]),
        "catalog_snapshot": baseline.reference.catalog_snapshot,
        "availability_snapshot_sha256": baseline.reference.availability_snapshot,
        "baseline_input_snapshot_sha256": baseline.reference.input_snapshot_sha256,
        "feature_policy": profile_document["feature_policy"],
        "event_time_policy_sha256": canonical_sha256(event_time_policy),
        "source_evidence": source_evidence,
        "adaptive_profile": profile_document,
        "derived_features_sha256": canonical_sha256(derived_features),
        "source_evidence_sha256": canonical_sha256(source_evidence),
        "retained_until_ms": int(retained_until.timestamp() * 1000),
    }
    document["snapshot_sha256"] = canonical_sha256(document)
    return document

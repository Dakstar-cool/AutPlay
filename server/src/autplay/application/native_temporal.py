"""Canonical transient temporal materialization and fresh complete source replay."""

from dataclasses import asdict
from hashlib import sha256
from hmac import compare_digest
from typing import Any
from uuid import UUID

import rfc8785

from autplay.application.adaptive_recommendations import adaptive_profile_document
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    project_temporal_profile,
)
from autplay.domain.native_temporal import (
    MAX_NATIVE_TEMPORAL_BYTES,
    NativeTemporalFact,
    NativeTemporalSelection,
    NativeTemporalSnapshot,
    normalize_native_temporal,
)
from autplay.ports.native_temporal import NativeTemporalReader


def _json(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json(item) for item in value]
    return value


def build_native_temporal_snapshot(
    selection: NativeTemporalSelection,
    facts: tuple[NativeTemporalFact, ...],
    *,
    authority: dict[str, Any],
) -> NativeTemporalSnapshot:
    """Pure assembly; only the trusted reader can establish authority for these inputs."""
    evidence = normalize_native_temporal(facts, selection)
    profile = project_temporal_profile(
        owner_user_id=selection.owner,
        cutoff_at_ms=selection.cutoff_at_ms,
        interaction_watermark=selection.interaction_watermark,
        evidence=evidence,
    )
    document = rfc8785.dumps(
        {
            "schema_version": 2,
            "kind": "READ_DERIVED_NATIVE_TEMPORAL_V2",
            "quality_eligible": False,
            "admission_blocker": "NATIVE_SCHEMA2_SOURCE_PRODUCER_UNAVAILABLE",
            "selection": _json(asdict(selection)),
            "authority": authority,
            "feature_policy_sha256": DEFAULT_ADAPTIVE_FEATURE_POLICY.content_sha256,
            "facts": [_json(asdict(fact)) for fact in facts],
            "source_evidence": [
                {
                    "schema_version": 2,
                    "kind": "NATIVE_TEMPORAL_EVIDENCE_V2",
                    **_json(
                        {
                            key: value
                            for key, value in asdict(item).items()
                            if key
                            not in {
                                "recommendation_request_id",
                                "impression_event_id",
                                "recommendation_source_rank",
                            }
                        }
                    ),
                    "association_id": next(
                        (
                            str(fact.causality.association_id)
                            for fact in facts
                            if fact.event_id == item.source_event_id and fact.causality is not None
                        ),
                        None,
                    ),
                }
                for item in evidence
            ],
            "adaptive_profile": adaptive_profile_document(profile),
        }
    )
    if len(document) > MAX_NATIVE_TEMPORAL_BYTES:
        raise ValueError("NATIVE_TEMPORAL_DOCUMENT_BOUND")
    return NativeTemporalSnapshot(facts, evidence, profile, document, sha256(document).hexdigest())


def replay_native_temporal_snapshot(
    snapshot: NativeTemporalSnapshot,
    *,
    selection: NativeTemporalSelection,
    reader: NativeTemporalReader,
) -> str:
    """Proof bytes and Python objects alone never authorize materialization reuse."""
    if not 1 <= len(snapshot.document) <= MAX_NATIVE_TEMPORAL_BYTES or not compare_digest(
        sha256(snapshot.document).hexdigest(), snapshot.sha256
    ):
        raise ValueError("NATIVE_TEMPORAL_SNAPSHOT_HASH")
    current = reader.materialize(selection)
    if snapshot != current:
        raise ValueError("NATIVE_TEMPORAL_REPLAY_MISMATCH")
    return current.sha256

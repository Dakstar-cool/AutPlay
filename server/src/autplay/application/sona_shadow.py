"""Owner-bound Sona shadow evidence and deterministic algorithmic replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from typing import cast
from uuid import UUID

import rfc8785

from autplay.application.sona import SonaShadowResult, SonaShadowService
from autplay.application.sona_codec import sona_output_envelope, sona_output_from_envelope
from autplay.domain.recommendations import (
    CandidateContribution,
    JsonValue,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationNotFound,
    RecommendationRequestTrace,
    RecommendationResponse,
    ReplayInputUnavailable,
)
from autplay.domain.sona import (
    SonaInferenceOutput,
    SonaSemanticId,
    SonaShadowEvidence,
    SonaShadowStatus,
    SonaTemporalSnapshot,
)
from autplay.ports.recommendations import (
    RecommendationSnapshotRepository,
    RecommendationTraceRepository,
    SonaShadowEvidenceRepository,
    SonaTemporalSnapshotReader,
)


class SonaShadowCoordinator:
    """Attach model evidence to P11 truth without creating shadow items or impressions."""

    def __init__(
        self,
        *,
        service: SonaShadowService,
        snapshots: RecommendationSnapshotRepository,
        traces: RecommendationTraceRepository,
        temporal_snapshots: SonaTemporalSnapshotReader,
        evidence: SonaShadowEvidenceRepository,
    ) -> None:
        self._service = service
        self._snapshots = snapshots
        self._traces = traces
        self._temporal_snapshots = temporal_snapshots
        self._evidence = evidence

    def run(
        self, p11_response: RecommendationResponse, temporal: SonaTemporalSnapshot
    ) -> SonaShadowEvidence:
        """Run one shadow inference for an already persisted, independently served P11 request."""

        trace = p11_response.request
        if trace.query.shadow:
            raise ValueError("Sona shadow must attach to a non-shadow P11 request")
        baseline = self._load_bound_baseline(trace)
        _validate_temporal_binding(trace, baseline, temporal)
        result = self._service.run(
            replace(trace.query, shadow=True),
            baseline,
            temporal_snapshot_id=temporal.temporal_snapshot_id,
            cutoff_at_ms=temporal.cutoff_at_ms,
            evidence=temporal.evidence,
        )
        stored = build_sona_shadow_evidence(p11_response, temporal, self._service, result)
        self._traces.ensure_pipeline(self._service.pipeline)
        self._evidence.save(stored)
        return stored

    def algorithmic_replay(
        self, owner_user_id: UUID, recommendation_request_id: UUID
    ) -> SonaShadowEvidence:
        """Re-run only from the original retained P11 and temporal snapshots."""

        stored = self._evidence.load(owner_user_id, recommendation_request_id)
        if stored is None:
            raise RecommendationNotFound
        _validate_service_identity(stored, self._service)
        p11_response = self._traces.exact(owner_user_id, recommendation_request_id)
        if p11_response is None:
            raise RecommendationNotFound
        trace = p11_response.request
        temporal = self._temporal_snapshots.load_sona_snapshot(
            owner_user_id, stored.temporal_snapshot_id
        )
        if temporal is None:
            raise ReplayInputUnavailable
        baseline = self._load_bound_baseline(trace)
        try:
            _validate_temporal_binding(trace, baseline, temporal)
            result = self._service.run(
                replace(trace.query, shadow=True),
                baseline,
                temporal_snapshot_id=temporal.temporal_snapshot_id,
                cutoff_at_ms=temporal.cutoff_at_ms,
                evidence=temporal.evidence,
            )
            replayed = build_sona_shadow_evidence(p11_response, temporal, self._service, result)
        except (RuntimeError, ValueError) as error:
            raise ReplayInputUnavailable from error
        if replayed != stored:
            raise ReplayInputUnavailable
        return replayed

    def _load_bound_baseline(
        self, trace: RecommendationRequestTrace
    ) -> RecommendationInputSnapshot:
        snapshot = self._snapshots.load(trace.query.user_id, trace.snapshot.snapshot_id)
        if snapshot is None or snapshot.reference != trace.snapshot:
            raise ReplayInputUnavailable
        return snapshot


def build_sona_shadow_evidence(
    p11_response: RecommendationResponse,
    temporal: SonaTemporalSnapshot,
    service: SonaShadowService,
    result: SonaShadowResult,
) -> SonaShadowEvidence:
    """Freeze one canonical shadow result against exact P11 and R1 inputs."""

    trace = p11_response.request
    status = SonaShadowStatus.DEGRADED if result.degraded_to_p11 else SonaShadowStatus.SUCCEEDED
    output_sha256 = _output_sha256(result.output)
    ranking_sha256 = _postprocessed_ranking_sha256(result.postprocessed_ranking)
    values: dict[str, object] = {
        "owner_user_id": trace.query.user_id,
        "recommendation_request_id": trace.recommendation_request_id,
        "p11_request_sha256": trace.request_sha256,
        "p11_pipeline_manifest_sha256": trace.pipeline.manifest_sha256,
        "p11_ranking_sha256": recommendation_ranking_sha256(p11_response.items),
        "baseline_snapshot_id": trace.snapshot.snapshot_id,
        "baseline_input_snapshot_sha256": trace.snapshot.input_snapshot_sha256,
        "temporal_snapshot_id": temporal.temporal_snapshot_id,
        "temporal_snapshot_sha256": temporal.temporal_snapshot_sha256,
        "feature_policy_sha256": temporal.feature_policy_sha256,
        "shadow_pipeline_key": service.pipeline.pipeline_key,
        "shadow_pipeline_version": service.pipeline.version,
        "shadow_pipeline_manifest_sha256": service.pipeline.manifest_sha256,
        "tokenizer_sha256": service.tokenizer_sha256,
        "model_manifest_sha256": service.model_manifest_sha256,
        "sona_request_sha256": (
            result.request.request_sha256 if result.request is not None else None
        ),
        "sona_output_sha256": output_sha256,
        "postprocessed_ranking_sha256": ranking_sha256,
        "generated_recording_ids": result.generated_recording_ids,
        "generated_expansion": result.generated_expansion,
        "status": status,
        "reason": result.reason,
        "output": result.output,
        "postprocessed_ranking": result.postprocessed_ranking,
        "created_at": trace.created_at,
    }
    unhashed = _evidence_document(values)
    evidence_sha256 = sha256(rfc8785.dumps(unhashed)).hexdigest()
    return SonaShadowEvidence(
        owner_user_id=cast(UUID, values["owner_user_id"]),
        recommendation_request_id=cast(UUID, values["recommendation_request_id"]),
        p11_request_sha256=cast(str, values["p11_request_sha256"]),
        p11_pipeline_manifest_sha256=cast(str, values["p11_pipeline_manifest_sha256"]),
        p11_ranking_sha256=cast(str, values["p11_ranking_sha256"]),
        baseline_snapshot_id=cast(UUID, values["baseline_snapshot_id"]),
        baseline_input_snapshot_sha256=cast(str, values["baseline_input_snapshot_sha256"]),
        temporal_snapshot_id=cast(UUID, values["temporal_snapshot_id"]),
        temporal_snapshot_sha256=cast(str, values["temporal_snapshot_sha256"]),
        feature_policy_sha256=cast(str, values["feature_policy_sha256"]),
        shadow_pipeline_key=cast(str, values["shadow_pipeline_key"]),
        shadow_pipeline_version=cast(str, values["shadow_pipeline_version"]),
        shadow_pipeline_manifest_sha256=cast(str, values["shadow_pipeline_manifest_sha256"]),
        tokenizer_sha256=cast(str, values["tokenizer_sha256"]),
        model_manifest_sha256=cast(str, values["model_manifest_sha256"]),
        sona_request_sha256=cast(str | None, values["sona_request_sha256"]),
        sona_output_sha256=cast(str | None, values["sona_output_sha256"]),
        postprocessed_ranking_sha256=cast(str | None, values["postprocessed_ranking_sha256"]),
        evidence_sha256=evidence_sha256,
        status=cast(SonaShadowStatus, values["status"]),
        reason=cast(str | None, values["reason"]),
        output=cast(SonaInferenceOutput | None, values["output"]),
        postprocessed_ranking=cast(
            tuple[RankedRecommendation, ...], values["postprocessed_ranking"]
        ),
        generated_recording_ids=cast(tuple[UUID, ...], values["generated_recording_ids"]),
        generated_expansion=cast(
            tuple[tuple[SonaSemanticId, tuple[UUID, ...]], ...], values["generated_expansion"]
        ),
        created_at=cast(datetime, values["created_at"]),
    )


def sona_shadow_evidence_document(value: SonaShadowEvidence) -> dict[str, JsonValue]:
    if _output_sha256(value.output) != value.sona_output_sha256:
        raise ValueError("Sona shadow output hash binding mismatch")
    if (
        _postprocessed_ranking_sha256(value.postprocessed_ranking)
        != value.postprocessed_ranking_sha256
    ):
        raise ValueError("Sona shadow postprocessed ranking hash binding mismatch")
    values: dict[str, object] = {
        "owner_user_id": value.owner_user_id,
        "recommendation_request_id": value.recommendation_request_id,
        "p11_request_sha256": value.p11_request_sha256,
        "p11_pipeline_manifest_sha256": value.p11_pipeline_manifest_sha256,
        "p11_ranking_sha256": value.p11_ranking_sha256,
        "baseline_snapshot_id": value.baseline_snapshot_id,
        "baseline_input_snapshot_sha256": value.baseline_input_snapshot_sha256,
        "temporal_snapshot_id": value.temporal_snapshot_id,
        "temporal_snapshot_sha256": value.temporal_snapshot_sha256,
        "feature_policy_sha256": value.feature_policy_sha256,
        "shadow_pipeline_key": value.shadow_pipeline_key,
        "shadow_pipeline_version": value.shadow_pipeline_version,
        "shadow_pipeline_manifest_sha256": value.shadow_pipeline_manifest_sha256,
        "tokenizer_sha256": value.tokenizer_sha256,
        "model_manifest_sha256": value.model_manifest_sha256,
        "sona_request_sha256": value.sona_request_sha256,
        "sona_output_sha256": value.sona_output_sha256,
        "postprocessed_ranking_sha256": value.postprocessed_ranking_sha256,
        "generated_recording_ids": value.generated_recording_ids,
        "generated_expansion": value.generated_expansion,
        "status": value.status,
        "reason": value.reason,
        "output": value.output,
        "postprocessed_ranking": value.postprocessed_ranking,
        "created_at": value.created_at,
    }
    document = _evidence_document(values)
    digest = sha256(rfc8785.dumps(document)).hexdigest()
    if digest != value.evidence_sha256:
        raise ValueError("Sona shadow evidence canonical hash mismatch")
    document["evidence_sha256"] = value.evidence_sha256
    return document


def sona_shadow_evidence_from_document(value: Mapping[str, object]) -> SonaShadowEvidence:
    """Parse persisted evidence and verify both nested output and envelope hashes."""

    expected = {
        "schema_version",
        "kind",
        "owner_user_id",
        "recommendation_request_id",
        "p11_request_sha256",
        "p11_pipeline_manifest_sha256",
        "p11_ranking_sha256",
        "baseline_snapshot_id",
        "baseline_input_snapshot_sha256",
        "temporal_snapshot_id",
        "temporal_snapshot_sha256",
        "feature_policy_sha256",
        "shadow_pipeline_key",
        "shadow_pipeline_version",
        "shadow_pipeline_manifest_sha256",
        "tokenizer_sha256",
        "model_manifest_sha256",
        "sona_request_sha256",
        "sona_output_sha256",
        "postprocessed_ranking_sha256",
        "generated_recording_ids",
        "generated_expansion",
        "status",
        "reason",
        "output",
        "postprocessed_ranking",
        "created_at",
        "evidence_sha256",
    }
    if set(value) != expected:
        raise ValueError("Sona shadow evidence keys are invalid")
    if _integer(value, "schema_version") != 2 or _string(value, "kind") != "SONA_SHADOW_V2":
        raise ValueError("Sona shadow evidence schema is unsupported")
    document = cast(
        dict[str, JsonValue], {key: item for key, item in value.items() if key != "evidence_sha256"}
    )
    evidence_sha256 = _string(value, "evidence_sha256")
    if sha256(rfc8785.dumps(document)).hexdigest() != evidence_sha256:
        raise ValueError("Sona shadow evidence hash mismatch")
    raw_output = value.get("output")
    output = None
    if raw_output is not None:
        if not isinstance(raw_output, dict):
            raise ValueError("Sona shadow output envelope is invalid")
        if _string(raw_output, "output_sha256") != _optional_string(value, "sona_output_sha256"):
            raise ValueError("Sona shadow output hash binding mismatch")
        output = sona_output_from_envelope(cast(dict[str, object], raw_output))
    ranking = _postprocessed_ranking_from_document(value.get("postprocessed_ranking"))
    if _postprocessed_ranking_sha256(ranking) != _optional_string(
        value, "postprocessed_ranking_sha256"
    ):
        raise ValueError("Sona shadow postprocessed ranking hash binding mismatch")
    raw_generated = value.get("generated_recording_ids")
    if not isinstance(raw_generated, list) or any(
        not isinstance(item, str) for item in raw_generated
    ):
        raise ValueError("Sona shadow generated recording IDs are invalid")
    try:
        generated_recording_ids = tuple(UUID(item) for item in raw_generated)
    except ValueError as error:
        raise ValueError("Sona shadow generated recording ID must be a UUID") from error
    generated_expansion = _generated_expansion_from_document(value.get("generated_expansion"))
    return SonaShadowEvidence(
        owner_user_id=_uuid(value, "owner_user_id"),
        recommendation_request_id=_uuid(value, "recommendation_request_id"),
        p11_request_sha256=_string(value, "p11_request_sha256"),
        p11_pipeline_manifest_sha256=_string(value, "p11_pipeline_manifest_sha256"),
        p11_ranking_sha256=_string(value, "p11_ranking_sha256"),
        baseline_snapshot_id=_uuid(value, "baseline_snapshot_id"),
        baseline_input_snapshot_sha256=_string(value, "baseline_input_snapshot_sha256"),
        temporal_snapshot_id=_uuid(value, "temporal_snapshot_id"),
        temporal_snapshot_sha256=_string(value, "temporal_snapshot_sha256"),
        feature_policy_sha256=_string(value, "feature_policy_sha256"),
        shadow_pipeline_key=_string(value, "shadow_pipeline_key"),
        shadow_pipeline_version=_string(value, "shadow_pipeline_version"),
        shadow_pipeline_manifest_sha256=_string(value, "shadow_pipeline_manifest_sha256"),
        tokenizer_sha256=_string(value, "tokenizer_sha256"),
        model_manifest_sha256=_string(value, "model_manifest_sha256"),
        sona_request_sha256=_optional_string(value, "sona_request_sha256"),
        sona_output_sha256=_optional_string(value, "sona_output_sha256"),
        postprocessed_ranking_sha256=_optional_string(value, "postprocessed_ranking_sha256"),
        evidence_sha256=evidence_sha256,
        status=SonaShadowStatus(_string(value, "status")),
        reason=_optional_string(value, "reason"),
        output=output,
        postprocessed_ranking=ranking,
        generated_recording_ids=generated_recording_ids,
        generated_expansion=generated_expansion,
        created_at=_datetime(value, "created_at"),
    )


def _validate_temporal_binding(
    trace: RecommendationRequestTrace,
    baseline: RecommendationInputSnapshot,
    temporal: SonaTemporalSnapshot,
) -> None:
    reference = baseline.reference
    if (
        temporal.owner_user_id != trace.query.user_id
        or temporal.baseline_snapshot_id != reference.snapshot_id
        or temporal.baseline_input_snapshot_sha256 != reference.input_snapshot_sha256
        or temporal.interaction_watermark != reference.interaction_watermark
        or temporal.catalog_snapshot != reference.catalog_snapshot
        or temporal.availability_snapshot_sha256 != reference.availability_snapshot
    ):
        raise ReplayInputUnavailable


def _validate_service_identity(stored: SonaShadowEvidence, service: SonaShadowService) -> None:
    pipeline = service.pipeline
    if (
        stored.shadow_pipeline_key != pipeline.pipeline_key
        or stored.shadow_pipeline_version != pipeline.version
        or stored.shadow_pipeline_manifest_sha256 != pipeline.manifest_sha256
        or stored.tokenizer_sha256 != service.tokenizer_sha256
        or stored.model_manifest_sha256 != service.model_manifest_sha256
    ):
        raise ReplayInputUnavailable


def _evidence_document(values: Mapping[str, object]) -> dict[str, JsonValue]:
    output = cast(SonaInferenceOutput | None, values["output"])
    ranking = cast(tuple[RankedRecommendation, ...], values["postprocessed_ranking"])
    generated_recording_ids = cast(tuple[UUID, ...], values["generated_recording_ids"])
    generated_expansion = cast(
        tuple[tuple[SonaSemanticId, tuple[UUID, ...]], ...], values["generated_expansion"]
    )
    created_at = cast(datetime, values["created_at"])
    return {
        "schema_version": 2,
        "kind": "SONA_SHADOW_V2",
        "owner_user_id": str(values["owner_user_id"]),
        "recommendation_request_id": str(values["recommendation_request_id"]),
        "p11_request_sha256": cast(str, values["p11_request_sha256"]),
        "p11_pipeline_manifest_sha256": cast(str, values["p11_pipeline_manifest_sha256"]),
        "p11_ranking_sha256": cast(str, values["p11_ranking_sha256"]),
        "baseline_snapshot_id": str(values["baseline_snapshot_id"]),
        "baseline_input_snapshot_sha256": cast(str, values["baseline_input_snapshot_sha256"]),
        "temporal_snapshot_id": str(values["temporal_snapshot_id"]),
        "temporal_snapshot_sha256": cast(str, values["temporal_snapshot_sha256"]),
        "feature_policy_sha256": cast(str, values["feature_policy_sha256"]),
        "shadow_pipeline_key": cast(str, values["shadow_pipeline_key"]),
        "shadow_pipeline_version": cast(str, values["shadow_pipeline_version"]),
        "shadow_pipeline_manifest_sha256": cast(str, values["shadow_pipeline_manifest_sha256"]),
        "tokenizer_sha256": cast(str, values["tokenizer_sha256"]),
        "model_manifest_sha256": cast(str, values["model_manifest_sha256"]),
        "sona_request_sha256": cast(str | None, values["sona_request_sha256"]),
        "sona_output_sha256": cast(str | None, values["sona_output_sha256"]),
        "postprocessed_ranking_sha256": cast(str | None, values["postprocessed_ranking_sha256"]),
        "generated_recording_ids": [str(value) for value in generated_recording_ids],
        "generated_expansion": [
            {
                "semantic_id": list(semantic_id.values),
                "recording_ids": [str(value) for value in recording_ids],
            }
            for semantic_id, recording_ids in generated_expansion
        ],
        "status": cast(SonaShadowStatus, values["status"]).value,
        "reason": cast(str | None, values["reason"]),
        "output": sona_output_envelope(output) if output is not None else None,
        "postprocessed_ranking": (_postprocessed_ranking_document(ranking) if ranking else None),
        "created_at": created_at.astimezone(UTC).isoformat(timespec="microseconds"),
    }


def _output_sha256(output: SonaInferenceOutput | None) -> str | None:
    if output is None:
        return None
    envelope = sona_output_envelope(output)
    return cast(str, envelope["output_sha256"])


def _postprocessed_ranking_sha256(
    ranking: tuple[RankedRecommendation, ...],
) -> str | None:
    if not ranking:
        return None
    return sha256(rfc8785.dumps(_postprocessed_ranking_document(ranking))).hexdigest()


def _postprocessed_ranking_document(
    ranking: tuple[RankedRecommendation, ...],
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "kind": "SONA_POSTPROCESSED_RANKING_V1",
        "items": [
            {
                "recording_id": str(item.recording_id),
                "source_rank": item.source_rank,
                "score": item.score,
                "reason_code": item.reason_code,
                "reason_codes": list(item.reason_codes),
                "contributions": [
                    {
                        "source_key": contribution.source_key,
                        "source_version": contribution.source_version,
                        "source_rank": contribution.source_rank,
                        "raw_score": contribution.raw_score,
                        "provenance": contribution.provenance,
                    }
                    for contribution in item.contributions
                ],
                "artist_key": item.artist_key,
                "release_key": item.release_key,
                "section": item.section,
            }
            for item in ranking
        ],
    }


def recommendation_ranking_sha256(ranking: tuple[RankedRecommendation, ...]) -> str:
    """Hash one complete P11 ranking using all persisted public item fields."""

    return sha256(rfc8785.dumps(recommendation_ranking_document(ranking))).hexdigest()


def recommendation_ranking_document(
    ranking: tuple[RankedRecommendation, ...],
) -> dict[str, JsonValue]:
    """Serialize one complete persisted P11 ranking for audit evidence."""

    document = _postprocessed_ranking_document(ranking)
    document["kind"] = "RECOMMENDATION_RANKING_V1"
    return document


def _postprocessed_ranking_from_document(
    value: object,
) -> tuple[RankedRecommendation, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or set(value) != {"schema_version", "kind", "items"}:
        raise ValueError("Sona shadow postprocessed ranking document is invalid")
    if (
        _integer(value, "schema_version") != 1
        or _string(value, "kind") != "SONA_POSTPROCESSED_RANKING_V1"
    ):
        raise ValueError("Sona shadow postprocessed ranking schema is unsupported")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Sona shadow postprocessed ranking items are invalid")
    items: list[RankedRecommendation] = []
    expected_item_keys = {
        "recording_id",
        "source_rank",
        "score",
        "reason_code",
        "reason_codes",
        "contributions",
        "artist_key",
        "release_key",
        "section",
    }
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or set(raw_item) != expected_item_keys:
            raise ValueError("Sona shadow postprocessed ranking item is invalid")
        raw_contributions = raw_item.get("contributions")
        if not isinstance(raw_contributions, list) or not raw_contributions:
            raise ValueError("Sona shadow postprocessed ranking contributions are invalid")
        contributions: list[CandidateContribution] = []
        for raw_contribution in raw_contributions:
            expected_contribution_keys = {
                "source_key",
                "source_version",
                "source_rank",
                "raw_score",
                "provenance",
            }
            if (
                not isinstance(raw_contribution, dict)
                or set(raw_contribution) != expected_contribution_keys
            ):
                raise ValueError("Sona shadow postprocessed contribution is invalid")
            provenance = raw_contribution.get("provenance")
            if not isinstance(provenance, dict):
                raise ValueError("Sona shadow postprocessed provenance is invalid")
            contributions.append(
                CandidateContribution(
                    _string(raw_contribution, "source_key"),
                    _string(raw_contribution, "source_version"),
                    _integer(raw_contribution, "source_rank"),
                    _number(raw_contribution, "raw_score"),
                    cast(dict[str, JsonValue], provenance),
                )
            )
        raw_reason_codes = raw_item.get("reason_codes")
        if not isinstance(raw_reason_codes, list) or any(
            not isinstance(reason, str) for reason in raw_reason_codes
        ):
            raise ValueError("Sona shadow postprocessed reason codes are invalid")
        release_key = raw_item.get("release_key")
        if release_key is not None and not isinstance(release_key, str):
            raise ValueError("Sona shadow postprocessed release key is invalid")
        items.append(
            RankedRecommendation(
                recording_id=_uuid(raw_item, "recording_id"),
                source_rank=_integer(raw_item, "source_rank"),
                score=_number(raw_item, "score"),
                reason_code=_string(raw_item, "reason_code"),
                reason_codes=tuple(cast(list[str], raw_reason_codes)),
                contributions=tuple(contributions),
                artist_key=_string(raw_item, "artist_key"),
                release_key=release_key,
                section=_string(raw_item, "section"),
            )
        )
    if len({item.recording_id for item in items}) != len(items):
        raise ValueError("Sona shadow postprocessed ranking contains duplicate recordings")
    return tuple(items)


def _generated_expansion_from_document(
    value: object,
) -> tuple[tuple[SonaSemanticId, tuple[UUID, ...]], ...]:
    if not isinstance(value, list):
        raise ValueError("Sona shadow generated expansion is invalid")
    expansion: list[tuple[SonaSemanticId, tuple[UUID, ...]]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"semantic_id", "recording_ids"}:
            raise ValueError("Sona shadow generated expansion item is invalid")
        raw_sid = raw.get("semantic_id")
        raw_recordings = raw.get("recording_ids")
        if (
            not isinstance(raw_sid, list)
            or len(raw_sid) != 3
            or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_sid)
            or not isinstance(raw_recordings, list)
            or any(not isinstance(item, str) for item in raw_recordings)
        ):
            raise ValueError("Sona shadow generated expansion values are invalid")
        try:
            semantic_id = SonaSemanticId(*cast(list[int], raw_sid))
            recording_ids = tuple(UUID(item) for item in cast(list[str], raw_recordings))
        except (TypeError, ValueError) as error:
            raise ValueError("Sona shadow generated expansion values are invalid") from error
        expansion.append((semantic_id, recording_ids))
    return tuple(expansion)


def _number(value: Mapping[str, object], key: str) -> float:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (float, int)) or not isfinite(item):
        raise ValueError(f"Sona shadow {key} must be a finite number")
    return float(item)


def _string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"Sona shadow {key} must be a string")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ValueError(f"Sona shadow {key} must be a string or null")
    return item


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"Sona shadow {key} must be an integer")
    return item


def _uuid(value: Mapping[str, object], key: str) -> UUID:
    try:
        return UUID(_string(value, key))
    except ValueError as error:
        raise ValueError(f"Sona shadow {key} must be a UUID") from error


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_string(value, key))
    except ValueError as error:
        raise ValueError(f"Sona shadow {key} must be RFC 3339 time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Sona shadow {key} must be timezone-aware")
    return parsed


__all__ = (
    "SonaShadowCoordinator",
    "build_sona_shadow_evidence",
    "recommendation_ranking_document",
    "recommendation_ranking_sha256",
    "sona_shadow_evidence_document",
    "sona_shadow_evidence_from_document",
)

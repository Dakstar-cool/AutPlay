"""Paired P11/Sona offline metrics and exact R1B quality-gate verdicts."""

from __future__ import annotations

import json
import math
import struct
from ast import literal_eval
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from datetime import UTC
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, Final, cast
from uuid import UUID

import onnx
import rfc8785

from autplay.application.recommendations import (
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    request_document,
)
from autplay.application.sona import sona_inference_request_document
from autplay.application.sona_shadow import (
    recommendation_ranking_document,
    recommendation_ranking_sha256,
    sona_shadow_evidence_document,
)
from autplay.domain.recommendations import (
    JsonValue,
    RankedRecommendation,
    RecommendationInputSnapshot,
    RecommendationResponse,
)
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    SONA_MAX_HISTORY_EVENTS,
    SONA_RANKING_HEADS,
    SONA_SID_DEPTH,
    SonaInferenceRequest,
    SonaShadowEvidence,
    SonaShadowStatus,
)
from autplay.domain.sona_approval import (
    SONA_APPROVAL_MAX_BYTES,
    VerifiedSonaArtifactApproval,
    VerifiedSonaDatasetApproval,
    VerifiedSonaSourceApproval,
    verify_sona_artifact_approval,
    verify_sona_dataset_approval,
    verify_sona_evaluation_approval,
    verify_sona_source_approval,
)
from autplay.domain.sona_trust import load_deployment_sona_reviewer_trust_anchor

SONA_EVALUATION_SCHEMA_VERSION: Final = 1
SONA_EVALUATION_K: Final = 10
SONA_MAX_NDCG_REGRESSION: Final = 0.01
SONA_MAX_DIVERSITY_REGRESSION: Final = 0.02
SONA_MAX_ARTIST_CONCENTRATION_INCREASE: Final = 0.02
SONA_MAX_REPEAT_RATE_INCREASE: Final = 0.02
SONA_MAX_P95_LATENCY_MS: Final = 300.0
SONA_MAX_P95_LATENCY_RATIO: Final = 1.25
SONA_CONTRACT_POLICY_SHA256: Final = (
    "c21308922e78fc58a1f5b7297f52e120ac5893662f165767f0f24921bdc4ea22"
)
POSITIVE_SIGNALS: Final = ("selection", "completion", "like")
NEGATIVE_SIGNALS: Final = ("skip", "dislike")
ALL_SIGNALS: Final = (*POSITIVE_SIGNALS, *NEGATIVE_SIGNALS)
SONA_LABEL_ATTRIBUTION_WINDOW_MS: Final = 7 * 24 * 60 * 60 * 1_000
SONA_EVALUATION_REPORT_MAX_BYTES: Final = 4 * 1024 * 1024
SONA_SAFETY_EVIDENCE_MAX_BYTES: Final = 4 * 1024 * 1024
SONA_PERFORMANCE_EVIDENCE_MAX_BYTES: Final = 64 * 1024 * 1024
SONA_OUTCOME_EVIDENCE_MAX_BYTES: Final = 64 * 1024 * 1024
SONA_EXECUTION_EVIDENCE_MAX_BYTES: Final = 64 * 1024 * 1024
SONA_ONNX_INPUT_NAMES: Final = (
    "history_sids",
    "history_actions",
    "history_origins",
    "history_age_buckets",
    "history_mask",
    "candidate_sids",
    "candidate_mask",
    "seed",
)
SONA_ONNX_OUTPUT_NAMES: Final = (
    "generated_sids",
    "generated_log_probabilities",
    "ranking_head_scores",
    "ranking_scores",
)
SONA_DIRECTIONAL_EXPECTED_SHA256: Final = {
    "bounded-owner-export": ("b8468794e2afe3651aad4f36e82a4ea3c018659296bfe7961dcc074e83f5f71f"),
    "cold-start-high-bounded-plasticity": (
        "f1e42da81167a7f805c2a52a2e0ba61c8f42ced5c9b6b13a4cb5d33c5a033808"
    ),
    "contradictory-evidence-lowers-confidence-first": (
        "86f0b54c484ecacd4f98172c4cc15d3e364ccaad9b0891a5572df5d0a64fdb97"
    ),
    "delayed-sync-does-not-rewrite-snapshot": (
        "3457a18ed31d8d9d071448e9dc1514b55543c3a4f9fd50b889b8e4cc5246f849"
    ),
    "device-unbinding-purges-local-context-only": (
        "2bd946e9ef2f4a3a911662b0124113644e4789542969fcd69302a436191a0dc8"
    ),
    "explicit-dislike-remains-mandatory-filter": (
        "fe4fbb362a68020a9cb6b7990de040f8b8c608fd5bd051d0b151f54fba8ecba5"
    ),
    "fading-interest-negative-momentum": (
        "d134d694fdd724d8b1ceaa11c539d215cfafb3e95ed6ba2866b47650eebed957"
    ),
    "fatigue-recovers-with-expiry-and-completion": (
        "b866d5699aa107e26887a337e3a57b9540d00fb25634a28746f54520e2bbfe29"
    ),
    "future-clock-skew-clamps-and-reduces-confidence": (
        "f7ab9a72d285c029fabbabd7c035158db9f2c3b4263659896ee7040841538ac5"
    ),
    "maturity-growth-reduces-single-event-influence": (
        "d82612ff9a462e6dd4f91db4c23a3d2726522b2b1115863be208b50e3bbeb936"
    ),
    "overlapping-horizons-conserve-event-mass": (
        "ee813549a1cada04e4551f8d71f59159078072e504cc5a7d5d58b4a0cb322fec"
    ),
    "partial-active-horizons-renormalize-mass": (
        "29fbddb0f9a32c1a81fc83a2d3240e5abc63e9e5ec7435d79fb1599a2ad185e2"
    ),
    "past-clock-skew-disables-recent-only": (
        "8a730b5bd36d616fc4e6c485372e937b122cfb673a215901223784de148dce66"
    ),
    "privacy-delete-dead-device-does-not-claim-wipe": (
        "1e5d867be52e591e658e85b64797e646dc04ad65166422a7596b9e1d26aba2fc"
    ),
    "privacy-delete-offline-device-awaits-receipt-or-local-erasure": (
        "0fb05da329afb68793594ac362be9c2ad52b21a776f28b943e1d23d1d395aca6"
    ),
    "privacy-deletion-cascades-and-denies-replay": (
        "6d7f3e3578db7f6bff51a4aaac6614b4f7662efd903b4f43420062671ebfb556"
    ),
    "privacy-restore-reapplies-active-deletion-tombstone": (
        "813793e143db07e4363751927c02f10771a24c1c8807118da3a193c3b727b5de"
    ),
    "recommendation-causal-chain-conserves-intent-mass": (
        "ad0bb674ec21b728f82dd8b02d3b97034cb002a0b2cabbdf2aa9f163911102e5"
    ),
    "recommendation-causal-chain-normalizes-over-budget-mass": (
        "22180c9c847ebed223f8af3ab3c1bedf34aafff778188c5f418ff587dd125b72"
    ),
    "recommendation-feedback-never-becomes-organic": (
        "d0772e581d8f3e6f9994eec04ba5be7ac4bc8e8df7aba068ee28b8a1a06b2fc9"
    ),
    "retention-expiry-blocks-algorithmic-replay": (
        "35e4fb60e62e279528ba1370eb5a3b5f531735fa29d6421e2173e151ab30aedb"
    ),
    "rising-interest-positive-momentum": (
        "80acacdf53e037a00a9292a03be5ad5004028e13e1b8dd1eb842ae723b2c96af"
    ),
    "short-listens-raise-temporary-fatigue": (
        "fc204e4fcb6907d8b21bc8f39e42cd6577ab7c9373ce07435c4b190163595d21"
    ),
    "truncated-export-remains-open": (
        "3ca4bebcf5c5da6af0225ee4549d50f2ec17f106e7068282ff0ce34f4bf577c4"
    ),
}
REQUIRED_DIRECTIONAL_SCENARIOS: Final = frozenset(SONA_DIRECTIONAL_EXPECTED_SHA256)
SONA_SAFETY_GATE_IDS: Final = (
    "mandatory_filter",
    "owner_scope",
    "shadow_impression",
    "replay_mismatch",
    "fallback_unavailable",
    "privacy_deletion",
    "privacy_export",
    "device_unbinding",
)
SONA_SAFETY_GATE_ARTIFACT_SPECS: Final = {
    gate_id: (f"SONA_{gate_id.upper()}_AUDIT_V1", 1) for gate_id in SONA_SAFETY_GATE_IDS
}
_PAIRED_CASE_FACTORY_TOKEN: Final = object()


@dataclass(frozen=True, slots=True)
class EvaluationTrackMetadata:
    recording_id: UUID
    artist_key: str
    genre_keys: tuple[str, ...]
    prior_play_count: int
    prior_recommendation_count_7d: int

    def __post_init__(self) -> None:
        if (
            not self.artist_key
            or not self.genre_keys
            or tuple(sorted(set(self.genre_keys))) != self.genre_keys
            or self.prior_play_count < 0
            or self.prior_recommendation_count_7d < 0
        ):
            raise ValueError("Sona evaluation track metadata is invalid")


@dataclass(frozen=True, slots=True)
class AttributedRelevance:
    selection: frozenset[UUID] = frozenset()
    completion: frozenset[UUID] = frozenset()
    skip: frozenset[UUID] = frozenset()
    like: frozenset[UUID] = frozenset()
    dislike: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class AttributedOutcome:
    """One causal held-out label bound to the evaluated request and source rank."""

    event_sha256: str
    recording_id: UUID
    signal: str
    source_request_sha256: str
    source_rank: int
    observed_at_ms: int

    def __post_init__(self) -> None:
        _validate_sha256(self.event_sha256, "event_sha256")
        _validate_sha256(self.source_request_sha256, "source_request_sha256")
        if self.signal not in ALL_SIGNALS or self.source_rank < 1 or self.observed_at_ms < 0:
            raise ValueError("Sona attributed outcome is invalid")


@dataclass(frozen=True, slots=True)
class PairedRankingCaseEvidence:
    """Raw persisted sources from which the evaluator rebuilds one paired case."""

    case_id: str
    owner_lineage_token: str
    baseline_response: RecommendationResponse
    baseline_snapshot: RecommendationInputSnapshot
    sona_request: SonaInferenceRequest
    shadow_evidence: SonaShadowEvidence
    outcome_documents: tuple[Mapping[str, JsonValue], ...]


def sona_paired_execution_evidence_document(
    case_evidence: tuple[PairedRankingCaseEvidence, ...],
) -> bytes:
    """Serialize and re-verify the exact persisted inputs for paired execution."""

    if not case_evidence:
        raise ValueError("Sona paired execution evidence is empty")
    cases = [_paired_execution_case_document(value) for value in case_evidence]
    return rfc8785.dumps(
        {
            "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
            "kind": "SONA_PAIRED_EXECUTION_EVIDENCE_BUNDLE_V1",
            "cases": cases,
        }
    )


def _paired_execution_case_document(
    evidence: PairedRankingCaseEvidence,
) -> dict[str, JsonValue]:
    trace = evidence.baseline_response.request
    snapshot = evidence.baseline_snapshot
    reference = snapshot.reference
    expected_request = request_document(trace.query, trace.pipeline, reference)
    input_snapshot = recommendation_input_snapshot_document(
        trace.query.user_id,
        interaction_watermark=reference.interaction_watermark,
        tracks=snapshot.tracks,
    )
    availability_snapshot = recommendation_availability_snapshot_document(snapshot.tracks)
    policy_snapshot = recommendation_policy_snapshot_document(snapshot.tracks)
    catalog_snapshot = max(
        (max(track.added_at_ms, track.last_played_at_ms or 0) for track in snapshot.tracks),
        default=0,
    )
    ranking = recommendation_ranking_document(evidence.baseline_response.items)
    sona_request = sona_inference_request_document(evidence.sona_request)
    shadow = sona_shadow_evidence_document(evidence.shadow_evidence)
    if (
        trace.canonical_request != expected_request
        or trace.request_sha256 != sha256(rfc8785.dumps(expected_request)).hexdigest()
        or reference.input_snapshot_sha256 != sha256(rfc8785.dumps(input_snapshot)).hexdigest()
        or reference.availability_snapshot
        != sha256(rfc8785.dumps(availability_snapshot)).hexdigest()
        or reference.policy_snapshot_sha256 != sha256(rfc8785.dumps(policy_snapshot)).hexdigest()
        or reference.catalog_snapshot != catalog_snapshot
        or evidence.sona_request.request_sha256 != sha256(rfc8785.dumps(sona_request)).hexdigest()
        or evidence.shadow_evidence.evidence_sha256
        != sha256(
            rfc8785.dumps(
                cast(
                    dict[str, JsonValue],
                    {key: value for key, value in shadow.items() if key != "evidence_sha256"},
                )
            )
        ).hexdigest()
        or evidence.shadow_evidence.p11_ranking_sha256 != sha256(rfc8785.dumps(ranking)).hexdigest()
    ):
        raise ValueError("Sona paired execution persisted input binding is invalid")
    if trace.created_at.tzinfo is None or snapshot.retained_until.tzinfo is None:
        raise ValueError("Sona paired execution timestamps must be timezone-aware")
    return {
        "case_id": evidence.case_id,
        "owner_lineage_token": evidence.owner_lineage_token,
        "p11_request": {
            "recommendation_request_id": str(trace.recommendation_request_id),
            "request_sha256": trace.request_sha256,
            "canonical_request": trace.canonical_request,
            "created_at": trace.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
        },
        "baseline_snapshot": {
            "snapshot_id": str(reference.snapshot_id),
            "input_snapshot_sha256": reference.input_snapshot_sha256,
            "availability_snapshot_sha256": reference.availability_snapshot,
            "policy_snapshot_sha256": reference.policy_snapshot_sha256,
            "catalog_snapshot": reference.catalog_snapshot,
            "retained_until": snapshot.retained_until.astimezone(UTC).isoformat(
                timespec="microseconds"
            ),
            "document": input_snapshot,
        },
        "p11_ranking": ranking,
        "sona_request": sona_request,
        "sona_shadow_evidence": shadow,
    }


@dataclass(frozen=True, slots=True)
class PairedRankingCase:
    case_id: str
    owner_lineage_token: str
    request_sha256: str
    baseline_snapshot_sha256: str
    temporal_snapshot_sha256: str
    p11_pipeline_manifest_sha256: str
    sona_pipeline_manifest_sha256: str
    sona_shadow_evidence_sha256: str
    p11_ranking_sha256: str
    sona_postprocessed_ranking_sha256: str
    cutoff_at_ms: int
    baseline_ranked_ids: tuple[UUID, ...]
    sona_ranked_ids: tuple[UUID, ...]
    sona_generated_ids: tuple[UUID, ...]
    tracks: tuple[EvaluationTrackMetadata, ...]
    outcomes: tuple[AttributedOutcome, ...]
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _PAIRED_CASE_FACTORY_TOKEN:
            raise ValueError("Sona evaluation case must be built from verified source evidence")
        if not self.case_id or not self.tracks:
            raise ValueError("Sona evaluation case identity or catalog is empty")
        for field_name in (
            "owner_lineage_token",
            "request_sha256",
            "baseline_snapshot_sha256",
            "temporal_snapshot_sha256",
            "p11_pipeline_manifest_sha256",
            "sona_pipeline_manifest_sha256",
            "sona_shadow_evidence_sha256",
            "p11_ranking_sha256",
            "sona_postprocessed_ranking_sha256",
        ):
            _validate_sha256(getattr(self, field_name), field_name)
        if self.cutoff_at_ms < 0 or not self.outcomes:
            raise ValueError("Sona evaluation cutoff or outcomes are invalid")
        track_ids = {track.recording_id for track in self.tracks}
        if len(track_ids) != len(self.tracks):
            raise ValueError("Sona evaluation case track metadata is duplicated")
        for ranked in (
            self.baseline_ranked_ids,
            self.sona_ranked_ids,
            self.sona_generated_ids,
        ):
            if len(ranked) != len(set(ranked)) or any(value not in track_ids for value in ranked):
                raise ValueError("Sona evaluation ranking is duplicate or outside its snapshot")
        if not self.baseline_ranked_ids or not self.sona_ranked_ids or not self.sona_generated_ids:
            raise ValueError("Sona evaluation ranking or generated pool is empty")
        if not set(self.sona_ranked_ids) <= set(self.sona_generated_ids):
            raise ValueError("Sona postprocessed ranking escapes its generated pool")
        if len({outcome.event_sha256 for outcome in self.outcomes}) != len(self.outcomes):
            raise ValueError("Sona evaluation outcome event is duplicated")
        if any(
            outcome.recording_id not in track_ids
            or outcome.source_request_sha256 != self.request_sha256
            or outcome.source_rank > len(self.baseline_ranked_ids)
            or self.baseline_ranked_ids[outcome.source_rank - 1] != outcome.recording_id
            or not self.cutoff_at_ms
            < outcome.observed_at_ms
            <= self.cutoff_at_ms + SONA_LABEL_ATTRIBUTION_WINDOW_MS
            for outcome in self.outcomes
        ):
            raise ValueError("Sona evaluation outcome escapes causal attribution bounds")

    @property
    def relevance(self) -> AttributedRelevance:
        values = {
            signal: frozenset(
                outcome.recording_id for outcome in self.outcomes if outcome.signal == signal
            )
            for signal in ALL_SIGNALS
        }
        return AttributedRelevance(**values)


def build_paired_ranking_case(
    *,
    case_id: str,
    owner_lineage_token: str,
    baseline_response: RecommendationResponse,
    baseline_snapshot: RecommendationInputSnapshot,
    sona_request: SonaInferenceRequest,
    shadow_evidence: SonaShadowEvidence,
    outcome_documents: tuple[Mapping[str, JsonValue], ...],
) -> PairedRankingCase:
    """Derive an evaluation case from the persisted P11 response and Sona shadow evidence."""

    trace = baseline_response.request
    sona_shadow_evidence_document(shadow_evidence)
    if (
        shadow_evidence.status is not SonaShadowStatus.SUCCEEDED
        or shadow_evidence.output is None
        or shadow_evidence.postprocessed_ranking_sha256 is None
        or trace.query.user_id != sona_request.owner_user_id
        or trace.query.user_id != shadow_evidence.owner_user_id
        or trace.recommendation_request_id != shadow_evidence.recommendation_request_id
        or trace.request_sha256 != shadow_evidence.p11_request_sha256
        or trace.pipeline.manifest_sha256 != shadow_evidence.p11_pipeline_manifest_sha256
        or trace.snapshot.snapshot_id != sona_request.baseline_snapshot_id
        or trace.snapshot.snapshot_id != shadow_evidence.baseline_snapshot_id
        or trace.snapshot.input_snapshot_sha256 != shadow_evidence.baseline_input_snapshot_sha256
        or sona_request.temporal_snapshot_id != shadow_evidence.temporal_snapshot_id
        or sona_request.request_sha256
        != sha256(rfc8785.dumps(sona_inference_request_document(sona_request))).hexdigest()
        or sona_request.request_sha256 != shadow_evidence.sona_request_sha256
        or sona_request.model_manifest_sha256 != shadow_evidence.model_manifest_sha256
        or sona_request.tokenizer_sha256 != shadow_evidence.tokenizer_sha256
        or recommendation_ranking_sha256(baseline_response.items)
        != shadow_evidence.p11_ranking_sha256
        or baseline_snapshot.reference != trace.snapshot
        or {track.recording_id for track in baseline_snapshot.tracks}
        != {candidate.recording_id for candidate in sona_request.candidates}
    ):
        raise ValueError("Sona evaluation case source evidence binding is invalid")
    baseline_ids = tuple(value.recording_id for value in baseline_response.items)
    sona_ids = tuple(value.recording_id for value in shadow_evidence.postprocessed_ranking)
    tracks = tuple(
        EvaluationTrackMetadata(
            track.recording_id,
            track.artist_key,
            tuple(sorted(set(track.metadata_tokens))) or ("unknown",),
            track.play_count,
            track.recommended_play_count,
        )
        for track in sorted(baseline_snapshot.tracks, key=lambda value: value.recording_id.hex)
    )
    outcomes = _attributed_outcomes_from_documents(outcome_documents)
    return PairedRankingCase(
        case_id=case_id,
        owner_lineage_token=owner_lineage_token,
        request_sha256=sona_request.request_sha256,
        baseline_snapshot_sha256=trace.snapshot.input_snapshot_sha256,
        temporal_snapshot_sha256=shadow_evidence.temporal_snapshot_sha256,
        p11_pipeline_manifest_sha256=trace.pipeline.manifest_sha256,
        sona_pipeline_manifest_sha256=shadow_evidence.shadow_pipeline_manifest_sha256,
        sona_shadow_evidence_sha256=shadow_evidence.evidence_sha256,
        p11_ranking_sha256=compute_recommendation_ranking_sha256(baseline_response.items),
        sona_postprocessed_ranking_sha256=shadow_evidence.postprocessed_ranking_sha256,
        cutoff_at_ms=sona_request.cutoff_at_ms,
        baseline_ranked_ids=baseline_ids,
        sona_ranked_ids=sona_ids,
        sona_generated_ids=shadow_evidence.generated_recording_ids,
        tracks=tracks,
        outcomes=outcomes,
        _factory_token=_PAIRED_CASE_FACTORY_TOKEN,
    )


def _build_paired_ranking_cases(
    evidence: tuple[PairedRankingCaseEvidence, ...],
) -> tuple[PairedRankingCase, ...]:
    if not evidence:
        raise ValueError("Sona paired evaluation source evidence is empty")
    return tuple(
        build_paired_ranking_case(
            case_id=value.case_id,
            owner_lineage_token=value.owner_lineage_token,
            baseline_response=value.baseline_response,
            baseline_snapshot=value.baseline_snapshot,
            sona_request=value.sona_request,
            shadow_evidence=value.shadow_evidence,
            outcome_documents=value.outcome_documents,
        )
        for value in evidence
    )


def _attributed_outcomes_from_documents(
    documents: tuple[Mapping[str, JsonValue], ...],
) -> tuple[AttributedOutcome, ...]:
    expected_keys = {
        "schema_version",
        "kind",
        "recording_id",
        "signal",
        "source_request_sha256",
        "source_rank",
        "observed_at_ms",
    }
    outcomes: list[AttributedOutcome] = []
    for raw in documents:
        document = dict(raw)
        if (
            set(document) != expected_keys
            or document.get("schema_version") != 1
            or document.get("kind") != "SONA_ATTRIBUTED_OUTCOME_V1"
        ):
            raise ValueError("Sona attributed outcome document schema is invalid")
        recording_id = document.get("recording_id")
        signal = document.get("signal")
        source_request_sha256 = document.get("source_request_sha256")
        source_rank = document.get("source_rank")
        observed_at_ms = document.get("observed_at_ms")
        if (
            not isinstance(recording_id, str)
            or not isinstance(signal, str)
            or not isinstance(source_request_sha256, str)
            or not isinstance(source_rank, int)
            or isinstance(source_rank, bool)
            or not isinstance(observed_at_ms, int)
            or isinstance(observed_at_ms, bool)
        ):
            raise ValueError("Sona attributed outcome document values are invalid")
        outcomes.append(
            AttributedOutcome(
                event_sha256=sha256(rfc8785.dumps(document)).hexdigest(),
                recording_id=UUID(recording_id),
                signal=signal,
                source_request_sha256=source_request_sha256,
                source_rank=source_rank,
                observed_at_ms=observed_at_ms,
            )
        )
    return tuple(outcomes)


@dataclass(frozen=True, slots=True)
class DirectionalScenarioEvidence:
    """One scenario result derived from canonical expected and actual documents."""

    case_id: str
    actual_document_rfc8785: bytes
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.case_id not in REQUIRED_DIRECTIONAL_SCENARIOS:
            raise ValueError("Sona directional scenario identity is unsupported")
        if not 1 <= len(self.actual_document_rfc8785) <= 65_536:
            raise ValueError("Sona directional scenario actual document is invalid")
        try:
            actual = cast(JsonValue, json.loads(self.actual_document_rfc8785))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Sona directional scenario actual document is invalid") from error
        if not isinstance(actual, dict) or rfc8785.dumps(actual) != self.actual_document_rfc8785:
            raise ValueError("Sona directional scenario actual document is not canonical")
        _validate_sha256(self.evidence_sha256, "evidence_sha256")
        if (
            self.evidence_sha256
            != sha256(
                rfc8785.dumps(
                    {
                        "case_id": self.case_id,
                        "expected_sha256": self.expected_sha256,
                        "actual_sha256": self.actual_sha256,
                    }
                )
            ).hexdigest()
        ):
            raise ValueError("Sona directional scenario evidence hash mismatch")

    @classmethod
    def from_actual_document(
        cls, case_id: str, actual_document: Mapping[str, JsonValue]
    ) -> DirectionalScenarioEvidence:
        """Bind an executor-produced result to the repository-pinned expected vector."""

        if case_id not in SONA_DIRECTIONAL_EXPECTED_SHA256:
            raise ValueError("Sona directional scenario identity is unsupported")
        expected_sha256 = SONA_DIRECTIONAL_EXPECTED_SHA256[case_id]
        actual_sha256 = sha256(rfc8785.dumps(dict(actual_document))).hexdigest()
        evidence_sha256 = sha256(
            rfc8785.dumps(
                {
                    "case_id": case_id,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                }
            )
        ).hexdigest()
        return cls(case_id, rfc8785.dumps(dict(actual_document)), evidence_sha256)

    @property
    def expected_sha256(self) -> str:
        return SONA_DIRECTIONAL_EXPECTED_SHA256[self.case_id]

    @property
    def actual_sha256(self) -> str:
        return sha256(self.actual_document_rfc8785).hexdigest()

    @property
    def passed(self) -> bool:
        return self.expected_sha256 == self.actual_sha256


@dataclass(frozen=True, slots=True)
class SonaSafetyGateEvidence:
    gate_id: str
    artifact_kind: str
    artifact_schema_version: int
    artifact_sha256: str
    covered_request_sha256s: tuple[str, ...]
    violation_event_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.gate_id not in SONA_SAFETY_GATE_ARTIFACT_SPECS
            or (self.artifact_kind, self.artifact_schema_version)
            != SONA_SAFETY_GATE_ARTIFACT_SPECS.get(self.gate_id)
            or not self.covered_request_sha256s
            or self.covered_request_sha256s != tuple(sorted(set(self.covered_request_sha256s)))
            or self.violation_event_sha256s != tuple(sorted(set(self.violation_event_sha256s)))
        ):
            raise ValueError("Sona safety gate evidence is invalid")
        _validate_sha256(self.artifact_sha256, "safety artifact sha256")
        for digest in (*self.covered_request_sha256s, *self.violation_event_sha256s):
            _validate_sha256(digest, "safety covered request sha256")
        document: dict[str, JsonValue] = {
            "schema_version": self.artifact_schema_version,
            "kind": self.artifact_kind,
            "gate_id": self.gate_id,
            "covered_request_sha256s": list(self.covered_request_sha256s),
            "violation_event_sha256s": list(self.violation_event_sha256s),
        }
        if self.artifact_sha256 != sha256(rfc8785.dumps(document)).hexdigest():
            raise ValueError("Sona safety gate artifact hash mismatch")

    @classmethod
    def from_audit(
        cls,
        gate_id: str,
        *,
        covered_request_sha256s: tuple[str, ...],
        violation_event_sha256s: tuple[str, ...] = (),
    ) -> SonaSafetyGateEvidence:
        if gate_id not in SONA_SAFETY_GATE_ARTIFACT_SPECS:
            raise ValueError("Sona safety gate identity is unsupported")
        artifact_kind, schema_version = SONA_SAFETY_GATE_ARTIFACT_SPECS[gate_id]
        document: dict[str, JsonValue] = {
            "schema_version": schema_version,
            "kind": artifact_kind,
            "gate_id": gate_id,
            "covered_request_sha256s": list(covered_request_sha256s),
            "violation_event_sha256s": list(violation_event_sha256s),
        }
        return cls(
            gate_id,
            artifact_kind,
            schema_version,
            sha256(rfc8785.dumps(document)).hexdigest(),
            covered_request_sha256s,
            violation_event_sha256s,
        )

    @property
    def violation_count(self) -> int:
        return len(self.violation_event_sha256s)


@dataclass(frozen=True, slots=True)
class SonaSafetyEvidence:
    checks: tuple[SonaSafetyGateEvidence, ...]

    def __post_init__(self) -> None:
        if tuple(value.gate_id for value in self.checks) != SONA_SAFETY_GATE_IDS:
            raise ValueError("Sona evaluation safety gate set or ordering is invalid")

    @property
    def total(self) -> int:
        return sum(value.violation_count for value in self.checks)


@dataclass(frozen=True, slots=True)
class PairedLatencySample:
    case_id: str
    request_sha256: str
    baseline_latency_ms: float
    sona_latency_ms: float

    def __post_init__(self) -> None:
        _validate_sha256(self.request_sha256, "latency request_sha256")
        if (
            not self.case_id
            or not math.isfinite(self.baseline_latency_ms)
            or self.baseline_latency_ms < 0.0
            or not math.isfinite(self.sona_latency_ms)
            or self.sona_latency_ms < 0.0
        ):
            raise ValueError("Sona paired latency sample is invalid")


@dataclass(frozen=True, slots=True)
class SonaPerformanceEvidence:
    samples: tuple[PairedLatencySample, ...]
    benchmark_run_sha256: str
    ort_benchmark_evidence_sha256: str
    environment_sha256: str
    checkpoint_manifest_sha256: str
    artifact_sha256: str
    tokenizer_manifest_sha256: str
    warmup_iterations: int
    measured_iterations_per_case: int
    dataset_storage_bytes: int
    artifact_storage_bytes: int
    dataset_storage_manifest_sha256: str
    artifact_storage_manifest_sha256: str

    def __post_init__(self) -> None:
        if (
            not self.samples
            or self.warmup_iterations < 1
            or self.measured_iterations_per_case < 1
            or self.dataset_storage_bytes < 0
            or self.artifact_storage_bytes < 0
        ):
            raise ValueError("Sona performance evidence is invalid")
        for field_name in (
            "benchmark_run_sha256",
            "ort_benchmark_evidence_sha256",
            "environment_sha256",
            "checkpoint_manifest_sha256",
            "artifact_sha256",
            "tokenizer_manifest_sha256",
            "dataset_storage_manifest_sha256",
            "artifact_storage_manifest_sha256",
        ):
            _validate_sha256(getattr(self, field_name), field_name)
        if self.benchmark_run_sha256 != _performance_run_sha256(
            samples=self.samples,
            ort_benchmark_evidence_sha256=self.ort_benchmark_evidence_sha256,
            environment_sha256=self.environment_sha256,
            checkpoint_manifest_sha256=self.checkpoint_manifest_sha256,
            artifact_sha256=self.artifact_sha256,
            tokenizer_manifest_sha256=self.tokenizer_manifest_sha256,
            warmup_iterations=self.warmup_iterations,
            measured_iterations_per_case=self.measured_iterations_per_case,
            dataset_storage_bytes=self.dataset_storage_bytes,
            artifact_storage_bytes=self.artifact_storage_bytes,
            dataset_storage_manifest_sha256=self.dataset_storage_manifest_sha256,
            artifact_storage_manifest_sha256=self.artifact_storage_manifest_sha256,
        ):
            raise ValueError("Sona benchmark run hash mismatch")

    @classmethod
    def from_benchmark(
        cls,
        *,
        samples: tuple[PairedLatencySample, ...],
        ort_benchmark_evidence_sha256: str,
        environment_sha256: str,
        checkpoint_manifest_sha256: str,
        artifact_sha256: str,
        tokenizer_manifest_sha256: str,
        warmup_iterations: int,
        measured_iterations_per_case: int,
        dataset_storage_bytes: int,
        artifact_storage_bytes: int,
        dataset_storage_manifest_sha256: str,
        artifact_storage_manifest_sha256: str,
    ) -> SonaPerformanceEvidence:
        run_sha256 = _performance_run_sha256(
            samples=samples,
            ort_benchmark_evidence_sha256=ort_benchmark_evidence_sha256,
            environment_sha256=environment_sha256,
            checkpoint_manifest_sha256=checkpoint_manifest_sha256,
            artifact_sha256=artifact_sha256,
            tokenizer_manifest_sha256=tokenizer_manifest_sha256,
            warmup_iterations=warmup_iterations,
            measured_iterations_per_case=measured_iterations_per_case,
            dataset_storage_bytes=dataset_storage_bytes,
            artifact_storage_bytes=artifact_storage_bytes,
            dataset_storage_manifest_sha256=dataset_storage_manifest_sha256,
            artifact_storage_manifest_sha256=artifact_storage_manifest_sha256,
        )
        return cls(
            samples,
            run_sha256,
            ort_benchmark_evidence_sha256,
            environment_sha256,
            checkpoint_manifest_sha256,
            artifact_sha256,
            tokenizer_manifest_sha256,
            warmup_iterations,
            measured_iterations_per_case,
            dataset_storage_bytes,
            artifact_storage_bytes,
            dataset_storage_manifest_sha256,
            artifact_storage_manifest_sha256,
        )

    @property
    def baseline_latency_ms(self) -> tuple[float, ...]:
        return tuple(value.baseline_latency_ms for value in self.samples)

    @property
    def sona_latency_ms(self) -> tuple[float, ...]:
        return tuple(value.sona_latency_ms for value in self.samples)


def _performance_run_sha256(
    *,
    samples: tuple[PairedLatencySample, ...],
    ort_benchmark_evidence_sha256: str,
    environment_sha256: str,
    checkpoint_manifest_sha256: str,
    artifact_sha256: str,
    tokenizer_manifest_sha256: str,
    warmup_iterations: int,
    measured_iterations_per_case: int,
    dataset_storage_bytes: int,
    artifact_storage_bytes: int,
    dataset_storage_manifest_sha256: str,
    artifact_storage_manifest_sha256: str,
) -> str:
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "kind": "SONA_PAIRED_ARTIFACT_BENCHMARK_RUN_V1",
        "ort_benchmark_evidence_sha256": ort_benchmark_evidence_sha256,
        "environment_sha256": environment_sha256,
        "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
        "artifact_sha256": artifact_sha256,
        "tokenizer_manifest_sha256": tokenizer_manifest_sha256,
        "warmup_iterations": warmup_iterations,
        "measured_iterations_per_case": measured_iterations_per_case,
        "samples": [
            {
                "case_id": sample.case_id,
                "request_sha256": sample.request_sha256,
                "baseline_latency_ms": sample.baseline_latency_ms,
                "sona_latency_ms": sample.sona_latency_ms,
            }
            for sample in samples
        ],
        "dataset_storage_bytes": dataset_storage_bytes,
        "artifact_storage_bytes": artifact_storage_bytes,
        "dataset_storage_manifest_sha256": dataset_storage_manifest_sha256,
        "artifact_storage_manifest_sha256": artifact_storage_manifest_sha256,
    }
    return sha256(rfc8785.dumps(document)).hexdigest()


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    labeled_cases: int
    recall_at_10: float | None
    ndcg_at_10: float | None
    exposure_at_10: float | None

    def __post_init__(self) -> None:
        if self.labeled_cases < 0 or any(
            value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0)
            for value in (self.recall_at_10, self.ndcg_at_10, self.exposure_at_10)
        ):
            raise ValueError("Sona signal metrics are outside canonical bounds")


@dataclass(frozen=True, slots=True)
class SignalMetricsByName:
    values: tuple[tuple[str, SignalMetrics], ...]

    def __post_init__(self) -> None:
        if tuple(name for name, _ in self.values) != ALL_SIGNALS:
            raise ValueError("Sona signal metrics set or ordering is invalid")

    def __getitem__(self, signal: str) -> SignalMetrics:
        for name, metrics in self.values:
            if name == signal:
                return metrics
        raise KeyError(signal)

    def items(self) -> tuple[tuple[str, SignalMetrics], ...]:
        return self.values


@dataclass(frozen=True, slots=True)
class RankingMetrics:
    signal_metrics: SignalMetricsByName
    positive_ndcg_at_10: float
    coverage_at_10: float
    artist_diversity_at_10: float
    novelty_at_10: float
    repeat_rate_at_10: float
    artist_concentration_hhi_at_10: float
    genre_concentration_hhi_at_10: float
    decoder_coverage: float | None
    decoder_positive_recall: float | None

    def __post_init__(self) -> None:
        values = (
            self.positive_ndcg_at_10,
            self.coverage_at_10,
            self.artist_diversity_at_10,
            self.novelty_at_10,
            self.repeat_rate_at_10,
            self.artist_concentration_hhi_at_10,
            self.genre_concentration_hhi_at_10,
            self.decoder_coverage,
            self.decoder_positive_recall,
        )
        if any(
            value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0)
            for value in values
        ):
            raise ValueError("Sona ranking metrics are outside canonical bounds")


@dataclass(frozen=True, slots=True)
class SonaGateVerdict:
    eligible: bool
    failures: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.eligible != (not self.failures)
            or len(self.failures) != len(set(self.failures))
            or any(not value for value in self.failures)
        ):
            raise ValueError("Sona gate verdict is internally inconsistent")


@dataclass(frozen=True, slots=True)
class PairedEvaluationReport:
    evaluation_approval_sha256: str
    dataset_approval_sha256: str
    dataset_bundle_sha256: str
    checkpoint_manifest_sha256: str
    artifact_sha256: str
    artifact_manifest_sha256: str
    tokenizer_manifest_sha256: str
    p11_pipeline_manifest_sha256: str
    sona_pipeline_manifest_sha256: str
    contract_policy_sha256: str
    evaluation_case_bundle_sha256: str
    execution_evidence_bundle_sha256: str
    outcome_evidence_bundle_sha256: str
    directional_scenario_bundle_sha256: str
    safety_evidence_bundle_sha256: str
    raw_performance_evidence_sha256: str
    baseline: RankingMetrics
    sona: RankingMetrics
    safety: SonaSafetyEvidence
    directional_scenarios_passed: int
    directional_scenarios_total: int
    baseline_latency_p50_ms: float
    baseline_latency_p95_ms: float
    sona_latency_p50_ms: float
    sona_latency_p95_ms: float
    latency_ratio_p95: float
    benchmark_run_sha256: str
    ort_benchmark_evidence_sha256: str
    environment_sha256: str
    warmup_iterations: int
    measured_iterations_per_case: int
    dataset_storage_bytes: int
    artifact_storage_bytes: int
    dataset_storage_manifest_sha256: str
    artifact_storage_manifest_sha256: str
    metrics_sha256: str
    performance_sha256: str
    report_sha256: str
    verdict: SonaGateVerdict


def compute_sona_evaluation_case_bundle_sha256(
    cases: tuple[PairedRankingCase, ...],
) -> str:
    """Hash exact request/snapshot/pipeline/ranking/label evidence for every test case."""

    documents: list[JsonValue] = []
    for case in sorted(cases, key=lambda value: value.case_id):
        documents.append(
            {
                "case_id": case.case_id,
                "owner_lineage_token": case.owner_lineage_token,
                "request_sha256": case.request_sha256,
                "baseline_snapshot_sha256": case.baseline_snapshot_sha256,
                "temporal_snapshot_sha256": case.temporal_snapshot_sha256,
                "p11_pipeline_manifest_sha256": case.p11_pipeline_manifest_sha256,
                "sona_pipeline_manifest_sha256": case.sona_pipeline_manifest_sha256,
                "sona_shadow_evidence_sha256": case.sona_shadow_evidence_sha256,
                "p11_ranking_sha256": case.p11_ranking_sha256,
                "sona_postprocessed_ranking_sha256": case.sona_postprocessed_ranking_sha256,
                "cutoff_at_ms": case.cutoff_at_ms,
                "baseline_ranked_ids": [str(value) for value in case.baseline_ranked_ids],
                "sona_ranked_ids": [str(value) for value in case.sona_ranked_ids],
                "sona_generated_ids": [str(value) for value in case.sona_generated_ids],
                "tracks": [
                    {
                        "recording_id": str(track.recording_id),
                        "artist_key": track.artist_key,
                        "genre_keys": list(track.genre_keys),
                        "prior_play_count": track.prior_play_count,
                        "prior_recommendation_count_7d": track.prior_recommendation_count_7d,
                    }
                    for track in case.tracks
                ],
                "outcomes": [
                    {
                        "event_sha256": outcome.event_sha256,
                        "recording_id": str(outcome.recording_id),
                        "signal": outcome.signal,
                        "source_request_sha256": outcome.source_request_sha256,
                        "source_rank": outcome.source_rank,
                        "observed_at_ms": outcome.observed_at_ms,
                    }
                    for outcome in sorted(case.outcomes, key=lambda value: value.event_sha256)
                ],
            }
        )
    return sha256(
        rfc8785.dumps(
            {
                "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
                "kind": "SONA_EVALUATION_CASE_BUNDLE_V1",
                "cases": documents,
            }
        )
    ).hexdigest()


def sona_outcome_evidence_document(
    cases: tuple[PairedRankingCase, ...],
    *,
    test_manifest_sha256: str,
    owner_lineage_manifest_sha256: str,
) -> bytes:
    """Serialize the exact held-out outcome membership reviewed for one test split."""

    _validate_sha256(test_manifest_sha256, "test_manifest_sha256")
    _validate_sha256(owner_lineage_manifest_sha256, "owner_lineage_manifest_sha256")
    outcomes: list[dict[str, JsonValue]] = []
    for case in cases:
        outcomes.extend(
            {
                "owner_lineage_token": case.owner_lineage_token,
                "event_sha256": outcome.event_sha256,
                "recording_id": str(outcome.recording_id),
                "signal": outcome.signal,
                "source_request_sha256": outcome.source_request_sha256,
                "source_rank": outcome.source_rank,
                "observed_at_ms": outcome.observed_at_ms,
            }
            for outcome in case.outcomes
        )
    outcomes.sort(
        key=lambda value: (
            cast(str, value["owner_lineage_token"]),
            cast(str, value["source_request_sha256"]),
            cast(str, value["event_sha256"]),
        )
    )
    return rfc8785.dumps(
        {
            "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
            "kind": "SONA_TEST_OUTCOME_EVIDENCE_BUNDLE_V1",
            "test_manifest_sha256": test_manifest_sha256,
            "owner_lineage_manifest_sha256": owner_lineage_manifest_sha256,
            "outcomes": outcomes,
        }
    )


def _verify_paired_execution_evidence_document(
    payload: bytes,
    *,
    case_evidence: tuple[PairedRankingCaseEvidence, ...],
    approved_bundle_sha256: str,
) -> None:
    _canonical_evidence_object(
        payload,
        kind="SONA_PAIRED_EXECUTION_EVIDENCE_BUNDLE_V1",
        max_bytes=SONA_EXECUTION_EVIDENCE_MAX_BYTES,
    )
    expected = sona_paired_execution_evidence_document(case_evidence)
    if payload != expected or sha256(payload).hexdigest() != approved_bundle_sha256:
        raise ValueError("Sona paired execution evidence ancestry mismatch")


def _verify_outcome_evidence_document(
    payload: bytes,
    *,
    cases: tuple[PairedRankingCase, ...],
    test_manifest_sha256: str,
    owner_lineage_manifest_sha256: str,
    approved_bundle_sha256: str,
) -> None:
    document = _canonical_evidence_object(
        payload,
        kind="SONA_TEST_OUTCOME_EVIDENCE_BUNDLE_V1",
        max_bytes=SONA_OUTCOME_EVIDENCE_MAX_BYTES,
    )
    if (
        set(document)
        != {
            "schema_version",
            "kind",
            "test_manifest_sha256",
            "owner_lineage_manifest_sha256",
            "outcomes",
        }
        or _report_string(document, "test_manifest_sha256") != test_manifest_sha256
        or _report_string(document, "owner_lineage_manifest_sha256")
        != owner_lineage_manifest_sha256
        or sha256(payload).hexdigest() != approved_bundle_sha256
    ):
        raise ValueError("Sona held-out outcome evidence ancestry mismatch")
    raw_outcomes = document.get("outcomes")
    if not isinstance(raw_outcomes, list):
        raise ValueError("Sona held-out outcome evidence is invalid")
    parsed: list[tuple[str, AttributedOutcome]] = []
    expected_keys = {
        "owner_lineage_token",
        "event_sha256",
        "recording_id",
        "signal",
        "source_request_sha256",
        "source_rank",
        "observed_at_ms",
    }
    for raw in raw_outcomes:
        item = _report_object(raw, "held-out outcome")
        if set(item) != expected_keys:
            raise ValueError("Sona held-out outcome evidence keys are invalid")
        outcome_document: dict[str, JsonValue] = {
            "schema_version": 1,
            "kind": "SONA_ATTRIBUTED_OUTCOME_V1",
            "recording_id": _report_string(item, "recording_id"),
            "signal": _report_string(item, "signal"),
            "source_request_sha256": _report_string(item, "source_request_sha256"),
            "source_rank": _report_int(item, "source_rank"),
            "observed_at_ms": _report_int(item, "observed_at_ms"),
        }
        outcome = AttributedOutcome(
            event_sha256=_report_string(item, "event_sha256"),
            recording_id=UUID(cast(str, outcome_document["recording_id"])),
            signal=cast(str, outcome_document["signal"]),
            source_request_sha256=cast(str, outcome_document["source_request_sha256"]),
            source_rank=cast(int, outcome_document["source_rank"]),
            observed_at_ms=cast(int, outcome_document["observed_at_ms"]),
        )
        if outcome.event_sha256 != sha256(rfc8785.dumps(outcome_document)).hexdigest():
            raise ValueError("Sona held-out outcome event hash mismatch")
        parsed.append((_report_string(item, "owner_lineage_token"), outcome))

    def identity(
        owner: str, outcome: AttributedOutcome
    ) -> tuple[str, str, str, str, str, int, int]:
        return (
            owner,
            outcome.event_sha256,
            str(outcome.recording_id),
            outcome.signal,
            outcome.source_request_sha256,
            outcome.source_rank,
            outcome.observed_at_ms,
        )

    expected = sorted(
        identity(case.owner_lineage_token, outcome) for case in cases for outcome in case.outcomes
    )
    actual = sorted(identity(owner, outcome) for owner, outcome in parsed)
    if actual != expected or len(actual) != len({item[1] for item in actual}):
        raise ValueError("Sona held-out outcome membership mismatch")


def compute_sona_directional_scenario_bundle_sha256(
    values: tuple[DirectionalScenarioEvidence, ...],
) -> str:
    """Hash the exact required scenario set and derived pass/fail identities."""

    documents: list[JsonValue] = [
        {
            "case_id": value.case_id,
            "expected_sha256": value.expected_sha256,
            "actual_sha256": value.actual_sha256,
            "actual_document": cast(JsonValue, json.loads(value.actual_document_rfc8785)),
            "evidence_sha256": value.evidence_sha256,
            "passed": value.passed,
        }
        for value in sorted(values, key=lambda item: item.case_id)
    ]
    return sha256(
        rfc8785.dumps(
            {
                "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
                "kind": "SONA_DIRECTIONAL_SCENARIO_BUNDLE_V1",
                "scenarios": documents,
            }
        )
    ).hexdigest()


def compute_sona_safety_evidence_bundle_sha256(value: SonaSafetyEvidence) -> str:
    """Hash every zero/nonzero counter together with its gate-specific evidence identity."""

    return sha256(sona_safety_evidence_document(value)).hexdigest()


def compute_sona_performance_evidence_sha256(value: SonaPerformanceEvidence) -> str:
    """Hash raw paired latency measurements, storage sizes and environment identity."""

    return sha256(sona_performance_evidence_document(value)).hexdigest()


def sona_safety_evidence_document(value: SonaSafetyEvidence) -> bytes:
    """Serialize the complete raw safety evidence as one canonical immutable artifact."""

    return rfc8785.dumps(
        {
            "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
            "kind": "SONA_SAFETY_EVIDENCE_BUNDLE_V1",
            **_safety_document(value),
        }
    )


def sona_performance_evidence_document(value: SonaPerformanceEvidence) -> bytes:
    """Serialize every raw paired benchmark sample as one canonical immutable artifact."""

    return rfc8785.dumps(
        {
            "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
            "kind": "SONA_RAW_PERFORMANCE_EVIDENCE_V1",
            "benchmark_run_sha256": value.benchmark_run_sha256,
            "ort_benchmark_evidence_sha256": value.ort_benchmark_evidence_sha256,
            "environment_sha256": value.environment_sha256,
            "checkpoint_manifest_sha256": value.checkpoint_manifest_sha256,
            "artifact_sha256": value.artifact_sha256,
            "tokenizer_manifest_sha256": value.tokenizer_manifest_sha256,
            "warmup_iterations": value.warmup_iterations,
            "measured_iterations_per_case": value.measured_iterations_per_case,
            "samples": [
                {
                    "case_id": sample.case_id,
                    "request_sha256": sample.request_sha256,
                    "baseline_latency_ms": sample.baseline_latency_ms,
                    "sona_latency_ms": sample.sona_latency_ms,
                }
                for sample in value.samples
            ],
            "dataset_storage_bytes": value.dataset_storage_bytes,
            "artifact_storage_bytes": value.artifact_storage_bytes,
            "dataset_storage_manifest_sha256": value.dataset_storage_manifest_sha256,
            "artifact_storage_manifest_sha256": value.artifact_storage_manifest_sha256,
        }
    )


def _canonical_evidence_object(payload: bytes, *, kind: str, max_bytes: int) -> dict[str, object]:
    if not 1 <= len(payload) <= max_bytes:
        raise ValueError("Sona raw evaluation evidence exceeds the accepted bound")
    try:
        value = cast(object, json.loads(payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Sona raw evaluation evidence is invalid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Sona raw evaluation evidence must be an object")
    document = cast(dict[str, object], value)
    if (
        document.get("schema_version") != SONA_EVALUATION_SCHEMA_VERSION
        or document.get("kind") != kind
        or rfc8785.dumps(cast(dict[str, JsonValue], document)) != payload
    ):
        raise ValueError("Sona raw evaluation evidence is not canonical")
    return document


def _safety_evidence_from_document(payload: bytes) -> SonaSafetyEvidence:
    document = _canonical_evidence_object(
        payload,
        kind="SONA_SAFETY_EVIDENCE_BUNDLE_V1",
        max_bytes=SONA_SAFETY_EVIDENCE_MAX_BYTES,
    )
    if set(document) != {"schema_version", "kind", "checks"}:
        raise ValueError("Sona raw safety evidence keys are invalid")
    return _report_safety({"checks": document["checks"]})


def _performance_evidence_from_document(payload: bytes) -> SonaPerformanceEvidence:
    document = _canonical_evidence_object(
        payload,
        kind="SONA_RAW_PERFORMANCE_EVIDENCE_V1",
        max_bytes=SONA_PERFORMANCE_EVIDENCE_MAX_BYTES,
    )
    expected = {
        "schema_version",
        "kind",
        "benchmark_run_sha256",
        "ort_benchmark_evidence_sha256",
        "environment_sha256",
        "checkpoint_manifest_sha256",
        "artifact_sha256",
        "tokenizer_manifest_sha256",
        "warmup_iterations",
        "measured_iterations_per_case",
        "samples",
        "dataset_storage_bytes",
        "artifact_storage_bytes",
        "dataset_storage_manifest_sha256",
        "artifact_storage_manifest_sha256",
    }
    raw_samples = document.get("samples")
    if set(document) != expected or not isinstance(raw_samples, list):
        raise ValueError("Sona raw performance evidence keys are invalid")
    samples: list[PairedLatencySample] = []
    for raw_sample in raw_samples:
        sample = _report_object(raw_sample, "raw performance sample")
        if set(sample) != {
            "case_id",
            "request_sha256",
            "baseline_latency_ms",
            "sona_latency_ms",
        }:
            raise ValueError("Sona raw performance sample keys are invalid")
        samples.append(
            PairedLatencySample(
                case_id=_report_string(sample, "case_id"),
                request_sha256=_report_string(sample, "request_sha256"),
                baseline_latency_ms=_report_number(sample, "baseline_latency_ms"),
                sona_latency_ms=_report_number(sample, "sona_latency_ms"),
            )
        )
    return SonaPerformanceEvidence(
        samples=tuple(samples),
        benchmark_run_sha256=_report_string(document, "benchmark_run_sha256"),
        ort_benchmark_evidence_sha256=_report_string(document, "ort_benchmark_evidence_sha256"),
        environment_sha256=_report_string(document, "environment_sha256"),
        checkpoint_manifest_sha256=_report_string(document, "checkpoint_manifest_sha256"),
        artifact_sha256=_report_string(document, "artifact_sha256"),
        tokenizer_manifest_sha256=_report_string(document, "tokenizer_manifest_sha256"),
        warmup_iterations=_report_int(document, "warmup_iterations"),
        measured_iterations_per_case=_report_int(document, "measured_iterations_per_case"),
        dataset_storage_bytes=_report_int(document, "dataset_storage_bytes"),
        artifact_storage_bytes=_report_int(document, "artifact_storage_bytes"),
        dataset_storage_manifest_sha256=_report_string(document, "dataset_storage_manifest_sha256"),
        artifact_storage_manifest_sha256=_report_string(
            document, "artifact_storage_manifest_sha256"
        ),
    )


def _verify_ort_benchmark_evidence_document(
    payload: bytes,
    *,
    performance: SonaPerformanceEvidence,
    cases: tuple[PairedRankingCase, ...],
    dataset_approval_sha256: str,
    dataset_bundle_sha256: str,
    artifact_manifest_sha256: str,
) -> None:
    envelope = _canonical_evidence_object(
        payload,
        kind="SONA_ORT_BENCHMARK_EVIDENCE_ENVELOPE_V1",
        max_bytes=SONA_PERFORMANCE_EVIDENCE_MAX_BYTES,
    )
    if set(envelope) != {"schema_version", "kind", "evidence", "evidence_sha256"}:
        raise ValueError("Sona ORT benchmark envelope keys are invalid")
    evidence = _report_object(envelope.get("evidence"), "ORT benchmark evidence")
    evidence_sha256 = _report_string(envelope, "evidence_sha256")
    expected_keys = {
        "schema_version",
        "benchmark",
        "artifact_sha256",
        "model_manifest_sha256",
        "commit_sha256",
        "checkpoint_manifest_sha256",
        "checkpoint_weights_sha256",
        "dataset_manifest_sha256",
        "data_classification",
        "dataset_approval_sha256",
        "dataset_bundle_sha256",
        "quality_provenance_eligible",
        "quality_eligible",
        "device_type",
        "device_name",
        "onnxruntime_version",
        "environment_sha256",
        "cuda_only_execution",
        "warmup_iterations",
        "measured_iterations",
        "request_count",
        "raw_samples",
        "mean_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "generated_nonzero",
        "generated_within_tokenizer",
        "generated_expandable",
        "tokenizer_fit_manifest_sha256",
        "tokenizer_active_codes_per_level",
    }
    if (
        set(evidence) != expected_keys
        or _report_int(evidence, "schema_version") != 1
        or _report_string(evidence, "benchmark") != "SONA_LITE_COMMITTED_ONNX_RUNTIME_V1"
        or evidence_sha256
        != sha256(rfc8785.dumps(cast(dict[str, JsonValue], evidence))).hexdigest()
        or evidence_sha256 != performance.ort_benchmark_evidence_sha256
        or _report_string(evidence, "artifact_sha256") != performance.artifact_sha256
        or _report_string(evidence, "model_manifest_sha256") != artifact_manifest_sha256
        or _report_string(evidence, "checkpoint_manifest_sha256")
        != performance.checkpoint_manifest_sha256
        or _report_string(evidence, "dataset_manifest_sha256")
        != performance.dataset_storage_manifest_sha256
        or _report_string(evidence, "tokenizer_fit_manifest_sha256")
        != performance.tokenizer_manifest_sha256
        or _report_string(evidence, "dataset_approval_sha256") != dataset_approval_sha256
        or _report_string(evidence, "dataset_bundle_sha256") != dataset_bundle_sha256
        or _report_string(evidence, "data_classification") != "APPROVED_OWNER_SAFE"
        or evidence.get("quality_provenance_eligible") is not True
        or evidence.get("quality_eligible") is not False
        or evidence.get("generated_nonzero") is not True
        or evidence.get("generated_within_tokenizer") is not True
        or evidence.get("generated_expandable") is not True
        or evidence.get("cuda_only_execution") is not True
    ):
        raise ValueError("Sona ORT benchmark evidence ancestry is invalid")
    for field in ("commit_sha256", "checkpoint_weights_sha256", "environment_sha256"):
        _validate_sha256(_report_string(evidence, field), field)
    environment_document: dict[str, JsonValue] = {
        "device_type": _report_string(evidence, "device_type"),
        "device_name": _report_string(evidence, "device_name"),
        "onnxruntime_version": _report_string(evidence, "onnxruntime_version"),
    }
    if (
        environment_document["device_type"] != "cuda"
        or environment_document["device_name"] != "CUDAExecutionProvider"
        or not environment_document["onnxruntime_version"]
        or sha256(rfc8785.dumps(environment_document)).hexdigest() != performance.environment_sha256
        or _report_string(evidence, "environment_sha256") != performance.environment_sha256
        or _report_int(evidence, "warmup_iterations") != performance.warmup_iterations
        or _report_int(evidence, "measured_iterations") != performance.measured_iterations_per_case
        or _report_int(evidence, "request_count") != len(cases)
        or _report_int(evidence, "tokenizer_active_codes_per_level") < 1
    ):
        raise ValueError("Sona ORT benchmark environment or coverage is invalid")
    raw_samples = evidence.get("raw_samples")
    if not isinstance(raw_samples, list):
        raise ValueError("Sona ORT benchmark samples are invalid")
    actual_samples: list[tuple[str, int, float]] = []
    for raw_sample in raw_samples:
        sample = _report_object(raw_sample, "ORT benchmark sample")
        if set(sample) != {"request_sha256", "iteration", "latency_ms"}:
            raise ValueError("Sona ORT benchmark sample keys are invalid")
        request_sha256 = _report_string(sample, "request_sha256")
        _validate_sha256(request_sha256, "ORT benchmark request_sha256")
        iteration = _report_int(sample, "iteration")
        latency_ms = _report_number(sample, "latency_ms")
        if not 0 <= iteration < performance.measured_iterations_per_case or latency_ms < 0.0:
            raise ValueError("Sona ORT benchmark sample is invalid")
        actual_samples.append((request_sha256, iteration, latency_ms))
    expected_membership = sorted(
        (case.request_sha256, iteration)
        for case in cases
        for iteration in range(performance.measured_iterations_per_case)
    )
    if (
        sorted((request, iteration) for request, iteration, _ in actual_samples)
        != expected_membership
    ):
        raise ValueError("Sona ORT benchmark request coverage is incomplete")
    if sorted((request, latency) for request, _, latency in actual_samples) != sorted(
        (sample.request_sha256, sample.sona_latency_ms) for sample in performance.samples
    ):
        raise ValueError("Sona paired latency does not match executed ORT evidence")
    latency_values = tuple(value[2] for value in actual_samples)
    if (
        not math.isclose(
            _report_number(evidence, "mean_latency_ms"),
            sum(latency_values) / len(latency_values),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or _report_number(evidence, "p50_latency_ms") != _percentile(latency_values, 0.5)
        or _report_number(evidence, "p95_latency_ms") != _percentile(latency_values, 0.95)
    ):
        raise ValueError("Sona ORT benchmark latency summary is invalid")


def compute_sona_contract_policy_sha256() -> str:
    """Return the pinned canonical hash of the full accepted recommendation policy."""

    return SONA_CONTRACT_POLICY_SHA256


def evaluate_paired_rankings(
    case_evidence: tuple[PairedRankingCaseEvidence, ...],
    *,
    catalog_recording_count: int,
    source_approval_document: Mapping[str, Any],
    dataset_approval_document: Mapping[str, Any],
    evaluation_approval_document: Mapping[str, Any],
    at_ms: int,
    directional_scenarios: tuple[DirectionalScenarioEvidence, ...],
    execution_evidence_document_rfc8785: bytes,
    outcome_evidence_document_rfc8785: bytes,
    safety_evidence_document_rfc8785: bytes,
    performance_evidence_document_rfc8785: bytes,
    ort_benchmark_evidence_document_rfc8785: bytes,
) -> PairedEvaluationReport:
    """Verify raw evidence, compute one report and apply every automatic R1B threshold."""

    if catalog_recording_count <= 0:
        raise ValueError("Sona paired evaluation dataset is empty")
    cases = _build_paired_ranking_cases(case_evidence)
    safety = _safety_evidence_from_document(safety_evidence_document_rfc8785)
    performance = _performance_evidence_from_document(performance_evidence_document_rfc8785)
    trusted_reviewer_spki = load_deployment_sona_reviewer_trust_anchor().reviewer_spki
    source_approval = verify_sona_source_approval(
        source_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    dataset_approval = verify_sona_dataset_approval(
        dataset_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        source_approval=source_approval,
        at_ms=at_ms,
    )
    approval = verify_sona_evaluation_approval(
        evaluation_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        dataset_approval=dataset_approval,
        at_ms=at_ms,
    )
    _verify_paired_execution_evidence_document(
        execution_evidence_document_rfc8785,
        case_evidence=case_evidence,
        approved_bundle_sha256=approval.execution_evidence_bundle_sha256,
    )
    _verify_outcome_evidence_document(
        outcome_evidence_document_rfc8785,
        cases=cases,
        test_manifest_sha256=dataset_approval.test_manifest_sha256,
        owner_lineage_manifest_sha256=dataset_approval.owner_lineage_manifest_sha256,
        approved_bundle_sha256=approval.outcome_evidence_bundle_sha256,
    )
    _verify_ort_benchmark_evidence_document(
        ort_benchmark_evidence_document_rfc8785,
        performance=performance,
        cases=cases,
        dataset_approval_sha256=dataset_approval.approval_sha256,
        dataset_bundle_sha256=dataset_approval.dataset_bundle_sha256,
        artifact_manifest_sha256=approval.artifact_manifest_sha256,
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Sona paired evaluation case identity is duplicated")
    evaluated_request_sha256s = tuple(sorted(case.request_sha256 for case in cases))
    if (
        len(evaluated_request_sha256s) != dataset_approval.test_request_count
        or sha256(rfc8785.dumps(list(evaluated_request_sha256s))).hexdigest()
        != dataset_approval.test_request_set_sha256
    ):
        raise ValueError("Sona evaluation cases do not match the approved test request set")
    if any(len(case.tracks) > catalog_recording_count for case in cases):
        raise ValueError("Sona paired evaluation catalog count is inconsistent")
    if (
        len(directional_scenarios) != len(REQUIRED_DIRECTIONAL_SCENARIOS)
        or len({value.case_id for value in directional_scenarios}) != len(directional_scenarios)
        or {value.case_id for value in directional_scenarios} != REQUIRED_DIRECTIONAL_SCENARIOS
    ):
        raise ValueError("Sona evaluation directional scenario set is incomplete")
    expected_requests = evaluated_request_sha256s
    if any(check.covered_request_sha256s != expected_requests for check in safety.checks):
        raise ValueError("Sona evaluation safety evidence request coverage is incomplete")
    expected_latency_pairs = sorted(
        (case.case_id, case.request_sha256)
        for case in cases
        for _ in range(performance.measured_iterations_per_case)
    )
    actual_latency_pairs = sorted(
        (sample.case_id, sample.request_sha256) for sample in performance.samples
    )
    if actual_latency_pairs != expected_latency_pairs:
        raise ValueError("Sona evaluation latency samples do not exactly cover the paired cases")
    if (
        any(
            case.p11_pipeline_manifest_sha256 != approval.p11_pipeline_manifest_sha256
            or case.sona_pipeline_manifest_sha256 != approval.sona_pipeline_manifest_sha256
            for case in cases
        )
        or compute_sona_evaluation_case_bundle_sha256(cases)
        != approval.evaluation_case_bundle_sha256
        or performance.checkpoint_manifest_sha256 != approval.checkpoint_manifest_sha256
        or performance.artifact_sha256 != approval.artifact_sha256
        or performance.tokenizer_manifest_sha256 != approval.tokenizer_manifest_sha256
        or performance.dataset_storage_manifest_sha256 != dataset_approval.test_manifest_sha256
        or performance.artifact_storage_manifest_sha256 != approval.artifact_manifest_sha256
        or compute_sona_directional_scenario_bundle_sha256(directional_scenarios)
        != approval.directional_scenario_bundle_sha256
        or compute_sona_safety_evidence_bundle_sha256(safety)
        != approval.safety_evidence_bundle_sha256
        or compute_sona_performance_evidence_sha256(performance)
        != approval.performance_evidence_sha256
        or compute_sona_contract_policy_sha256() != approval.contract_policy_sha256
    ):
        raise ValueError("Sona evaluation evidence does not match its signed approval")
    baseline = _ranking_metrics(cases, catalog_recording_count, use_sona=False)
    sona = _ranking_metrics(cases, catalog_recording_count, use_sona=True)
    baseline_p50 = median(performance.baseline_latency_ms)
    baseline_p95 = _percentile(performance.baseline_latency_ms, 0.95)
    sona_p50 = median(performance.sona_latency_ms)
    sona_p95 = _percentile(performance.sona_latency_ms, 0.95)
    latency_ratio = (
        1.0
        if baseline_p95 == 0.0 and sona_p95 == 0.0
        else SONA_MAX_P95_LATENCY_RATIO + 1.0
        if baseline_p95 == 0.0
        else sona_p95 / baseline_p95
    )
    directional_passed = sum(value.passed for value in directional_scenarios)

    metrics_document: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "dataset_bundle_sha256": approval.dataset_bundle_sha256,
        "artifact_manifest_sha256": approval.artifact_manifest_sha256,
        "k": SONA_EVALUATION_K,
        "baseline": _ranking_metrics_document(baseline),
        "sona": _ranking_metrics_document(sona),
        "safety": _safety_document(safety),
        "directional_scenarios_passed": directional_passed,
        "directional_scenarios_total": len(directional_scenarios),
    }
    metrics_sha256 = sha256(rfc8785.dumps(metrics_document)).hexdigest()
    performance_document: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "raw_performance_evidence_sha256": approval.performance_evidence_sha256,
        "benchmark_run_sha256": performance.benchmark_run_sha256,
        "ort_benchmark_evidence_sha256": performance.ort_benchmark_evidence_sha256,
        "environment_sha256": performance.environment_sha256,
        "warmup_iterations": performance.warmup_iterations,
        "measured_iterations_per_case": performance.measured_iterations_per_case,
        "baseline_latency_p50_ms": baseline_p50,
        "baseline_latency_p95_ms": baseline_p95,
        "sona_latency_p50_ms": sona_p50,
        "sona_latency_p95_ms": sona_p95,
        "latency_ratio_p95": latency_ratio,
        "dataset_storage_bytes": performance.dataset_storage_bytes,
        "artifact_storage_bytes": performance.artifact_storage_bytes,
        "dataset_storage_manifest_sha256": performance.dataset_storage_manifest_sha256,
        "artifact_storage_manifest_sha256": performance.artifact_storage_manifest_sha256,
    }
    performance_sha256 = sha256(rfc8785.dumps(performance_document)).hexdigest()
    verdict = _gate_verdict(
        baseline,
        sona,
        safety=safety,
        directional_passed=directional_passed,
        directional_total=len(directional_scenarios),
        sona_latency_p95_ms=sona_p95,
        latency_ratio_p95=latency_ratio,
    )
    report_document: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "kind": "SONA_PAIRED_EVALUATION_REPORT_V1",
        "evaluation_approval_sha256": approval.approval_sha256,
        "dataset_approval_sha256": approval.dataset_approval_sha256,
        "dataset_bundle_sha256": approval.dataset_bundle_sha256,
        "checkpoint_manifest_sha256": approval.checkpoint_manifest_sha256,
        "artifact_sha256": approval.artifact_sha256,
        "artifact_manifest_sha256": approval.artifact_manifest_sha256,
        "tokenizer_manifest_sha256": approval.tokenizer_manifest_sha256,
        "p11_pipeline_manifest_sha256": approval.p11_pipeline_manifest_sha256,
        "sona_pipeline_manifest_sha256": approval.sona_pipeline_manifest_sha256,
        "contract_policy_sha256": approval.contract_policy_sha256,
        "evaluation_case_bundle_sha256": approval.evaluation_case_bundle_sha256,
        "execution_evidence_bundle_sha256": approval.execution_evidence_bundle_sha256,
        "outcome_evidence_bundle_sha256": approval.outcome_evidence_bundle_sha256,
        "directional_scenario_bundle_sha256": approval.directional_scenario_bundle_sha256,
        "safety_evidence_bundle_sha256": approval.safety_evidence_bundle_sha256,
        "raw_performance_evidence_sha256": approval.performance_evidence_sha256,
        "metrics_sha256": metrics_sha256,
        "performance_sha256": performance_sha256,
        "metrics": metrics_document,
        "performance": performance_document,
        "gate_policy": _gate_policy_document(),
        "eligible": verdict.eligible,
        "failures": list(verdict.failures),
    }
    report_sha256 = sha256(rfc8785.dumps(report_document)).hexdigest()
    return PairedEvaluationReport(
        evaluation_approval_sha256=approval.approval_sha256,
        dataset_approval_sha256=approval.dataset_approval_sha256,
        dataset_bundle_sha256=approval.dataset_bundle_sha256,
        checkpoint_manifest_sha256=approval.checkpoint_manifest_sha256,
        artifact_sha256=approval.artifact_sha256,
        artifact_manifest_sha256=approval.artifact_manifest_sha256,
        tokenizer_manifest_sha256=approval.tokenizer_manifest_sha256,
        p11_pipeline_manifest_sha256=approval.p11_pipeline_manifest_sha256,
        sona_pipeline_manifest_sha256=approval.sona_pipeline_manifest_sha256,
        contract_policy_sha256=approval.contract_policy_sha256,
        evaluation_case_bundle_sha256=approval.evaluation_case_bundle_sha256,
        execution_evidence_bundle_sha256=approval.execution_evidence_bundle_sha256,
        outcome_evidence_bundle_sha256=approval.outcome_evidence_bundle_sha256,
        directional_scenario_bundle_sha256=approval.directional_scenario_bundle_sha256,
        safety_evidence_bundle_sha256=approval.safety_evidence_bundle_sha256,
        raw_performance_evidence_sha256=approval.performance_evidence_sha256,
        baseline=baseline,
        sona=sona,
        safety=safety,
        directional_scenarios_passed=directional_passed,
        directional_scenarios_total=len(directional_scenarios),
        baseline_latency_p50_ms=baseline_p50,
        baseline_latency_p95_ms=baseline_p95,
        sona_latency_p50_ms=sona_p50,
        sona_latency_p95_ms=sona_p95,
        latency_ratio_p95=latency_ratio,
        benchmark_run_sha256=performance.benchmark_run_sha256,
        ort_benchmark_evidence_sha256=performance.ort_benchmark_evidence_sha256,
        environment_sha256=performance.environment_sha256,
        warmup_iterations=performance.warmup_iterations,
        measured_iterations_per_case=performance.measured_iterations_per_case,
        dataset_storage_bytes=performance.dataset_storage_bytes,
        artifact_storage_bytes=performance.artifact_storage_bytes,
        dataset_storage_manifest_sha256=performance.dataset_storage_manifest_sha256,
        artifact_storage_manifest_sha256=performance.artifact_storage_manifest_sha256,
        metrics_sha256=metrics_sha256,
        performance_sha256=performance_sha256,
        report_sha256=report_sha256,
        verdict=verdict,
    )


def paired_evaluation_report_document(value: PairedEvaluationReport) -> dict[str, JsonValue]:
    """Serialize and self-verify one complete canonical evaluation report envelope."""

    metrics_document: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "dataset_bundle_sha256": value.dataset_bundle_sha256,
        "artifact_manifest_sha256": value.artifact_manifest_sha256,
        "k": SONA_EVALUATION_K,
        "baseline": _ranking_metrics_document(value.baseline),
        "sona": _ranking_metrics_document(value.sona),
        "safety": _safety_document(value.safety),
        "directional_scenarios_passed": value.directional_scenarios_passed,
        "directional_scenarios_total": value.directional_scenarios_total,
    }
    performance_document: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "raw_performance_evidence_sha256": value.raw_performance_evidence_sha256,
        "benchmark_run_sha256": value.benchmark_run_sha256,
        "ort_benchmark_evidence_sha256": value.ort_benchmark_evidence_sha256,
        "environment_sha256": value.environment_sha256,
        "warmup_iterations": value.warmup_iterations,
        "measured_iterations_per_case": value.measured_iterations_per_case,
        "baseline_latency_p50_ms": value.baseline_latency_p50_ms,
        "baseline_latency_p95_ms": value.baseline_latency_p95_ms,
        "sona_latency_p50_ms": value.sona_latency_p50_ms,
        "sona_latency_p95_ms": value.sona_latency_p95_ms,
        "latency_ratio_p95": value.latency_ratio_p95,
        "dataset_storage_bytes": value.dataset_storage_bytes,
        "artifact_storage_bytes": value.artifact_storage_bytes,
        "dataset_storage_manifest_sha256": value.dataset_storage_manifest_sha256,
        "artifact_storage_manifest_sha256": value.artifact_storage_manifest_sha256,
    }
    if (
        sha256(rfc8785.dumps(metrics_document)).hexdigest() != value.metrics_sha256
        or sha256(rfc8785.dumps(performance_document)).hexdigest() != value.performance_sha256
    ):
        raise ValueError("Sona evaluation report nested hash mismatch")
    report: dict[str, JsonValue] = {
        "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
        "kind": "SONA_PAIRED_EVALUATION_REPORT_V1",
        "evaluation_approval_sha256": value.evaluation_approval_sha256,
        "dataset_approval_sha256": value.dataset_approval_sha256,
        "dataset_bundle_sha256": value.dataset_bundle_sha256,
        "checkpoint_manifest_sha256": value.checkpoint_manifest_sha256,
        "artifact_sha256": value.artifact_sha256,
        "artifact_manifest_sha256": value.artifact_manifest_sha256,
        "tokenizer_manifest_sha256": value.tokenizer_manifest_sha256,
        "p11_pipeline_manifest_sha256": value.p11_pipeline_manifest_sha256,
        "sona_pipeline_manifest_sha256": value.sona_pipeline_manifest_sha256,
        "contract_policy_sha256": value.contract_policy_sha256,
        "evaluation_case_bundle_sha256": value.evaluation_case_bundle_sha256,
        "execution_evidence_bundle_sha256": value.execution_evidence_bundle_sha256,
        "outcome_evidence_bundle_sha256": value.outcome_evidence_bundle_sha256,
        "directional_scenario_bundle_sha256": value.directional_scenario_bundle_sha256,
        "safety_evidence_bundle_sha256": value.safety_evidence_bundle_sha256,
        "raw_performance_evidence_sha256": value.raw_performance_evidence_sha256,
        "metrics_sha256": value.metrics_sha256,
        "performance_sha256": value.performance_sha256,
        "metrics": metrics_document,
        "performance": performance_document,
        "gate_policy": _gate_policy_document(),
        "eligible": value.verdict.eligible,
        "failures": list(value.verdict.failures),
    }
    if sha256(rfc8785.dumps(report)).hexdigest() != value.report_sha256:
        raise ValueError("Sona evaluation report canonical hash mismatch")
    return {"report": report, "report_sha256": value.report_sha256}


def paired_evaluation_report_from_document(value: Mapping[str, object]) -> PairedEvaluationReport:
    """Parse an exact report envelope and reject any altered or incomplete binding."""

    if set(value) != {"report", "report_sha256"}:
        raise ValueError("Sona evaluation report envelope keys are invalid")
    report = _report_object(value.get("report"), "report")
    expected_report_keys = {
        "schema_version",
        "kind",
        "evaluation_approval_sha256",
        "dataset_approval_sha256",
        "dataset_bundle_sha256",
        "checkpoint_manifest_sha256",
        "artifact_sha256",
        "artifact_manifest_sha256",
        "tokenizer_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "sona_pipeline_manifest_sha256",
        "contract_policy_sha256",
        "evaluation_case_bundle_sha256",
        "execution_evidence_bundle_sha256",
        "outcome_evidence_bundle_sha256",
        "directional_scenario_bundle_sha256",
        "safety_evidence_bundle_sha256",
        "raw_performance_evidence_sha256",
        "metrics_sha256",
        "performance_sha256",
        "metrics",
        "performance",
        "gate_policy",
        "eligible",
        "failures",
    }
    if (
        set(report) != expected_report_keys
        or _report_int(report, "schema_version") != SONA_EVALUATION_SCHEMA_VERSION
        or _report_string(report, "kind") != "SONA_PAIRED_EVALUATION_REPORT_V1"
        or _report_object(report.get("gate_policy"), "gate_policy") != _gate_policy_document()
    ):
        raise ValueError("Sona evaluation report schema or policy is invalid")
    report_sha256 = _report_string(value, "report_sha256")
    _validate_sha256(report_sha256, "report_sha256")
    if sha256(rfc8785.dumps(cast(dict[str, JsonValue], report))).hexdigest() != report_sha256:
        raise ValueError("Sona evaluation report canonical hash mismatch")
    digest_fields = (
        "evaluation_approval_sha256",
        "dataset_approval_sha256",
        "dataset_bundle_sha256",
        "checkpoint_manifest_sha256",
        "artifact_sha256",
        "artifact_manifest_sha256",
        "tokenizer_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "sona_pipeline_manifest_sha256",
        "contract_policy_sha256",
        "evaluation_case_bundle_sha256",
        "execution_evidence_bundle_sha256",
        "outcome_evidence_bundle_sha256",
        "directional_scenario_bundle_sha256",
        "safety_evidence_bundle_sha256",
        "raw_performance_evidence_sha256",
        "metrics_sha256",
        "performance_sha256",
    )
    digests = {field: _report_string(report, field) for field in digest_fields}
    for field, digest in digests.items():
        _validate_sha256(digest, field)
    if digests["contract_policy_sha256"] != compute_sona_contract_policy_sha256():
        raise ValueError("Sona evaluation report contract policy is not pinned")
    metrics = _report_object(report.get("metrics"), "metrics")
    performance = _report_object(report.get("performance"), "performance")
    if (
        sha256(rfc8785.dumps(cast(dict[str, JsonValue], metrics))).hexdigest()
        != digests["metrics_sha256"]
        or sha256(rfc8785.dumps(cast(dict[str, JsonValue], performance))).hexdigest()
        != digests["performance_sha256"]
    ):
        raise ValueError("Sona evaluation report nested hash mismatch")
    parsed_metrics = _report_metrics(metrics)
    parsed_performance = _report_performance(performance)
    if (
        parsed_metrics[0] != digests["dataset_bundle_sha256"]
        or parsed_metrics[1] != digests["artifact_manifest_sha256"]
        or parsed_performance[0] != digests["raw_performance_evidence_sha256"]
    ):
        raise ValueError("Sona evaluation report nested ancestry mismatch")
    raw_failures = report.get("failures")
    eligible = report.get("eligible")
    if (
        not isinstance(eligible, bool)
        or not isinstance(raw_failures, list)
        or any(not isinstance(item, str) for item in raw_failures)
    ):
        raise ValueError("Sona evaluation report verdict is invalid")
    result = PairedEvaluationReport(
        evaluation_approval_sha256=digests["evaluation_approval_sha256"],
        dataset_approval_sha256=digests["dataset_approval_sha256"],
        dataset_bundle_sha256=digests["dataset_bundle_sha256"],
        checkpoint_manifest_sha256=digests["checkpoint_manifest_sha256"],
        artifact_sha256=digests["artifact_sha256"],
        artifact_manifest_sha256=digests["artifact_manifest_sha256"],
        tokenizer_manifest_sha256=digests["tokenizer_manifest_sha256"],
        p11_pipeline_manifest_sha256=digests["p11_pipeline_manifest_sha256"],
        sona_pipeline_manifest_sha256=digests["sona_pipeline_manifest_sha256"],
        contract_policy_sha256=digests["contract_policy_sha256"],
        evaluation_case_bundle_sha256=digests["evaluation_case_bundle_sha256"],
        execution_evidence_bundle_sha256=digests["execution_evidence_bundle_sha256"],
        outcome_evidence_bundle_sha256=digests["outcome_evidence_bundle_sha256"],
        directional_scenario_bundle_sha256=digests["directional_scenario_bundle_sha256"],
        safety_evidence_bundle_sha256=digests["safety_evidence_bundle_sha256"],
        raw_performance_evidence_sha256=digests["raw_performance_evidence_sha256"],
        baseline=parsed_metrics[2],
        sona=parsed_metrics[3],
        safety=parsed_metrics[4],
        directional_scenarios_passed=parsed_metrics[5],
        directional_scenarios_total=parsed_metrics[6],
        baseline_latency_p50_ms=parsed_performance[1],
        baseline_latency_p95_ms=parsed_performance[2],
        sona_latency_p50_ms=parsed_performance[3],
        sona_latency_p95_ms=parsed_performance[4],
        latency_ratio_p95=parsed_performance[5],
        benchmark_run_sha256=parsed_performance[6],
        ort_benchmark_evidence_sha256=parsed_performance[7],
        environment_sha256=parsed_performance[8],
        warmup_iterations=parsed_performance[9],
        measured_iterations_per_case=parsed_performance[10],
        dataset_storage_bytes=parsed_performance[11],
        artifact_storage_bytes=parsed_performance[12],
        dataset_storage_manifest_sha256=parsed_performance[13],
        artifact_storage_manifest_sha256=parsed_performance[14],
        metrics_sha256=digests["metrics_sha256"],
        performance_sha256=digests["performance_sha256"],
        report_sha256=report_sha256,
        verdict=SonaGateVerdict(eligible, tuple(cast(list[str], raw_failures))),
    )
    if (
        result.directional_scenarios_total != len(REQUIRED_DIRECTIONAL_SCENARIOS)
        or result.baseline.decoder_coverage is not None
        or result.baseline.decoder_positive_recall is not None
        or result.verdict
        != _gate_verdict(
            result.baseline,
            result.sona,
            safety=result.safety,
            directional_passed=result.directional_scenarios_passed,
            directional_total=result.directional_scenarios_total,
            sona_latency_p95_ms=result.sona_latency_p95_ms,
            latency_ratio_p95=result.latency_ratio_p95,
        )
    ):
        raise ValueError("Sona evaluation report semantic gate verdict is invalid")
    paired_evaluation_report_document(result)
    return result


def verify_quality_eligible_sona_artifact(
    *,
    report_document: Mapping[str, object],
    source_approval_document: Mapping[str, Any],
    dataset_approval_document: Mapping[str, Any],
    evaluation_approval_document: Mapping[str, Any],
    artifact_approval_document: Mapping[str, Any],
    checkpoint_directory: Path,
    artifact_path: Path,
    at_ms: int,
) -> VerifiedSonaArtifactApproval:
    """Re-verify the complete approval chain and derive final artifact eligibility."""

    trusted_reviewer_spki = load_deployment_sona_reviewer_trust_anchor().reviewer_spki
    source = verify_sona_source_approval(
        source_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    dataset = verify_sona_dataset_approval(
        dataset_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        source_approval=source,
        at_ms=at_ms,
    )
    evaluation = verify_sona_evaluation_approval(
        evaluation_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        dataset_approval=dataset,
        at_ms=at_ms,
    )
    artifact = verify_sona_artifact_approval(
        artifact_approval_document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        evaluation_approval=evaluation,
        at_ms=at_ms,
    )
    report = paired_evaluation_report_from_document(report_document)
    if (
        not report.verdict.eligible
        or report.evaluation_approval_sha256 != evaluation.approval_sha256
        or report.p11_pipeline_manifest_sha256 != evaluation.p11_pipeline_manifest_sha256
        or report.sona_pipeline_manifest_sha256 != evaluation.sona_pipeline_manifest_sha256
        or report.evaluation_case_bundle_sha256 != evaluation.evaluation_case_bundle_sha256
        or report.execution_evidence_bundle_sha256 != evaluation.execution_evidence_bundle_sha256
        or report.outcome_evidence_bundle_sha256 != evaluation.outcome_evidence_bundle_sha256
        or report.directional_scenario_bundle_sha256
        != evaluation.directional_scenario_bundle_sha256
        or report.safety_evidence_bundle_sha256 != evaluation.safety_evidence_bundle_sha256
        or report.raw_performance_evidence_sha256 != evaluation.performance_evidence_sha256
        or report.report_sha256 != artifact.evaluation_report_sha256
        or report.metrics_sha256 != artifact.metrics_sha256
        or report.performance_sha256 != artifact.performance_sha256
        or report.dataset_approval_sha256 != artifact.dataset_approval_sha256
        or report.dataset_bundle_sha256 != artifact.dataset_bundle_sha256
        or report.tokenizer_manifest_sha256 != artifact.tokenizer_manifest_sha256
        or report.checkpoint_manifest_sha256 != artifact.checkpoint_manifest_sha256
        or report.artifact_sha256 != artifact.artifact_sha256
        or report.artifact_manifest_sha256 != artifact.artifact_manifest_sha256
        or report.contract_policy_sha256 != artifact.contract_policy_sha256
    ):
        raise ValueError("Sona artifact approval does not match one passing evaluation report")
    _verify_quality_candidate_files(
        checkpoint_directory=checkpoint_directory,
        artifact_path=artifact_path,
        report=report,
        source=source,
        dataset=dataset,
    )
    return artifact


def load_quality_eligible_sona_artifact(
    *,
    report_path: Path,
    source_approval_path: Path,
    dataset_approval_path: Path,
    evaluation_approval_path: Path,
    artifact_approval_path: Path,
    checkpoint_directory: Path,
    artifact_path: Path,
    at_ms: int,
) -> VerifiedSonaArtifactApproval:
    """Load bounded canonical documents and verify final artifact eligibility."""

    return verify_quality_eligible_sona_artifact(
        report_document=_load_evaluation_object(
            report_path, max_bytes=SONA_EVALUATION_REPORT_MAX_BYTES
        ),
        source_approval_document=_load_evaluation_object(
            source_approval_path, max_bytes=SONA_APPROVAL_MAX_BYTES
        ),
        dataset_approval_document=_load_evaluation_object(
            dataset_approval_path, max_bytes=SONA_APPROVAL_MAX_BYTES
        ),
        evaluation_approval_document=_load_evaluation_object(
            evaluation_approval_path, max_bytes=SONA_APPROVAL_MAX_BYTES
        ),
        artifact_approval_document=_load_evaluation_object(
            artifact_approval_path, max_bytes=SONA_APPROVAL_MAX_BYTES
        ),
        checkpoint_directory=checkpoint_directory,
        artifact_path=artifact_path,
        at_ms=at_ms,
    )


def _verify_quality_candidate_files(
    *,
    checkpoint_directory: Path,
    artifact_path: Path,
    report: PairedEvaluationReport,
    source: VerifiedSonaSourceApproval,
    dataset: VerifiedSonaDatasetApproval,
) -> None:
    checkpoint_envelope = _load_evaluation_object(
        checkpoint_directory / "manifest.json", max_bytes=SONA_APPROVAL_MAX_BYTES
    )
    if set(checkpoint_envelope) != {"manifest", "manifest_sha256"}:
        raise ValueError("Sona checkpoint manifest envelope is invalid")
    checkpoint = _report_object(checkpoint_envelope.get("manifest"), "checkpoint manifest")
    checkpoint_sha256 = _report_string(checkpoint_envelope, "manifest_sha256")
    if (
        checkpoint_sha256 != report.checkpoint_manifest_sha256
        or sha256(rfc8785.dumps(cast(dict[str, JsonValue], checkpoint))).hexdigest()
        != checkpoint_sha256
        or checkpoint.get("schema_version") != 3
        or checkpoint.get("format") != "PICKLE_FREE_NUMPY_STATE_V1"
        or checkpoint.get("dataset_approval_sha256") != report.dataset_approval_sha256
        or checkpoint.get("dataset_bundle_sha256") != report.dataset_bundle_sha256
        or checkpoint.get("dataset_manifest_sha256") != dataset.train_manifest_sha256
        or checkpoint.get("source_approval_sha256") != source.approval_sha256
        or checkpoint.get("data_classification") != "APPROVED_OWNER_SAFE"
        or checkpoint.get("tokenizer_sha256") != report.tokenizer_manifest_sha256
        or checkpoint.get("quality_provenance_eligible") is not True
        or checkpoint.get("quality_eligible") is not False
    ):
        raise ValueError("Sona checkpoint does not match final evaluation ancestry")
    weights = checkpoint.get("weights")
    if not isinstance(weights, list) or not 1 <= len(weights) <= 128:
        raise ValueError("Sona checkpoint weight manifest is invalid")
    aggregate = sha256()
    seen_names: set[str] = set()
    for raw_entry in weights:
        entry = _report_object(raw_entry, "checkpoint weight")
        if set(entry) != {"name", "file", "sha256", "dtype", "shape"}:
            raise ValueError("Sona checkpoint weight manifest keys are invalid")
        name = _report_string(entry, "name")
        file_name = _report_string(entry, "file")
        digest = _report_string(entry, "sha256")
        _validate_sha256(digest, "checkpoint weight sha256")
        if (
            name in seen_names
            or Path(file_name).name != file_name
            or not file_name.endswith(".npy")
        ):
            raise ValueError("Sona checkpoint weight identity is invalid")
        seen_names.add(name)
        payload = _bounded_evaluation_read(
            checkpoint_directory / "weights" / file_name,
            max_bytes=536_870_912,
        )
        if sha256(payload).hexdigest() != digest:
            raise ValueError("Sona checkpoint weight bytes do not match their manifest")
        _verify_numpy_weight(
            payload,
            dtype=_report_string(entry, "dtype"),
            shape=entry.get("shape"),
        )
        aggregate.update(name.encode("utf-8"))
        aggregate.update(bytes.fromhex(digest))
    if aggregate.hexdigest() != checkpoint.get("weights_sha256"):
        raise ValueError("Sona checkpoint aggregate weight hash mismatch")

    artifact_bytes = _bounded_evaluation_read(artifact_path, max_bytes=536_870_912)
    if sha256(artifact_bytes).hexdigest() != report.artifact_sha256:
        raise ValueError("Sona ONNX artifact hash mismatch")
    manifest_envelope = _load_evaluation_object(
        artifact_path.with_suffix(f"{artifact_path.suffix}.manifest.json"),
        max_bytes=SONA_APPROVAL_MAX_BYTES,
    )
    if set(manifest_envelope) != {"manifest", "manifest_sha256"}:
        raise ValueError("Sona ONNX manifest envelope is invalid")
    manifest = _report_object(manifest_envelope.get("manifest"), "ONNX manifest")
    manifest_sha256 = _report_string(manifest_envelope, "manifest_sha256")
    provenance = _report_object(manifest.get("training_provenance"), "training provenance")
    if (
        set(manifest)
        != {
            "schema_version",
            "architecture",
            "opset",
            "inputs",
            "outputs",
            "artifact_sha256",
            "weights_sha256",
            "config_sha256",
            "training_provenance",
        }
        or manifest.get("architecture") != "SONA_LITE_SHARED_GRU_V1"
        or manifest.get("opset") != 20
        or manifest.get("inputs") != list(SONA_ONNX_INPUT_NAMES)
        or manifest.get("outputs") != list(SONA_ONNX_OUTPUT_NAMES)
        or manifest_sha256 != report.artifact_manifest_sha256
        or sha256(rfc8785.dumps(cast(dict[str, JsonValue], manifest))).hexdigest()
        != manifest_sha256
        or manifest.get("artifact_sha256") != report.artifact_sha256
        or manifest.get("weights_sha256") != checkpoint.get("weights_sha256")
        or provenance.get("checkpoint_manifest_sha256") != checkpoint_sha256
        or provenance.get("checkpoint_weights_sha256") != checkpoint.get("weights_sha256")
        or provenance.get("dataset_manifest_sha256") != checkpoint.get("dataset_manifest_sha256")
        or provenance.get("dataset_approval_sha256") != report.dataset_approval_sha256
        or provenance.get("dataset_bundle_sha256") != report.dataset_bundle_sha256
        or provenance.get("tokenizer_sha256") != report.tokenizer_manifest_sha256
        or provenance.get("quality_provenance_eligible") is not True
        or provenance.get("quality_eligible") is not False
    ):
        raise ValueError("Sona ONNX manifest does not match final evaluation ancestry")
    _verify_onnx_model_structure(artifact_bytes)
    commit_envelope = _load_evaluation_object(
        artifact_path.with_suffix(f"{artifact_path.suffix}.commit.json"),
        max_bytes=16_384,
    )
    if set(commit_envelope) != {"commit", "commit_sha256"}:
        raise ValueError("Sona ONNX commit envelope is invalid")
    commit = _report_object(commit_envelope.get("commit"), "ONNX commit")
    commit_sha256 = _report_string(commit_envelope, "commit_sha256")
    expected_commit: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": report.artifact_sha256,
        "model_manifest_sha256": report.artifact_manifest_sha256,
    }
    if (
        commit != expected_commit
        or sha256(rfc8785.dumps(cast(dict[str, JsonValue], commit))).hexdigest() != commit_sha256
    ):
        raise ValueError("Sona ONNX commit marker is invalid")


def _verify_numpy_weight(payload: bytes, *, dtype: str, shape: object) -> None:
    if not payload.startswith(b"\x93NUMPY") or len(payload) < 12:
        raise ValueError("Sona checkpoint weight is not a NumPy array")
    version = tuple(payload[6:8])
    if version == (1, 0):
        header_size = struct.unpack_from("<H", payload, 8)[0]
        header_start = 10
        encoding = "latin1"
    elif version in {(2, 0), (3, 0)}:
        header_size = struct.unpack_from("<I", payload, 8)[0]
        header_start = 12
        encoding = "utf-8" if version == (3, 0) else "latin1"
    else:
        raise ValueError("Sona checkpoint NumPy version is unsupported")
    header_end = header_start + header_size
    if header_size < 1 or header_end > len(payload):
        raise ValueError("Sona checkpoint NumPy header is truncated")
    try:
        header = literal_eval(payload[header_start:header_end].decode(encoding).strip())
    except (SyntaxError, ValueError, UnicodeDecodeError) as error:
        raise ValueError("Sona checkpoint NumPy header is invalid") from error
    if not isinstance(shape, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in shape
    ):
        raise ValueError("Sona checkpoint weight shape is invalid")
    expected_descriptors = {
        "float16": ("<f2", 2),
        "float32": ("<f4", 4),
        "float64": ("<f8", 8),
        "int32": ("<i4", 4),
        "int64": ("<i8", 8),
        "uint8": ("|u1", 1),
        "bool": ("|b1", 1),
    }
    descriptor = expected_descriptors.get(dtype)
    expected_shape = tuple(cast(list[int], shape))
    if (
        not isinstance(header, dict)
        or set(header) != {"descr", "fortran_order", "shape"}
        or descriptor is None
        or header.get("descr") != descriptor[0]
        or header.get("fortran_order") is not False
        or header.get("shape") != expected_shape
    ):
        raise ValueError("Sona checkpoint NumPy header disagrees with its manifest")
    element_count = math.prod(expected_shape)
    if element_count > 134_217_728 or header_end + element_count * descriptor[1] != len(payload):
        raise ValueError("Sona checkpoint NumPy payload size is invalid")


def _verify_onnx_model_structure(payload: bytes) -> None:
    try:
        model = onnx.load_model_from_string(payload)
        onnx.checker.check_model(model, full_check=True)
    except Exception as error:
        raise ValueError("Sona ONNX model fails the official checker") from error
    default_opsets = [
        value.version for value in model.opset_import if value.domain in {"", "ai.onnx"}
    ]
    if default_opsets != [20]:
        raise ValueError("Sona ONNX opset does not match its manifest")
    if (
        tuple(value.name for value in model.graph.input) != SONA_ONNX_INPUT_NAMES
        or tuple(value.name for value in model.graph.output) != SONA_ONNX_OUTPUT_NAMES
        or not model.graph.node
    ):
        raise ValueError("Sona ONNX graph does not match its runtime contract")
    expected_inputs = {
        "history_sids": (onnx.TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS, SONA_SID_DEPTH)),
        "history_actions": (onnx.TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        "history_origins": (onnx.TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        "history_age_buckets": (onnx.TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        "history_mask": (onnx.TensorProto.INT64, (1, SONA_MAX_HISTORY_EVENTS)),
        "candidate_sids": (onnx.TensorProto.INT64, (1, SONA_MAX_CANDIDATES, SONA_SID_DEPTH)),
        "candidate_mask": (onnx.TensorProto.INT64, (1, SONA_MAX_CANDIDATES)),
        "seed": (onnx.TensorProto.INT64, (1,)),
    }
    expected_outputs = {
        "generated_sids": (onnx.TensorProto.INT64, (1, 1, SONA_SID_DEPTH)),
        "generated_log_probabilities": (onnx.TensorProto.FLOAT, (1, 1)),
        "ranking_head_scores": (
            onnx.TensorProto.FLOAT,
            (1, SONA_MAX_CANDIDATES, len(SONA_RANKING_HEADS)),
        ),
        "ranking_scores": (onnx.TensorProto.FLOAT, (1, SONA_MAX_CANDIDATES)),
    }
    if any(
        _onnx_value_info_contract(value) != expected_inputs[value.name]
        for value in model.graph.input
    ) or any(
        _onnx_value_info_contract(value) != expected_outputs[value.name]
        for value in model.graph.output
    ):
        raise ValueError("Sona ONNX tensor contract is invalid")


def _onnx_value_info_contract(value: onnx.ValueInfoProto) -> tuple[int, tuple[int, ...]]:
    tensor = value.type.tensor_type
    if not value.type.HasField("tensor_type") or not tensor.HasField("shape"):
        raise ValueError("Sona ONNX value is not a statically shaped tensor")
    dimensions: list[int] = []
    for dimension in tensor.shape.dim:
        if not dimension.HasField("dim_value") or dimension.dim_value < 1:
            raise ValueError("Sona ONNX tensor dimensions must be positive and static")
        dimensions.append(dimension.dim_value)
    return tensor.elem_type, tuple(dimensions)


def _load_evaluation_object(path: Path, *, max_bytes: int) -> dict[str, object]:
    payload = _bounded_evaluation_read(path, max_bytes=max_bytes)
    try:
        value = cast(object, json.loads(payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Sona evaluation document is invalid JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Sona evaluation document must be an object")
    return cast(dict[str, object], value)


def _bounded_evaluation_read(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise ValueError("Sona evaluation artifact path must not be a symbolic link")
    size = path.stat().st_size
    if not 1 <= size <= max_bytes:
        raise ValueError("Sona evaluation document exceeds the accepted bound")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError("Sona evaluation artifact changed while reading")
    return payload


def _report_metrics(
    value: Mapping[str, object],
) -> tuple[str, str, RankingMetrics, RankingMetrics, SonaSafetyEvidence, int, int]:
    expected = {
        "schema_version",
        "dataset_bundle_sha256",
        "artifact_manifest_sha256",
        "k",
        "baseline",
        "sona",
        "safety",
        "directional_scenarios_passed",
        "directional_scenarios_total",
    }
    if (
        set(value) != expected
        or _report_int(value, "schema_version") != SONA_EVALUATION_SCHEMA_VERSION
        or _report_int(value, "k") != SONA_EVALUATION_K
    ):
        raise ValueError("Sona evaluation metrics document is invalid")
    dataset_bundle_sha256 = _report_string(value, "dataset_bundle_sha256")
    artifact_manifest_sha256 = _report_string(value, "artifact_manifest_sha256")
    _validate_sha256(dataset_bundle_sha256, "metrics dataset_bundle_sha256")
    _validate_sha256(artifact_manifest_sha256, "metrics artifact_manifest_sha256")
    passed = _report_int(value, "directional_scenarios_passed")
    total = _report_int(value, "directional_scenarios_total")
    if not 0 <= passed <= total:
        raise ValueError("Sona evaluation scenario counts are invalid")
    return (
        dataset_bundle_sha256,
        artifact_manifest_sha256,
        _report_ranking_metrics(_report_object(value.get("baseline"), "baseline")),
        _report_ranking_metrics(_report_object(value.get("sona"), "sona")),
        _report_safety(_report_object(value.get("safety"), "safety")),
        passed,
        total,
    )


def _report_ranking_metrics(value: Mapping[str, object]) -> RankingMetrics:
    expected = {
        "signals",
        "positive_ndcg_at_10",
        "coverage_at_10",
        "artist_diversity_at_10",
        "novelty_at_10",
        "repeat_rate_at_10",
        "artist_concentration_hhi_at_10",
        "genre_concentration_hhi_at_10",
        "decoder_coverage",
        "decoder_positive_recall",
    }
    if set(value) != expected:
        raise ValueError("Sona evaluation ranking metrics keys are invalid")
    raw_signals = _report_object(value.get("signals"), "signals")
    if set(raw_signals) != set(ALL_SIGNALS):
        raise ValueError("Sona evaluation signal metrics set is invalid")
    signals: list[tuple[str, SignalMetrics]] = []
    for signal in ALL_SIGNALS:
        raw = _report_object(raw_signals.get(signal), f"signal {signal}")
        if set(raw) != {"labeled_cases", "recall_at_10", "ndcg_at_10", "exposure_at_10"}:
            raise ValueError("Sona evaluation signal metrics keys are invalid")
        labeled_cases = _report_int(raw, "labeled_cases")
        if labeled_cases < 0:
            raise ValueError("Sona evaluation labeled case count is invalid")
        signals.append(
            (
                signal,
                SignalMetrics(
                    labeled_cases,
                    _report_optional_number(raw, "recall_at_10"),
                    _report_optional_number(raw, "ndcg_at_10"),
                    _report_optional_number(raw, "exposure_at_10"),
                ),
            )
        )
    return RankingMetrics(
        signal_metrics=SignalMetricsByName(tuple(signals)),
        positive_ndcg_at_10=_report_number(value, "positive_ndcg_at_10"),
        coverage_at_10=_report_number(value, "coverage_at_10"),
        artist_diversity_at_10=_report_number(value, "artist_diversity_at_10"),
        novelty_at_10=_report_number(value, "novelty_at_10"),
        repeat_rate_at_10=_report_number(value, "repeat_rate_at_10"),
        artist_concentration_hhi_at_10=_report_number(value, "artist_concentration_hhi_at_10"),
        genre_concentration_hhi_at_10=_report_number(value, "genre_concentration_hhi_at_10"),
        decoder_coverage=_report_optional_number(value, "decoder_coverage"),
        decoder_positive_recall=_report_optional_number(value, "decoder_positive_recall"),
    )


def _report_safety(value: Mapping[str, object]) -> SonaSafetyEvidence:
    if set(value) != {"checks"}:
        raise ValueError("Sona evaluation safety document keys are invalid")
    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list):
        raise ValueError("Sona evaluation safety checks are invalid")
    checks: list[SonaSafetyGateEvidence] = []
    expected = {
        "gate_id",
        "artifact_kind",
        "artifact_schema_version",
        "artifact_sha256",
        "covered_request_sha256s",
        "violation_event_sha256s",
        "violation_count",
    }
    for raw in raw_checks:
        check = _report_object(raw, "safety check")
        if set(check) != expected:
            raise ValueError("Sona evaluation safety check keys are invalid")
        covered = check.get("covered_request_sha256s")
        violations = check.get("violation_event_sha256s")
        if (
            not isinstance(covered, list)
            or any(not isinstance(item, str) for item in covered)
            or not isinstance(violations, list)
            or any(not isinstance(item, str) for item in violations)
            or _report_int(check, "violation_count") != len(violations)
        ):
            raise ValueError("Sona evaluation safety coverage is invalid")
        checks.append(
            SonaSafetyGateEvidence(
                gate_id=_report_string(check, "gate_id"),
                artifact_kind=_report_string(check, "artifact_kind"),
                artifact_schema_version=_report_int(check, "artifact_schema_version"),
                artifact_sha256=_report_string(check, "artifact_sha256"),
                covered_request_sha256s=tuple(cast(list[str], covered)),
                violation_event_sha256s=tuple(cast(list[str], violations)),
            )
        )
    return SonaSafetyEvidence(tuple(checks))


def _report_performance(
    value: Mapping[str, object],
) -> tuple[
    str,
    float,
    float,
    float,
    float,
    float,
    str,
    str,
    str,
    int,
    int,
    int,
    int,
    str,
    str,
]:
    expected = {
        "schema_version",
        "raw_performance_evidence_sha256",
        "benchmark_run_sha256",
        "ort_benchmark_evidence_sha256",
        "environment_sha256",
        "warmup_iterations",
        "measured_iterations_per_case",
        "baseline_latency_p50_ms",
        "baseline_latency_p95_ms",
        "sona_latency_p50_ms",
        "sona_latency_p95_ms",
        "latency_ratio_p95",
        "dataset_storage_bytes",
        "artifact_storage_bytes",
        "dataset_storage_manifest_sha256",
        "artifact_storage_manifest_sha256",
    }
    if (
        set(value) != expected
        or _report_int(value, "schema_version") != SONA_EVALUATION_SCHEMA_VERSION
    ):
        raise ValueError("Sona evaluation performance document is invalid")
    digest_fields = (
        "raw_performance_evidence_sha256",
        "benchmark_run_sha256",
        "ort_benchmark_evidence_sha256",
        "environment_sha256",
        "dataset_storage_manifest_sha256",
        "artifact_storage_manifest_sha256",
    )
    digests = {field: _report_string(value, field) for field in digest_fields}
    for field, digest in digests.items():
        _validate_sha256(digest, field)
    warmup = _report_int(value, "warmup_iterations")
    measured = _report_int(value, "measured_iterations_per_case")
    dataset_bytes = _report_int(value, "dataset_storage_bytes")
    artifact_bytes = _report_int(value, "artifact_storage_bytes")
    if warmup < 1 or measured < 1 or dataset_bytes < 0 or artifact_bytes < 0:
        raise ValueError("Sona evaluation performance counts are invalid")
    return (
        digests["raw_performance_evidence_sha256"],
        _report_number(value, "baseline_latency_p50_ms"),
        _report_number(value, "baseline_latency_p95_ms"),
        _report_number(value, "sona_latency_p50_ms"),
        _report_number(value, "sona_latency_p95_ms"),
        _report_number(value, "latency_ratio_p95"),
        digests["benchmark_run_sha256"],
        digests["ort_benchmark_evidence_sha256"],
        digests["environment_sha256"],
        warmup,
        measured,
        dataset_bytes,
        artifact_bytes,
        digests["dataset_storage_manifest_sha256"],
        digests["artifact_storage_manifest_sha256"],
    )


def _report_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Sona evaluation report {field} must be an object")
    return cast(dict[str, object], value)


def _report_string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str):
        raise ValueError(f"Sona evaluation report {field} must be a string")
    return item


def _report_int(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"Sona evaluation report {field} must be an integer")
    return item


def _report_number(value: Mapping[str, object], field: str) -> float:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
        raise ValueError(f"Sona evaluation report {field} must be a finite number")
    return float(item)


def _report_optional_number(value: Mapping[str, object], field: str) -> float | None:
    item = value.get(field)
    return None if item is None else _report_number(value, field)


def _ranking_metrics(
    cases: tuple[PairedRankingCase, ...], catalog_recording_count: int, *, use_sona: bool
) -> RankingMetrics:
    signal_metrics: dict[str, SignalMetrics] = {}
    positive_ndcg_values: list[float] = []
    covered: set[UUID] = set()
    diversity_values: list[float] = []
    novelty_values: list[float] = []
    repeat_values: list[float] = []
    artist_concentration_values: list[float] = []
    genre_concentration_values: list[float] = []
    generated_covered: set[UUID] = set()
    decoder_positive_recall_values: list[float] = []
    for signal in ALL_SIGNALS:
        recall_values: list[float] = []
        ndcg_values: list[float] = []
        exposure_values: list[float] = []
        for case in cases:
            ranked = (case.sona_ranked_ids if use_sona else case.baseline_ranked_ids)[
                :SONA_EVALUATION_K
            ]
            relevant = getattr(case.relevance, signal)
            if not relevant:
                continue
            if signal in POSITIVE_SIGNALS:
                recall_values.append(len(set(ranked) & relevant) / len(relevant))
                ndcg = _ndcg(ranked, relevant)
                ndcg_values.append(ndcg)
                positive_ndcg_values.append(ndcg)
            else:
                exposure_values.append(len(set(ranked) & relevant) / max(1, len(ranked)))
        signal_metrics[signal] = SignalMetrics(
            labeled_cases=max(len(recall_values), len(exposure_values)),
            recall_at_10=_mean(recall_values) if recall_values else None,
            ndcg_at_10=_mean(ndcg_values) if ndcg_values else None,
            exposure_at_10=_mean(exposure_values) if exposure_values else None,
        )
    for case in cases:
        ranked = (case.sona_ranked_ids if use_sona else case.baseline_ranked_ids)[
            :SONA_EVALUATION_K
        ]
        covered.update(ranked)
        metadata = {track.recording_id: track for track in case.tracks}
        artists = [metadata[value].artist_key for value in ranked]
        genres = [genre for value in ranked for genre in metadata[value].genre_keys]
        diversity_values.append(0.0 if not artists else len(set(artists)) / len(artists))
        novelty_values.append(
            0.0
            if not ranked
            else sum(metadata[value].prior_play_count == 0 for value in ranked) / len(ranked)
        )
        repeat_values.append(
            0.0
            if not ranked
            else sum(metadata[value].prior_recommendation_count_7d > 0 for value in ranked)
            / len(ranked)
        )
        artist_concentration_values.append(_hhi(artists))
        genre_concentration_values.append(_hhi(genres))
        if use_sona:
            generated = set(case.sona_generated_ids)
            generated_covered.update(generated)
            positive = {
                outcome.recording_id
                for outcome in case.outcomes
                if outcome.signal in POSITIVE_SIGNALS
            }
            if positive:
                decoder_positive_recall_values.append(len(generated & positive) / len(positive))
    return RankingMetrics(
        signal_metrics=SignalMetricsByName(
            tuple((signal, signal_metrics[signal]) for signal in ALL_SIGNALS)
        ),
        positive_ndcg_at_10=_mean(positive_ndcg_values) if positive_ndcg_values else 0.0,
        coverage_at_10=round(min(1.0, len(covered) / catalog_recording_count), 8),
        artist_diversity_at_10=_mean(diversity_values),
        novelty_at_10=_mean(novelty_values),
        repeat_rate_at_10=_mean(repeat_values),
        artist_concentration_hhi_at_10=_mean(artist_concentration_values),
        genre_concentration_hhi_at_10=_mean(genre_concentration_values),
        decoder_coverage=(
            min(1.0, len(generated_covered) / catalog_recording_count) if use_sona else None
        ),
        decoder_positive_recall=(
            _mean(decoder_positive_recall_values)
            if use_sona and decoder_positive_recall_values
            else None
        ),
    )


def _gate_verdict(
    baseline: RankingMetrics,
    sona: RankingMetrics,
    *,
    safety: SonaSafetyEvidence,
    directional_passed: int,
    directional_total: int,
    sona_latency_p95_ms: float,
    latency_ratio_p95: float,
) -> SonaGateVerdict:
    failures: list[str] = []
    if any(baseline.signal_metrics[signal].labeled_cases == 0 for signal in ALL_SIGNALS):
        failures.append("MISSING_SIGNAL_LABELS")
    if directional_passed != directional_total:
        failures.append("DIRECTIONAL_SCENARIO_FAILURE")
    if safety.total:
        failures.append("SAFETY_PRIVACY_OR_REPLAY_VIOLATION")
    if baseline.positive_ndcg_at_10 - sona.positive_ndcg_at_10 > SONA_MAX_NDCG_REGRESSION:
        failures.append("NDCG_REGRESSION")
    if (
        baseline.artist_diversity_at_10 - sona.artist_diversity_at_10
        > SONA_MAX_DIVERSITY_REGRESSION
    ):
        failures.append("DIVERSITY_REGRESSION")
    if (
        sona.artist_concentration_hhi_at_10 - baseline.artist_concentration_hhi_at_10
        > SONA_MAX_ARTIST_CONCENTRATION_INCREASE
    ):
        failures.append("ARTIST_CONCENTRATION_INCREASE")
    if sona.repeat_rate_at_10 - baseline.repeat_rate_at_10 > SONA_MAX_REPEAT_RATE_INCREASE:
        failures.append("REPEAT_RATE_INCREASE")
    if sona_latency_p95_ms > SONA_MAX_P95_LATENCY_MS:
        failures.append("LATENCY_ABSOLUTE")
    if latency_ratio_p95 > SONA_MAX_P95_LATENCY_RATIO:
        failures.append("LATENCY_RATIO")
    return SonaGateVerdict(not failures, tuple(failures))


def _ndcg(ranked: tuple[UUID, ...], relevant: frozenset[UUID]) -> float:
    positions = [index for index, value in enumerate(ranked, 1) if value in relevant]
    dcg = sum(1.0 / math.log2(position + 1) for position in positions)
    ideal = sum(
        1.0 / math.log2(position + 1)
        for position in range(1, min(len(relevant), SONA_EVALUATION_K) + 1)
    )
    return 0.0 if ideal == 0.0 else dcg / ideal


def _hhi(values: list[str]) -> float:
    if not values:
        return 0.0
    counts = {value: values.count(value) for value in set(values)}
    return sum((count / len(values)) ** 2 for count in counts.values())


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _percentile(values: tuple[float, ...], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _ranking_metrics_document(value: RankingMetrics) -> dict[str, JsonValue]:
    return {
        "signals": {
            signal: {
                "labeled_cases": metrics.labeled_cases,
                "recall_at_10": metrics.recall_at_10,
                "ndcg_at_10": metrics.ndcg_at_10,
                "exposure_at_10": metrics.exposure_at_10,
            }
            for signal, metrics in sorted(value.signal_metrics.items())
        },
        "positive_ndcg_at_10": value.positive_ndcg_at_10,
        "coverage_at_10": value.coverage_at_10,
        "artist_diversity_at_10": value.artist_diversity_at_10,
        "novelty_at_10": value.novelty_at_10,
        "repeat_rate_at_10": value.repeat_rate_at_10,
        "artist_concentration_hhi_at_10": value.artist_concentration_hhi_at_10,
        "genre_concentration_hhi_at_10": value.genre_concentration_hhi_at_10,
        "decoder_coverage": value.decoder_coverage,
        "decoder_positive_recall": value.decoder_positive_recall,
    }


def _safety_document(value: SonaSafetyEvidence) -> dict[str, JsonValue]:
    return {
        "checks": [
            {
                "gate_id": check.gate_id,
                "artifact_kind": check.artifact_kind,
                "artifact_schema_version": check.artifact_schema_version,
                "artifact_sha256": check.artifact_sha256,
                "covered_request_sha256s": list(check.covered_request_sha256s),
                "violation_event_sha256s": list(check.violation_event_sha256s),
                "violation_count": check.violation_count,
            }
            for check in value.checks
        ]
    }


def _gate_policy_document() -> dict[str, JsonValue]:
    return {
        "required_scenario_direction_pass_rate": 1.0,
        "mandatory_filter_violation_count": 0,
        "owner_isolation_violation_count": 0,
        "shadow_impression_count": 0,
        "algorithmic_replay_mismatch_count": 0,
        "maximum_ndcg_at_10_regression": SONA_MAX_NDCG_REGRESSION,
        "maximum_diversity_regression": SONA_MAX_DIVERSITY_REGRESSION,
        "maximum_artist_concentration_increase": SONA_MAX_ARTIST_CONCENTRATION_INCREASE,
        "maximum_repeat_rate_increase": SONA_MAX_REPEAT_RATE_INCREASE,
        "maximum_p95_latency_ms": SONA_MAX_P95_LATENCY_MS,
        "maximum_p95_latency_ratio_to_p11": SONA_MAX_P95_LATENCY_RATIO,
        "activation_requires_r1b_pass": True,
        "activation_requires_explicit_user_decision": True,
        "rollback_on_any_safety_or_privacy_violation": True,
    }


def compute_sona_ranking_sha256(values: tuple[UUID, ...]) -> str:
    return sha256(
        rfc8785.dumps(
            {
                "schema_version": SONA_EVALUATION_SCHEMA_VERSION,
                "recording_ids": [str(value) for value in values],
            }
        )
    ).hexdigest()


def compute_recommendation_ranking_sha256(values: tuple[RankedRecommendation, ...]) -> str:
    """Hash the complete persisted ranking, including scores, reasons and provenance."""

    return recommendation_ranking_sha256(values)


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona evaluation {field} is not a lowercase SHA-256 digest")


__all__ = (
    "ALL_SIGNALS",
    "NEGATIVE_SIGNALS",
    "POSITIVE_SIGNALS",
    "REQUIRED_DIRECTIONAL_SCENARIOS",
    "SONA_CONTRACT_POLICY_SHA256",
    "SONA_DIRECTIONAL_EXPECTED_SHA256",
    "SONA_SAFETY_GATE_IDS",
    "AttributedOutcome",
    "AttributedRelevance",
    "DirectionalScenarioEvidence",
    "EvaluationTrackMetadata",
    "PairedEvaluationReport",
    "PairedLatencySample",
    "PairedRankingCase",
    "PairedRankingCaseEvidence",
    "RankingMetrics",
    "SignalMetrics",
    "SignalMetricsByName",
    "SonaGateVerdict",
    "SonaPerformanceEvidence",
    "SonaSafetyEvidence",
    "SonaSafetyGateEvidence",
    "build_paired_ranking_case",
    "compute_recommendation_ranking_sha256",
    "compute_sona_contract_policy_sha256",
    "compute_sona_directional_scenario_bundle_sha256",
    "compute_sona_evaluation_case_bundle_sha256",
    "compute_sona_performance_evidence_sha256",
    "compute_sona_ranking_sha256",
    "compute_sona_safety_evidence_bundle_sha256",
    "evaluate_paired_rankings",
    "load_quality_eligible_sona_artifact",
    "paired_evaluation_report_document",
    "paired_evaluation_report_from_document",
    "sona_outcome_evidence_document",
    "sona_paired_execution_evidence_document",
    "sona_performance_evidence_document",
    "sona_safety_evidence_document",
    "verify_quality_eligible_sona_artifact",
)

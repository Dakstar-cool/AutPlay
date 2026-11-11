"""P11 native captures -> temporal schema 2 -> standard Sona training examples.

This is an offline transformation, not a consent authority or training approval.
The caller must fence reads and all downstream uses with current consent/erasure.
V1 fails closed on unproven history and portable facts (no guessed deduplication).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785

from autplay.application.adaptive_recommendations import (
    adaptive_profile_document,
    canonical_sha256,
    temporal_evidence_document,
)
from autplay.application.recommendations import (
    RecommendationPipelineRunner,
    baseline_pipeline_definition,
    recommendation_availability_snapshot_document,
    recommendation_input_snapshot_document,
    recommendation_policy_snapshot_document,
    recommendation_snapshot_limit,
    request_document,
)
from autplay.application.sona import build_sona_inference_request
from autplay.application.sona_shadow import recommendation_ranking_document
from autplay.application.sona_source_materialization import snapshot_track_from_document
from autplay.application.sona_source_reconstruction import (
    AcceptedSyncEvent,
    classify_sona_0026_listening_outcome,
    normalize_accepted_sync_event,
)
from autplay.application.sona_training import (
    build_sona_training_example,
    verify_sona_teacher_snapshot,
)
from autplay.application.temporal_snapshot import build_temporal_snapshot_document
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    DimensionKind,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    project_temporal_profile,
)
from autplay.domain.recommendations import (
    JsonValue,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationSnapshotRef,
    RecommendationSurface,
)
from autplay.domain.sona import (
    SONA_MAX_CANDIDATES,
    UNKNOWN_SEMANTIC_ID,
    SonaInferenceRequest,
    SonaSemanticId,
)
from autplay.domain.sona_training import (
    SONA_MAX_LABEL_DELAY_MS,
    SonaObservedOutcome,
    SonaTeacherSnapshot,
    SonaTrainingExample,
)

ADAPTER_VERSION = "AUTPLAY_NATIVE_DATASET_ADAPTER_V1"
NAMESPACE = uuid5(NAMESPACE_URL, "https://autplay.local/sona/native-dataset/v1")
MAX_CAPTURES = 4096
MAX_BATCH_BYTES = 64 * 1024 * 1024


class NativeDatasetRejected(ValueError):
    """A stable, non-disclosing reason suitable for an aggregate report."""


@dataclass(frozen=True, slots=True)
class NativeCaptureRow:
    request_id: UUID
    owner_user_id: UUID
    consent_revision: int
    created_at: datetime
    retained_until: datetime
    payload: bytes
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class NativeCapture:
    row: NativeCaptureRow
    document: dict[str, Any]
    query: RecommendationQuery
    baseline: RecommendationInputSnapshot
    facts: tuple[dict[str, Any], ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeTrainingCase:
    """Private owner-bearing inputs; never serialize these in an aggregate log."""

    capture: NativeCapture
    outcome_capture: NativeCapture
    outcome_event: AcceptedSyncEvent
    evidence: tuple[TemporalEvidence, ...]
    temporal_document: dict[str, JsonValue]
    provenance_document: dict[str, JsonValue]
    split: str
    excluded_event_ids: frozenset[UUID]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise NativeDatasetRejected(code)


def _ms(value: datetime | str) -> int:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    _require(parsed.tzinfo is not None, "NAIVE_TIMESTAMP")
    return int(parsed.timestamp() * 1000)


def _integer(value: Any) -> int:
    _require(type(value) is int and value >= 0, "INVALID_INTEGER")
    return int(value)


def _digest(value: Any) -> str:
    _require(
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
        "INVALID_SHA256",
    )
    return str(value)


def decode_native_capture(
    row: NativeCaptureRow,
    *,
    current_revision: int,
    now: datetime,
) -> NativeCapture:
    """Validate a DB/consent-bound capture without accepting its source facts yet."""
    _require(0 < len(row.payload) <= 16 * 1024 * 1024, "CAPTURE_SIZE_LIMIT")
    _require(sha256(row.payload).hexdigest() == row.payload_sha256, "CAPTURE_HASH_MISMATCH")
    _require(row.consent_revision == current_revision > 0, "CONSENT_REVISION_MISMATCH")
    _require(_ms(row.created_at) <= _ms(now) < _ms(row.retained_until), "CAPTURE_EXPIRED")
    _require(
        _ms(row.retained_until) <= _ms(row.created_at) + 180 * 86400000,
        "CAPTURE_RETENTION_INVALID",
    )
    doc = json.loads(row.payload)
    _require(isinstance(doc, dict) and rfc8785.dumps(doc) == row.payload, "NONCANONICAL_CAPTURE")
    _require(
        doc["schema"] == "AUTPLAY_P11_NATIVE_COLLECTION_V1"
        and doc["producer"] == "ORIGINAL_REQUEST_TRANSACTION"
        and doc["quality_eligible"] is False
        and doc["admission_blocker"] == "TEMPORAL_SCHEMA2_DATASET_ADAPTER_REQUIRED",
        "UNSUPPORTED_CAPTURE_CONTRACT",
    )
    _require(
        doc["request_id"] == str(row.request_id)
        and doc["owner_user_id"] == str(row.owner_user_id)
        and _integer(doc["consent_revision"]) == row.consent_revision
        and datetime.fromisoformat(doc["cutoff_at"]) == row.created_at,
        "CAPTURE_ROW_BINDING_MISMATCH",
    )
    raw = doc["request"]
    query = RecommendationQuery(
        user_id=UUID(raw["user_id"]),
        surface=RecommendationSurface(raw["surface"]),
        context=raw["context"],
        limit=_integer(raw["limit"]),
        exploration=raw["exploration"],
        seed=_integer(raw["seed"]),
        schema_version=_integer(raw["schema_version"]),
        canonicalization_version=_integer(raw["canonicalization_version"]),
        shadow=raw["shadow"],
    )
    _require(query.user_id == row.owner_user_id and query.shadow is False, "INVALID_REQUEST_OWNER")
    _require(query.surface.value == doc["surface"], "SURFACE_MISMATCH")
    snap = raw["snapshot"]
    baseline = RecommendationInputSnapshot(
        reference=RecommendationSnapshotRef(
            snapshot_id=UUID(snap["snapshot_id"]),
            input_snapshot_sha256=_digest(snap["input_snapshot_sha256"]),
            interaction_watermark=_integer(snap["interaction_watermark"]),
            catalog_snapshot=_integer(snap["catalog_snapshot"]),
            availability_snapshot=_digest(snap["availability_snapshot"]),
            policy_snapshot_sha256=_digest(snap["policy_snapshot_sha256"]),
        ),
        tracks=tuple(snapshot_track_from_document(t) for t in doc["input_snapshot"]["tracks"]),
        retained_until=row.retained_until,
        policy_version=str(_integer(doc["input_snapshot"]["schema_version"])),
    )
    pipeline = baseline_pipeline_definition(raw["pipeline"]["version"])
    _require(
        request_document(query, pipeline, baseline.reference) == raw
        and canonical_sha256(raw) == doc["request_sha256"],
        "REQUEST_HASH_OR_PIPELINE_MISMATCH",
    )
    expected_snapshot = recommendation_input_snapshot_document(
        query.user_id,
        interaction_watermark=baseline.reference.interaction_watermark,
        tracks=baseline.tracks,
        policy_version=baseline.policy_version,
    )
    _require(
        expected_snapshot == doc["input_snapshot"]
        and canonical_sha256(expected_snapshot)
        == doc["input_snapshot_sha256"]
        == baseline.reference.input_snapshot_sha256,
        "SNAPSHOT_HASH_MISMATCH",
    )
    _require(
        canonical_sha256(recommendation_availability_snapshot_document(baseline.tracks))
        == baseline.reference.availability_snapshot
        and canonical_sha256(recommendation_policy_snapshot_document(baseline.tracks))
        == baseline.reference.policy_snapshot_sha256,
        "CANDIDATE_POLICY_MISMATCH",
    )
    _require(
        doc["candidate_policy_cap"] == recommendation_snapshot_limit(baseline.policy_version)
        and doc["source_fact_cap"] == 10000
        and _integer(doc["candidate_count"])
        == len(baseline.tracks)
        <= recommendation_snapshot_limit(baseline.policy_version),
        "CAPTURE_BOUND_MISMATCH",
    )
    facts = tuple(doc["source_facts"])
    _require(len(facts) <= 10000 and all(isinstance(f, dict) for f in facts), "SOURCE_LIMIT")
    status = doc["collection_status"]
    _require(status in {"COLLECTED", "SOURCE_LIMIT", "SOURCE_PROVENANCE_GAP"}, "UNKNOWN_STATUS")
    blockers: set[str] = set()
    if status == "SOURCE_LIMIT":
        blockers.add("SOURCE_LIMIT")
    else:
        _require(_integer(doc["source_count_lower_bound"]) == len(facts), "SOURCE_COUNT_MISMATCH")
    if status == "SOURCE_PROVENANCE_GAP" or any(
        f.get("kind") == "SYNC" and f.get("provenance_matches") is not True for f in facts
    ):
        blockers.add("SOURCE_PROVENANCE_GAP")
    if any(f.get("kind") != "SYNC" for f in facts):
        blockers.add("PORTABLE_DEDUPLICATION_REQUIRED")
    authority = RecommendationPipelineRunner()
    ranking = recommendation_ranking_document(authority.run(query, baseline, pipeline))
    _require(ranking == doc["ranking"], "RANKING_REPLAY_MISMATCH")
    count = len(authority.filter_snapshot_tracks(query, baseline))
    if not ranking["items"]:
        blockers.add("EMPTY_RANKING")
    if not count:
        blockers.add("NO_ELIGIBLE_CANDIDATES")
    if count > SONA_MAX_CANDIDATES:
        blockers.add("SONA_CANDIDATE_CAP_EXCEEDED")
    return NativeCapture(row, doc, query, baseline, facts, tuple(sorted(blockers)))


def _validate_fact(capture: NativeCapture, fact: dict[str, Any]) -> None:
    _require(fact["kind"] == "SYNC" and fact["provenance_matches"] is True, "UNPROVEN_FACT")
    _require(
        fact["operation"] == "UPSERT"
        and _integer(fact["schema_version"]) == 1
        and 0
        < _integer(fact["server_sequence"])
        <= capture.baseline.reference.interaction_watermark
        and _integer(fact["device_sequence"]) > 0
        and _ms(fact["received_at"]) <= _ms(capture.row.created_at)
        and _ms(fact["created_at"]) <= _ms(capture.row.created_at),
        "FACT_OUTSIDE_CAPTURE",
    )
    _digest(fact["source_request_sha256"])
    UUID(fact["event_id"])
    UUID(fact["device_id"])
    if fact["event_type"] != "USER_TRACK_PREFERENCE_SET":
        aggregate_type = (
            "LISTENING_EVENT"
            if fact["event_type"] == "LISTENING_EVENT_RECORDED"
            else "USER_INTERACTION_EVENT"
        )
        _require(
            fact["aggregate_id"] == fact["event_id"]
            and fact["aggregate_type"] == aggregate_type
            and fact["payload"]["interaction_type"] == fact["event_type"],
            "FACT_AGGREGATE_MISMATCH",
        )


def _accepted_event(capture: NativeCapture, fact: dict[str, Any]) -> AcceptedSyncEvent:
    _validate_fact(capture, fact)
    payload = fact["payload"]
    kind = fact["event_type"]
    context = fact["recording_context"]
    recording = UUID(context["recording_id"])
    dimensions: list[TemporalDimension] = []
    for key, dimension in (
        ("artist_key", DimensionKind.CANONICAL_ARTIST_ID),
        ("release_key", DimensionKind.CANONICAL_RELEASE_ID),
    ):
        if context[key] is not None:
            dimensions.append(TemporalDimension(dimension, context[key]))
    fields: dict[str, Any] = {}
    causal = payload.get("recommendation")
    if causal is not None:
        aliases = (
            causal.get("impression_event_server_id"),
            causal.get("impression_event_local_id"),
        )
        _require(not all(aliases) or aliases[0] == aliases[1], "IMPRESSION_ALIAS_CONFLICT")
        _require(causal["recording_id"] == str(recording), "ATTRIBUTION_RECORDING_MISMATCH")
        fields.update(
            recommendation_request_id=UUID(causal["recommendation_request_id"]),
            impression_event_id=UUID(
                causal.get("impression_event_server_id") or causal["impression_event_local_id"]
            ),
            recommendation_source_rank=_integer(causal["source_rank"]),
        )
    if kind == "USER_TRACK_PREFERENCE_SET":
        binding = fact["preference_binding"]
        _require(
            binding["recording_id"] == str(recording)
            and binding["deleted_at"] is None
            and binding["user_track_ref_id"] == payload["local_user_track_ref_id"],
            "PREFERENCE_BINDING_MISMATCH",
        )
        fields.update(
            preference=payload["preference"], excluded_from_taste=payload["excluded_from_taste"]
        )
    else:
        projection = (
            fact["listening_projection"]
            if kind == "LISTENING_EVENT_RECORDED"
            else fact["interaction_projection"]
        )
        _require(
            projection["user_id"] == str(capture.row.owner_user_id)
            and projection["device_id"] == fact["device_id"]
            and projection["recording_id"] == str(recording),
            "PROJECTION_OWNER_OR_RECORDING_MISMATCH",
        )
        if kind == "LISTENING_EVENT_RECORDED":
            _require(
                projection["listening_event_id"] == fact["event_id"]
                and _ms(projection["started_at"]) == _ms(fact["occurred_at"])
                and projection["recommendation_request_id"]
                == (None if causal is None else causal["recommendation_request_id"]),
                "LISTENING_PROJECTION_MISMATCH",
            )
            for key in ("event_origin", "played_ms", "completion_ratio", "excluded_from_taste"):
                _require(projection[key] == payload[key], "LISTENING_PROJECTION_MISMATCH")
                fields[key] = payload[key]
        elif kind == "RECOMMENDATION_FEEDBACK_RECORDED":
            _require(
                projection["interaction_id"] == fact["event_id"]
                and projection["payload"] == payload,
                "FEEDBACK_PROJECTION_MISMATCH",
            )
            fields["feedback_type"] = payload["feedback_type"]
        else:
            raise NativeDatasetRejected("UNSUPPORTED_EVENT_TYPE")
    return AcceptedSyncEvent(
        source_event_id=UUID(fact["event_id"]),
        owner_user_id=capture.row.owner_user_id,
        device_id=UUID(fact["device_id"]),
        device_sequence=fact["device_sequence"],
        server_sequence=fact["server_sequence"],
        source_event_type=kind,
        recording_id=recording,
        dimensions=tuple(dimensions),
        occurred_at_ms=_ms(fact["occurred_at"]),
        received_at_ms=_ms(fact["received_at"]),
        source_request_sha256=fact["source_request_sha256"],
        **fields,
    )


def _history(capture: NativeCapture) -> tuple[TemporalEvidence, ...]:
    evidence: list[TemporalEvidence] = []
    identities: set[str] = set()
    sequences: set[int] = set()
    for fact in capture.facts:
        _validate_fact(capture, fact)
        _require(
            fact["event_id"] not in identities and fact["server_sequence"] not in sequences,
            "DUPLICATE_FACT",
        )
        identities.add(fact["event_id"])
        sequences.add(fact["server_sequence"])
        if fact["event_type"] == "RECOMMENDATION_IMPRESSION_RECORDED":
            continue
        event = _accepted_event(capture, fact)
        if event.recommendation_request_id is not None:
            _verify_impression(capture, event, cutoff=0, watermark=0)
        evidence.extend(
            normalize_accepted_sync_event(
                event,
                server_profile_id=uuid5(NAMESPACE, "offline-owner:" + str(event.owner_user_id)),
                lineage_sha256=sha256(ADAPTER_VERSION.encode()).hexdigest(),
                evidence_namespace=NAMESPACE,
            )
        )
    return tuple(sorted(evidence, key=lambda e: e.evidence_id.hex))


def _causal_outcome(
    capture: NativeCapture,
    event_capture: NativeCapture,
    fact: dict[str, Any],
) -> AcceptedSyncEvent:
    _require(
        event_capture.document["collection_status"] == "COLLECTED"
        and all(
            f.get("kind") == "SYNC" and f.get("provenance_matches") is True
            for f in event_capture.facts
        ),
        "OUTCOME_CAPTURE_PROVENANCE_GAP",
    )
    event = _accepted_event(event_capture, fact)
    cutoff = _ms(capture.row.created_at)
    _require(
        event.owner_user_id == capture.row.owner_user_id
        and event_capture.row.consent_revision == capture.row.consent_revision
        and event.event_origin == "RECOMMENDED"
        and not event.excluded_from_taste
        and event.recommendation_request_id == capture.row.request_id
        and event.server_sequence > capture.baseline.reference.interaction_watermark
        and cutoff
        < event.occurred_at_ms
        <= event.received_at_ms
        <= cutoff + SONA_MAX_LABEL_DELAY_MS,
        "NONCAUSAL_OUTCOME",
    )
    rank = event.recommendation_source_rank
    items = capture.document["ranking"]["items"]
    _require(
        any(
            i["source_rank"] == rank and i["recording_id"] == str(event.recording_id) for i in items
        ),
        "OUTCOME_NOT_SERVED",
    )
    _verify_impression(
        event_capture,
        event,
        cutoff=cutoff,
        watermark=capture.baseline.reference.interaction_watermark,
    )
    return event


def _verify_impression(
    event_capture: NativeCapture,
    event: AcceptedSyncEvent,
    *,
    cutoff: int,
    watermark: int,
) -> None:
    impressions = [
        f for f in event_capture.facts if f.get("event_id") == str(event.impression_event_id)
    ]
    _require(len(impressions) == 1, "IMPRESSION_NOT_RETAINED")
    impression = impressions[0]
    _validate_fact(event_capture, impression)
    projection = impression["interaction_projection"]
    attribution = impression["payload"]["recommendation"]
    _require(
        impression["event_type"] == "RECOMMENDATION_IMPRESSION_RECORDED"
        and projection["interaction_id"] == impression["event_id"]
        and projection["event_type"] == impression["event_type"]
        and projection["payload"] == impression["payload"]
        and projection["user_id"] == str(event.owner_user_id)
        and projection["device_id"] == impression["device_id"] == str(event.device_id)
        and projection["recommendation_request_id"]
        == attribution["recommendation_request_id"]
        == str(event.recommendation_request_id)
        and projection["recording_id"] == attribution["recording_id"] == str(event.recording_id)
        and projection["source_rank"]
        == attribution["source_rank"]
        == event.recommendation_source_rank
        and projection["presentation_id"] is not None
        and projection["presentation_id"] == attribution["presentation_id"]
        and cutoff <= _ms(impression["occurred_at"]) <= event.occurred_at_ms
        and _ms(impression["received_at"]) <= event.received_at_ms
        and watermark < impression["server_sequence"] < event.server_sequence
        and impression["device_sequence"] < event.device_sequence,
        "IMPRESSION_CAUSALITY_MISMATCH",
    )


def build_native_training_cases(
    captures: Sequence[NativeCapture],
    *,
    train_end_ms: int,
    validation_end_ms: int,
    excluded_event_ids: frozenset[UUID] = frozenset(),
) -> tuple[tuple[NativeTrainingCase, ...], dict[str, JsonValue]]:
    """One earliest finalized attributed listen per request; purge split-crossing labels.

    Splits use explicit precommitted time boundaries, never a random row shuffle.
    All label-source facts must come from another retained original capture.
    """
    _require(0 < train_end_ms < validation_end_ms, "INVALID_SPLIT_BOUNDARIES")
    _require(
        len(captures) <= MAX_CAPTURES
        and sum(len(c.row.payload) for c in captures) <= MAX_BATCH_BYTES,
        "BATCH_LIMIT",
    )
    _require(len({c.row.request_id for c in captures}) == len(captures), "DUPLICATE_CAPTURE")
    counters: Counter[str] = Counter()
    cases: list[NativeTrainingCase] = []
    gaps: set[tuple[UUID, str]] = set()
    # Compare immutable source identity across repeated captures; catalog bindings
    # may legitimately change, so history always uses its own frozen binding.
    seen: dict[tuple[UUID, str], str] = {}
    conflict_owners: set[UUID] = set()
    for capture in captures:
        for fact in capture.facts:
            if fact.get("kind") != "SYNC":
                continue
            key = (capture.row.owner_user_id, fact["event_id"])
            identity = canonical_sha256(
                {
                    k: fact[k]
                    for k in (
                        "payload",
                        "source_request_sha256",
                        "device_id",
                        "device_sequence",
                        "server_sequence",
                        "event_type",
                        "occurred_at",
                        "received_at",
                        "provenance_matches",
                    )
                }
            )
            if key in seen and seen[key] != identity:
                conflict_owners.add(capture.row.owner_user_id)
            seen[key] = identity
            if fact["provenance_matches"] is not True:
                gaps.add(key)
    for capture in sorted(captures, key=lambda c: (c.row.created_at, c.row.request_id.hex)):
        blockers = set(capture.blockers)
        if capture.row.owner_user_id in conflict_owners:
            blockers.add("CONFLICTING_REPEATED_FACT")
        if any(f.get("event_id") in {str(e) for e in excluded_event_ids} for f in capture.facts):
            blockers.add("EXCLUDED_TEST_EVENT_IN_HISTORY")
        if blockers:
            counters.update(blockers)
            continue
        try:
            evidence = _history(capture)
            outcomes: dict[UUID, tuple[NativeCapture, AcceptedSyncEvent]] = {}
            for later in sorted(captures, key=lambda c: (c.row.created_at, c.row.request_id.hex)):
                if (
                    later.row.owner_user_id != capture.row.owner_user_id
                    or later.row.created_at <= capture.row.created_at
                ):
                    continue
                for fact in later.facts:
                    if (
                        fact.get("kind") != "SYNC"
                        or fact.get("event_type") != "LISTENING_EVENT_RECORDED"
                    ):
                        continue
                    causal = fact["payload"].get("recommendation")
                    if not isinstance(causal, dict) or causal.get(
                        "recommendation_request_id"
                    ) != str(capture.row.request_id):
                        continue
                    if UUID(fact["event_id"]) in excluded_event_ids:
                        counters["EXCLUDED_TEST_OUTCOME"] += 1
                        continue
                    event = _causal_outcome(capture, later, fact)
                    outcomes.setdefault(event.source_event_id, (later, event))
            if not outcomes:
                counters["NO_ATTRIBUTED_OUTCOME"] += 1
                continue
            later, event = min(
                outcomes.values(),
                key=lambda p: (p[1].received_at_ms, p[1].server_sequence, p[1].source_event_id.hex),
            )
            cutoff = _ms(capture.row.created_at)
            end = cutoff + SONA_MAX_LABEL_DELAY_MS
            if cutoff < train_end_ms:
                split, boundary = "train", train_end_ms
            elif cutoff < validation_end_ms:
                split, boundary = "validation", validation_end_ms
            else:
                split, boundary = "test", None
            if boundary is not None and end >= boundary:
                counters["PURGED_LABEL_WINDOW"] += 1
                continue
            provenance: dict[str, JsonValue] = {
                "adapter_version": ADAPTER_VERSION,
                "source_kind": "NATIVE_P11_CAPTURE_V1",
                "capture_sha256": capture.row.payload_sha256,
                "outcome_capture_sha256": later.row.payload_sha256,
                "p11_request_sha256": capture.document["request_sha256"],
                "outcome_source_event_id": str(event.source_event_id),
                "consent_revision": capture.row.consent_revision,
                "temporal_source": "OFFLINE_DERIVED_FROM_FROZEN_NATIVE_FACTS",
                "persisted_original_temporal_snapshot": False,
                "profile_identity_scheme": "UUID5_NATIVE_OFFLINE_OWNER_V1",
                "exclusions_sha256": canonical_sha256(
                    cast(list[JsonValue], sorted(str(e) for e in excluded_event_ids))
                ),
                "split": split,
                "train_end_ms": train_end_ms,
                "validation_end_ms": validation_end_ms,
                "quality_eligible": False,
            }
            snapshot_id = uuid5(NAMESPACE, canonical_sha256(provenance))
            profile = project_temporal_profile(
                owner_user_id=capture.row.owner_user_id,
                cutoff_at_ms=cutoff,
                interaction_watermark=capture.baseline.reference.interaction_watermark,
                evidence=evidence,
            )
            temporal = build_temporal_snapshot_document(
                snapshot_id=snapshot_id,
                baseline=capture.baseline,
                profile_document=adaptive_profile_document(profile),
                evidence_documents=[temporal_evidence_document(e) for e in evidence],
                retained_until=min(capture.row.retained_until, later.row.retained_until),
                policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
            )
            cases.append(
                NativeTrainingCase(
                    capture, later, event, evidence, temporal, provenance, split, excluded_event_ids
                )
            )
        except NativeDatasetRejected as error:
            counters[str(error)] += 1
        except ValueError, KeyError, TypeError:
            counters["INVALID_FROZEN_SOURCE_FACT"] += 1
    report: dict[str, JsonValue] = {
        "adapter_version": ADAPTER_VERSION,
        "capture_count": len(captures),
        "unique_provenance_gaps": len(gaps),
        "structurally_valid_cases": len(cases),
        "split_counts": {
            s: sum(c.split == s for c in cases) for s in ("train", "validation", "test")
        },
        "rejection_counts": dict(sorted(counters.items())),
        "quality_eligible": False,
        "training_started": False,
        "admission_requirements": [
            "NATIVE_SOURCE_AND_DATASET_APPROVAL",
            "COMPLETE_TOKENIZER_COVERAGE",
            "CALIBRATED_TEACHER",
            "NONEMPTY_PURGED_SPLITS",
            "CURRENT_CONSENT_AND_ERASURE_EXECUTION",
        ],
    }
    return tuple(cases), report


def verify_native_training_case(case: NativeTrainingCase) -> None:
    """Recompute private case ancestry from the immutable captured bytes."""
    # Rebuild canonical sources: frozen dataclasses still contain mutable JSON.
    now = case.outcome_capture.row.created_at
    originals = [
        decode_native_capture(c.row, current_revision=c.row.consent_revision, now=now)
        for c in (case.capture, case.outcome_capture)
    ]
    rebuilt, _ = build_native_training_cases(
        originals,
        train_end_ms=_integer(case.provenance_document["train_end_ms"]),
        validation_end_ms=_integer(case.provenance_document["validation_end_ms"]),
        excluded_event_ids=case.excluded_event_ids,
    )
    _require(case in rebuilt, "NATIVE_CASE_INTEGRITY_MISMATCH")


def materialize_native_training_example(
    case: NativeTrainingCase,
    *,
    semantic_ids: Mapping[UUID, SonaSemanticId],
    tokenizer_sha256: str,
    model_manifest_sha256: str,
    teacher: Callable[[SonaInferenceRequest], SonaTeacherSnapshot],
) -> SonaTrainingExample:
    """Produce the existing tensor writer's input; this grants no training admission."""
    verify_native_training_case(case)
    request = build_sona_inference_request(
        case.capture.query,
        case.capture.baseline,
        temporal_snapshot_id=UUID(str(case.temporal_document["snapshot_id"])),
        cutoff_at_ms=_ms(case.capture.row.created_at),
        evidence=case.evidence,
        semantic_ids=semantic_ids,
        tokenizer_sha256=tokenizer_sha256,
        model_manifest_sha256=model_manifest_sha256,
    )
    mandatory = {
        t.recording_id
        for t in RecommendationPipelineRunner().filter_snapshot_tracks(
            case.capture.query, case.capture.baseline
        )
    }
    _require(
        {c.recording_id for c in request.candidates} == mandatory
        and all(e.semantic_id != UNKNOWN_SEMANTIC_ID for e in request.history),
        "INCOMPLETE_TOKENIZER_COVERAGE",
    )
    signal = classify_sona_0026_listening_outcome(case.outcome_event)
    event = case.outcome_event
    outcome = SonaObservedOutcome(
        owner_user_id=event.owner_user_id,
        source_request_sha256=request.request_sha256,
        recording_id=event.recording_id,
        observed_at_ms=event.received_at_ms,
        completion=None
        if event.completion_ratio is None
        else float(signal is TemporalSignal.FINALIZED_COMPLETION),
        skip=float(signal is TemporalSignal.FINALIZED_SHORT_LISTEN_SKIP),
        outcome_source_event_id=event.source_event_id,
        outcome_source_request_sha256=event.source_request_sha256,
    )
    snapshot = teacher(request)
    verify_sona_teacher_snapshot(snapshot)
    return build_sona_training_example(request, outcome, snapshot)

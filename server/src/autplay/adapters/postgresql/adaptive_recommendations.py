"""PostgreSQL persistence for immutable R1B temporal shadow snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid7

import rfc8785
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from autplay.application.adaptive_recommendations import (
    adaptive_profile_document,
    adaptive_profile_from_document,
    canonical_sha256,
    temporal_evidence_document,
    temporal_evidence_from_document,
)
from autplay.domain.adaptive_recommendations import (
    MAX_DIMENSIONS,
    MAX_SOURCE_EVENTS,
    AdaptiveFeaturePolicy,
    AdaptiveProfile,
    TemporalEvidence,
)
from autplay.domain.recommendations import JsonValue, RecommendationInputSnapshot
from autplay.domain.sona import SonaTemporalSnapshot

from .models import (
    RecommendationAdaptiveProfileRow,
    RecommendationInputSnapshotRow,
    RecommendationTemporalEventRow,
    RecommendationTemporalSnapshotRow,
)

MAX_TEMPORAL_SNAPSHOT_BYTES = 4_194_304


@dataclass(frozen=True, slots=True)
class AdaptiveSnapshotReference:
    snapshot_id: UUID
    profile_id: UUID
    snapshot_sha256: str
    feature_policy_sha256: str


class SqlAlchemyAdaptiveRecommendationRepository:
    """Atomically persist and owner-filter normalized events, profile and snapshot."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        *,
        ids: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._ids = ids
        self._clock = clock or (lambda: datetime.now(UTC))

    def save_snapshot(
        self,
        *,
        baseline: RecommendationInputSnapshot,
        profile: AdaptiveProfile,
        evidence: Sequence[TemporalEvidence],
        retained_until: datetime,
        policy: AdaptiveFeaturePolicy,
    ) -> AdaptiveSnapshotReference:
        selected = _validate_capture(
            baseline, profile, evidence, retained_until, policy, self._clock()
        )
        evidence_documents = tuple(temporal_evidence_document(value) for value in selected)
        profile_document = adaptive_profile_document(profile)
        with self._sessions() as session, session.begin():
            baseline_row = session.scalar(
                select(RecommendationInputSnapshotRow)
                .where(
                    RecommendationInputSnapshotRow.user_id == profile.owner_user_id,
                    RecommendationInputSnapshotRow.recommendation_input_snapshot_id
                    == baseline.reference.snapshot_id,
                )
                .with_for_update()
            )
            if baseline_row is None or (
                baseline_row.input_snapshot_sha256.hex() != baseline.reference.input_snapshot_sha256
            ):
                raise ValueError("owner-bound baseline input snapshot is unavailable")
            stored_snapshot = session.scalar(
                select(RecommendationTemporalSnapshotRow).where(
                    RecommendationTemporalSnapshotRow.user_id == profile.owner_user_id,
                    RecommendationTemporalSnapshotRow.recommendation_input_snapshot_id
                    == baseline.reference.snapshot_id,
                    RecommendationTemporalSnapshotRow.feature_policy_key
                    == profile.feature_policy_key,
                    RecommendationTemporalSnapshotRow.feature_policy_version
                    == profile.feature_policy_version,
                )
            )
            if stored_snapshot is not None:
                self._validate_existing_snapshot(
                    session,
                    stored_snapshot,
                    baseline,
                    profile_document,
                    evidence_documents,
                    policy,
                )
            else:
                snapshot_id = self._ids()
                profile_id = self._ids()
                snapshot_document = _snapshot_document(
                    snapshot_id=snapshot_id,
                    baseline=baseline,
                    profile_document=profile_document,
                    evidence_documents=evidence_documents,
                    retained_until=retained_until,
                    policy=policy,
                )
                if len(rfc8785.dumps(snapshot_document)) > MAX_TEMPORAL_SNAPSHOT_BYTES:
                    raise ValueError("temporal snapshot exceeds the accepted byte limit")
                for event, document in zip(selected, evidence_documents, strict=True):
                    self._save_event(session, event, document, retained_until)
                stored_profile_id = self._save_profile(
                    session, profile_id, profile, profile_document
                )
                stored_snapshot = self._save_temporal_snapshot(
                    session,
                    snapshot_id,
                    stored_profile_id,
                    baseline,
                    profile,
                    snapshot_document,
                    retained_until,
                )
            reference = AdaptiveSnapshotReference(
                snapshot_id=stored_snapshot.recommendation_temporal_snapshot_id,
                profile_id=cast(UUID, stored_snapshot.recommendation_adaptive_profile_id),
                snapshot_sha256=stored_snapshot.snapshot_sha256.hex(),
                feature_policy_sha256=stored_snapshot.feature_policy_sha256.hex(),
            )
        return reference

    def load(self, user_id: UUID, baseline_snapshot_id: UUID) -> AdaptiveProfile | None:
        """Load only an unexpired snapshot for the exact owner/P11 input pair."""

        with self._sessions() as session:
            row = session.scalar(
                select(RecommendationTemporalSnapshotRow)
                .where(
                    RecommendationTemporalSnapshotRow.user_id == user_id,
                    RecommendationTemporalSnapshotRow.recommendation_input_snapshot_id
                    == baseline_snapshot_id,
                    RecommendationTemporalSnapshotRow.retained_until > self._clock(),
                )
                .order_by(RecommendationTemporalSnapshotRow.created_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            document = _json_object(row.snapshot_document)
            if (
                canonical_sha256(
                    cast(
                        dict[str, JsonValue],
                        {key: value for key, value in document.items() if key != "snapshot_sha256"},
                    )
                )
                != row.snapshot_sha256.hex()
            ):
                raise ValueError("temporal snapshot hash mismatch")
            profile = adaptive_profile_from_document(
                _json_object_required(document, "adaptive_profile")
            )
            if (
                profile.owner_user_id != user_id
                or profile.interaction_watermark != row.interaction_watermark
                or profile.feature_policy_sha256 != row.feature_policy_sha256.hex()
            ):
                raise ValueError("temporal snapshot profile binding mismatch")
            return profile

    def load_sona_snapshot(
        self, user_id: UUID, temporal_snapshot_id: UUID
    ) -> SonaTemporalSnapshot | None:
        """Reload the exact retained event sequence for model algorithmic replay."""

        with self._sessions() as session:
            row = session.scalar(
                select(RecommendationTemporalSnapshotRow).where(
                    RecommendationTemporalSnapshotRow.user_id == user_id,
                    RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                    == temporal_snapshot_id,
                    RecommendationTemporalSnapshotRow.retained_until > self._clock(),
                )
            )
            if row is None or row.recommendation_input_snapshot_id is None:
                return None
            document = _json_object(row.snapshot_document)
            document_json = cast(dict[str, JsonValue], document)
            declared_hash = _required_hash(document_json, "snapshot_sha256")
            if (
                canonical_sha256(
                    cast(
                        dict[str, JsonValue],
                        {key: value for key, value in document.items() if key != "snapshot_sha256"},
                    )
                )
                != declared_hash
                or declared_hash != row.snapshot_sha256.hex()
            ):
                raise ValueError("temporal snapshot hash mismatch")
            raw_evidence = document.get("source_evidence")
            if not isinstance(raw_evidence, list):
                raise ValueError("temporal snapshot source evidence is invalid")
            evidence = tuple(
                temporal_evidence_from_document(_json_object(cast(JsonValue, value)))
                for value in raw_evidence
            )
            if (
                len(evidence) != row.source_event_count
                or canonical_sha256(cast(list[JsonValue], raw_evidence))
                != row.source_evidence_sha256.hex()
            ):
                raise ValueError("temporal snapshot evidence binding mismatch")
            return SonaTemporalSnapshot(
                owner_user_id=user_id,
                temporal_snapshot_id=row.recommendation_temporal_snapshot_id,
                temporal_snapshot_sha256=declared_hash,
                feature_policy_sha256=row.feature_policy_sha256.hex(),
                baseline_snapshot_id=row.recommendation_input_snapshot_id,
                baseline_input_snapshot_sha256=row.baseline_input_snapshot_sha256.hex(),
                cutoff_at_ms=row.cutoff_at_ms,
                interaction_watermark=row.interaction_watermark,
                catalog_snapshot=row.catalog_snapshot,
                availability_snapshot_sha256=row.availability_snapshot_sha256.hex(),
                evidence=evidence,
                retained_until=row.retained_until,
            )

    def _save_event(
        self,
        session: Session,
        event: TemporalEvidence,
        document: dict[str, JsonValue],
        retained_until: datetime,
    ) -> None:
        digest = _required_hash(document, "normalized_evidence_sha256")
        statement = pg_insert(RecommendationTemporalEventRow).values(
            recommendation_temporal_event_id=event.evidence_id,
            user_id=event.owner_user_id,
            server_profile_id=event.server_profile_id,
            device_id=event.device_id,
            source_event_id=event.source_event_id,
            source_request_sha256=bytes.fromhex(event.source_request_sha256),
            device_sequence=event.device_sequence,
            server_sequence=event.server_sequence,
            source_event_type=event.source_event_type,
            signal_key=event.signal.value,
            derivation_key=event.derivation_key,
            recording_id=event.recording_id,
            dimensions=[{"kind": value.kind.value, "key": value.key} for value in event.dimensions],
            occurred_at_ms=event.occurred_at_ms,
            received_at_ms=event.received_at_ms,
            effective_at_ms=event.effective_at_ms,
            time_classification=event.time_classification.value,
            origin_lane=event.origin_lane.value,
            signed_strength=Decimal(str(event.signed_strength)),
            quality_weight=Decimal(str(event.quality_weight)),
            excluded_from_taste=event.excluded_from_taste,
            recommendation_request_id=event.recommendation_request_id,
            impression_event_id=event.impression_event_id,
            recommendation_source_rank=event.recommendation_source_rank,
            normalized_evidence_sha256=bytes.fromhex(digest),
            evidence_document=document,
            retained_until=retained_until,
        )
        session.execute(statement.on_conflict_do_nothing())
        row = session.scalar(
            select(RecommendationTemporalEventRow).where(
                RecommendationTemporalEventRow.user_id == event.owner_user_id,
                RecommendationTemporalEventRow.source_event_id == event.source_event_id,
                RecommendationTemporalEventRow.signal_key == event.signal.value,
                RecommendationTemporalEventRow.derivation_key == event.derivation_key,
            )
        )
        if row is None or (
            row.recommendation_temporal_event_id != event.evidence_id
            or row.normalized_evidence_sha256.hex() != digest
            or row.source_request_sha256.hex() != event.source_request_sha256
        ):
            raise ValueError("temporal evidence identity conflict")

    def _save_profile(
        self,
        session: Session,
        profile_id: UUID,
        profile: AdaptiveProfile,
        document: dict[str, JsonValue],
    ) -> UUID:
        digest = _required_hash(document, "profile_sha256")
        statement = pg_insert(RecommendationAdaptiveProfileRow).values(
            recommendation_adaptive_profile_id=profile_id,
            user_id=profile.owner_user_id,
            cutoff_at_ms=profile.cutoff_at_ms,
            interaction_watermark=profile.interaction_watermark,
            feature_policy_key=profile.feature_policy_key,
            feature_policy_version=profile.feature_policy_version,
            feature_policy_sha256=bytes.fromhex(profile.feature_policy_sha256),
            profile_sha256=bytes.fromhex(digest),
            profile_document=document,
            source_event_count=len(profile.source_event_ids),
            dimension_count=len(profile.dimensions),
        )
        session.execute(statement.on_conflict_do_nothing())
        row = session.scalar(
            select(RecommendationAdaptiveProfileRow).where(
                RecommendationAdaptiveProfileRow.user_id == profile.owner_user_id,
                RecommendationAdaptiveProfileRow.cutoff_at_ms == profile.cutoff_at_ms,
                RecommendationAdaptiveProfileRow.interaction_watermark
                == profile.interaction_watermark,
                RecommendationAdaptiveProfileRow.feature_policy_key == profile.feature_policy_key,
                RecommendationAdaptiveProfileRow.feature_policy_version
                == profile.feature_policy_version,
            )
        )
        if row is None or row.profile_sha256.hex() != digest:
            raise ValueError("adaptive profile identity conflict")
        return row.recommendation_adaptive_profile_id

    def _save_temporal_snapshot(
        self,
        session: Session,
        snapshot_id: UUID,
        profile_id: UUID,
        baseline: RecommendationInputSnapshot,
        profile: AdaptiveProfile,
        document: dict[str, JsonValue],
        retained_until: datetime,
    ) -> RecommendationTemporalSnapshotRow:
        statement = pg_insert(RecommendationTemporalSnapshotRow).values(
            recommendation_temporal_snapshot_id=snapshot_id,
            user_id=profile.owner_user_id,
            recommendation_input_snapshot_id=baseline.reference.snapshot_id,
            recommendation_adaptive_profile_id=profile_id,
            cutoff_at_ms=profile.cutoff_at_ms,
            interaction_watermark=profile.interaction_watermark,
            catalog_snapshot=baseline.reference.catalog_snapshot,
            availability_snapshot_sha256=bytes.fromhex(baseline.reference.availability_snapshot),
            baseline_input_snapshot_sha256=bytes.fromhex(baseline.reference.input_snapshot_sha256),
            feature_policy_key=profile.feature_policy_key,
            feature_policy_version=profile.feature_policy_version,
            feature_policy_sha256=bytes.fromhex(profile.feature_policy_sha256),
            event_time_policy_sha256=bytes.fromhex(
                _required_hash(document, "event_time_policy_sha256")
            ),
            derived_features_sha256=bytes.fromhex(
                _required_hash(document, "derived_features_sha256")
            ),
            source_evidence_sha256=bytes.fromhex(
                _required_hash(document, "source_evidence_sha256")
            ),
            snapshot_sha256=bytes.fromhex(_required_hash(document, "snapshot_sha256")),
            source_event_count=len(profile.source_event_ids),
            dimension_count=len(profile.dimensions),
            snapshot_document=document,
            retained_until=retained_until,
        )
        session.execute(statement.on_conflict_do_nothing())
        row = session.scalar(
            select(RecommendationTemporalSnapshotRow).where(
                RecommendationTemporalSnapshotRow.user_id == profile.owner_user_id,
                RecommendationTemporalSnapshotRow.recommendation_input_snapshot_id
                == baseline.reference.snapshot_id,
                RecommendationTemporalSnapshotRow.feature_policy_key == profile.feature_policy_key,
                RecommendationTemporalSnapshotRow.feature_policy_version
                == profile.feature_policy_version,
            )
        )
        if row is None or row.snapshot_sha256.hex() != _required_hash(document, "snapshot_sha256"):
            raise ValueError("temporal snapshot identity conflict")
        return row

    def _validate_existing_snapshot(
        self,
        session: Session,
        row: RecommendationTemporalSnapshotRow,
        baseline: RecommendationInputSnapshot,
        profile_document: dict[str, JsonValue],
        evidence_documents: Sequence[dict[str, JsonValue]],
        policy: AdaptiveFeaturePolicy,
    ) -> None:
        profile_id = row.recommendation_adaptive_profile_id
        if profile_id is None or row.retained_until <= self._clock():
            raise ValueError("existing temporal snapshot is unavailable")
        profile_row = session.get(RecommendationAdaptiveProfileRow, profile_id)
        expected_profile_sha256 = _required_hash(profile_document, "profile_sha256")
        if profile_row is None or profile_row.profile_sha256.hex() != expected_profile_sha256:
            raise ValueError("existing adaptive profile identity conflict")
        expected_document = _snapshot_document(
            snapshot_id=row.recommendation_temporal_snapshot_id,
            baseline=baseline,
            profile_document=profile_document,
            evidence_documents=evidence_documents,
            retained_until=row.retained_until,
            policy=policy,
        )
        if row.source_evidence_sha256.hex() != _required_hash(
            expected_document, "source_evidence_sha256"
        ) or row.snapshot_sha256.hex() != _required_hash(expected_document, "snapshot_sha256"):
            raise ValueError("existing temporal snapshot identity conflict")


def _validate_capture(
    baseline: RecommendationInputSnapshot,
    profile: AdaptiveProfile,
    evidence: Sequence[TemporalEvidence],
    retained_until: datetime,
    policy: AdaptiveFeaturePolicy,
    now: datetime,
) -> tuple[TemporalEvidence, ...]:
    if retained_until.tzinfo is None or retained_until <= now:
        raise ValueError("temporal snapshot retention must be future UTC time")
    if profile.interaction_watermark != baseline.reference.interaction_watermark:
        raise ValueError("profile and P11 watermark mismatch")
    if (
        profile.feature_policy_key,
        profile.feature_policy_version,
        profile.feature_policy_sha256,
    ) != (policy.key, policy.version, policy.content_sha256):
        raise ValueError("profile feature-policy identity mismatch")
    if len(evidence) > MAX_SOURCE_EVENTS or len(profile.dimensions) > MAX_DIMENSIONS:
        raise ValueError("temporal snapshot accepted bounds exceeded")
    selected = tuple(
        sorted(
            (
                value
                for value in evidence
                if value.owner_user_id == profile.owner_user_id
                and value.server_sequence is not None
                and value.received_at_ms <= profile.cutoff_at_ms
                and value.server_sequence <= profile.interaction_watermark
            ),
            key=lambda value: (
                value.effective_at_ms,
                value.received_at_ms,
                value.server_sequence,
                value.evidence_id.hex,
            ),
        )
    )
    if any(value.owner_user_id != profile.owner_user_id for value in evidence):
        raise ValueError("cross-owner temporal evidence rejected")
    if tuple(value.evidence_id for value in selected) != profile.source_event_ids:
        raise ValueError("profile source-event lineage mismatch")
    return selected


def _snapshot_document(
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


def _required_hash(document: Mapping[str, JsonValue], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{key} is invalid")
    return value


def _json_object(value: JsonValue) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("temporal snapshot document is invalid")
    return cast(dict[str, object], value)


def _json_object_required(value: Mapping[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"temporal snapshot {key} is invalid")
    return cast(dict[str, object], item)


__all__ = (
    "MAX_TEMPORAL_SNAPSHOT_BYTES",
    "AdaptiveSnapshotReference",
    "SqlAlchemyAdaptiveRecommendationRepository",
)

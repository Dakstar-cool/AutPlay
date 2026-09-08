"""Real PostgreSQL evidence for immutable Sona binding beside unchanged P11 truth."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.adaptive_recommendations import (
    SqlAlchemyAdaptiveRecommendationRepository,
)
from autplay.adapters.postgresql.models import (
    DeviceRow,
    RecommendationInputSnapshotRow,
    RecommendationItemRow,
    RecommendationRequestRow,
    RecommendationTemporalSnapshotRow,
    UserAccountRow,
)
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.adapters.postgresql.sona import SqlAlchemySonaShadowEvidenceRepository
from autplay.application.recommendations import baseline_pipeline_definition, request_document
from autplay.application.sona import SonaShadowService
from autplay.application.sona_shadow import (
    build_sona_shadow_evidence,
    sona_shadow_evidence_document,
)
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    DimensionKind,
    OriginLane,
    TemporalDimension,
    TemporalEvidence,
    TemporalSignal,
    TimeClassification,
    project_temporal_profile,
)
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    SnapshotTrack,
)
from autplay.domain.sona import (
    SonaGeneratedCandidate,
    SonaInferenceOutput,
    SonaInferenceRequest,
    SonaRankedCandidate,
    SonaSemanticId,
    SonaShadowStatus,
)
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

TOKENIZER = "b" * 64
TOKENIZER_MANIFEST = "d" * 64
MODEL = "c" * 64


class _SemanticIds:
    tokenizer_manifest_sha256 = TOKENIZER_MANIFEST

    def __init__(self, recording_id: UUID) -> None:
        self._recording_id = recording_id

    def load(
        self, recording_ids: Sequence[UUID], *, tokenizer_sha256: str
    ) -> Mapping[UUID, SonaSemanticId]:
        assert tokenizer_sha256 == TOKENIZER
        return {
            recording_id: SonaSemanticId(1, 1, 1)
            for recording_id in recording_ids
            if recording_id == self._recording_id
        }

    def expand(
        self, semantic_ids: Sequence[SonaSemanticId], *, tokenizer_sha256: str
    ) -> Mapping[SonaSemanticId, tuple[UUID, ...]]:
        assert tokenizer_sha256 == TOKENIZER
        semantic_id = SonaSemanticId(1, 1, 1)
        return {semantic_id: (self._recording_id,)} if semantic_id in semantic_ids else {}


class _Inference:
    def __init__(self, recording_id: UUID) -> None:
        self._recording_id = recording_id

    def infer(self, request: SonaInferenceRequest) -> SonaInferenceOutput:
        return SonaInferenceOutput(
            request.request_sha256,
            (SonaGeneratedCandidate(SonaSemanticId(1, 1, 1), -0.1),),
            (
                SonaRankedCandidate(
                    self._recording_id,
                    (0.1, 0.2, 0.3, 0.4),
                    0.5,
                ),
            ),
        )


def test_sona_evidence_is_owner_bound_idempotent_immutable_and_has_no_items(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime.now(UTC).replace(microsecond=123_000)
        retained_until = now + timedelta(days=30)
        cutoff_at_ms = int(now.timestamp() * 1_000)
        owner_id, other_id, device_id = uuid4(), uuid4(), uuid4()
        artist_id, recording_id, baseline_id, request_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        with sessions() as session, session.begin():
            session.add_all(
                (
                    UserAccountRow(
                        user_id=owner_id,
                        display_name="sona-owner",
                        role="USER",
                        status="ACTIVE",
                    ),
                    UserAccountRow(
                        user_id=other_id,
                        display_name="sona-other",
                        role="USER",
                        status="ACTIVE",
                    ),
                )
            )
            session.flush()
            session.add(
                DeviceRow(
                    device_id=device_id,
                    user_id=owner_id,
                    device_name="sona-device",
                    platform="ANDROID",
                    app_version="r1b-sona",
                )
            )
            session.execute(
                text(
                    "INSERT INTO catalog.artist_credit "
                    "(artist_credit_id,display_name,normalized_name) "
                    "VALUES (:id,'Sona artist','sona artist')"
                ),
                {"id": artist_id},
            )
            session.execute(
                text(
                    "INSERT INTO catalog.recording "
                    "(recording_id,artist_credit_id,title,normalized_title,duration_ms,"
                    "identity_status) VALUES "
                    "(:id,:artist,'Sona track','sona track',180000,'ACTIVE')"
                ),
                {"id": recording_id, "artist": artist_id},
            )
            session.add(
                RecommendationInputSnapshotRow(
                    recommendation_input_snapshot_id=baseline_id,
                    user_id=owner_id,
                    input_snapshot_sha256=bytes.fromhex("a" * 64),
                    interaction_watermark=7,
                    catalog_snapshot=11,
                    availability_snapshot="d" * 64,
                    policy_snapshot_sha256=bytes.fromhex("e" * 64),
                    snapshot_document={},
                    retained_until=retained_until,
                    created_at=now,
                )
            )

        baseline = RecommendationInputSnapshot(
            RecommendationSnapshotRef(
                baseline_id,
                "a" * 64,
                7,
                11,
                "d" * 64,
                "e" * 64,
            ),
            (
                SnapshotTrack(
                    recording_id,
                    None,
                    str(artist_id),
                    None,
                    (),
                    "VAULT",
                    True,
                    "ACTIVE",
                    "NONE",
                    False,
                    0,
                    0,
                    0,
                    None,
                    cutoff_at_ms,
                    None,
                ),
            ),
            retained_until,
        )
        event = TemporalEvidence(
            evidence_id=uuid4(),
            source_event_id=uuid4(),
            owner_user_id=owner_id,
            server_profile_id=uuid4(),
            device_id=device_id,
            device_sequence=1,
            server_sequence=7,
            source_event_type="LISTENING_EVENT_RECORDED",
            signal=TemporalSignal.FINALIZED_ORGANIC_LISTEN,
            derivation_key="BASE_LISTEN_V1",
            recording_id=recording_id,
            dimensions=(TemporalDimension(DimensionKind.CANONICAL_ARTIST_ID, str(artist_id)),),
            occurred_at_ms=cutoff_at_ms - 60_000,
            received_at_ms=cutoff_at_ms - 50_000,
            effective_at_ms=cutoff_at_ms - 60_000,
            time_classification=TimeClassification.TRUSTED_EVENT_TIME,
            origin_lane=OriginLane.ORGANIC,
            signed_strength=0.6,
            quality_weight=1.0,
            excluded_from_taste=False,
            source_request_sha256="f" * 64,
        )
        profile = project_temporal_profile(
            owner_user_id=owner_id,
            cutoff_at_ms=cutoff_at_ms,
            interaction_watermark=7,
            evidence=(event,),
        )
        generated_ids = iter((uuid4(), uuid4()))
        adaptive = SqlAlchemyAdaptiveRecommendationRepository(
            sessions,
            ids=lambda: next(generated_ids),
            clock=lambda: now,
        )
        temporal_reference = adaptive.save_snapshot(
            baseline=baseline,
            profile=profile,
            evidence=(event,),
            retained_until=retained_until,
            policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
        )
        temporal = adaptive.load_sona_snapshot(owner_id, temporal_reference.snapshot_id)
        assert temporal is not None

        query = RecommendationQuery(owner_id, RecommendationSurface.RECOMMENDATIONS, seed=17)
        p11_pipeline = baseline_pipeline_definition()
        canonical_request = request_document(query, p11_pipeline, baseline.reference)
        trace = RecommendationRequestTrace(
            request_id,
            query,
            p11_pipeline,
            baseline.reference,
            sha256(rfc8785.dumps(canonical_request)).hexdigest(),
            canonical_request,
            now,
        )
        p11_response = RecommendationResponse(trace, ())
        recommendations = SqlAlchemyRecommendationRuntime(sessions)
        recommendations.ensure_pipeline(p11_pipeline)
        recommendations.save(p11_response)

        service = SonaShadowService(
            _SemanticIds(recording_id),
            _Inference(recording_id),
            tokenizer_sha256=TOKENIZER,
            model_manifest_sha256=MODEL,
        )
        result = service.run(
            RecommendationQuery(
                owner_id,
                RecommendationSurface.RECOMMENDATIONS,
                seed=17,
                shadow=True,
            ),
            baseline,
            temporal_snapshot_id=temporal.temporal_snapshot_id,
            cutoff_at_ms=temporal.cutoff_at_ms,
            evidence=temporal.evidence,
        )
        evidence = build_sona_shadow_evidence(p11_response, temporal, service, result)
        recommendations.ensure_pipeline(service.pipeline)
        repository = SqlAlchemySonaShadowEvidenceRepository(sessions, clock=lambda: now)

        assert evidence.sona_request_sha256 is not None
        assert evidence.sona_output_sha256 is not None
        with pytest.raises(DBAPIError), sessions() as session, session.begin():
            session.execute(
                update(RecommendationRequestRow)
                .where(RecommendationRequestRow.recommendation_request_id == request_id)
                .values(
                    request_sha256=b"x" * 32,
                    recommendation_temporal_snapshot_id=evidence.temporal_snapshot_id,
                    temporal_snapshot_sha256=bytes.fromhex(evidence.temporal_snapshot_sha256),
                    adaptive_feature_policy_sha256=bytes.fromhex(evidence.feature_policy_sha256),
                    sona_shadow_pipeline_key=evidence.shadow_pipeline_key,
                    sona_shadow_pipeline_version=evidence.shadow_pipeline_version,
                    sona_shadow_pipeline_manifest_sha256=bytes.fromhex(
                        evidence.shadow_pipeline_manifest_sha256
                    ),
                    sona_tokenizer_sha256=bytes.fromhex(evidence.tokenizer_sha256),
                    sona_model_manifest_sha256=bytes.fromhex(evidence.model_manifest_sha256),
                    sona_request_sha256=bytes.fromhex(evidence.sona_request_sha256),
                    sona_output_sha256=bytes.fromhex(evidence.sona_output_sha256),
                    sona_shadow_evidence_sha256=bytes.fromhex(evidence.evidence_sha256),
                    sona_shadow_status=evidence.status.value,
                    sona_shadow_reason=evidence.reason,
                    sona_shadow_document=sona_shadow_evidence_document(evidence),
                )
            )

        repository.save(evidence)
        repository.save(evidence)

        assert repository.load(owner_id, request_id) == evidence
        assert repository.load(other_id, request_id) is None
        assert evidence.status is SonaShadowStatus.SUCCEEDED
        assert recommendations.exact(owner_id, request_id) == p11_response
        with sessions() as session:
            row = session.get(RecommendationRequestRow, request_id)
            assert row is not None
            assert row.recommendation_temporal_snapshot_id == temporal.temporal_snapshot_id
            assert row.sona_shadow_status == "SUCCEEDED"
            assert session.scalar(select(func.count()).select_from(RecommendationItemRow)) == 0

        with pytest.raises(DBAPIError), sessions() as session, session.begin():
            session.execute(
                update(RecommendationRequestRow)
                .where(RecommendationRequestRow.recommendation_request_id == request_id)
                .values(sona_shadow_document={})
            )
        with pytest.raises(DBAPIError), sessions() as session, session.begin():
            session.execute(
                update(RecommendationRequestRow)
                .where(RecommendationRequestRow.recommendation_request_id == request_id)
                .values(request_sha256=b"x" * 32)
            )
        with pytest.raises(DBAPIError), sessions() as session, session.begin():
            session.execute(
                delete(RecommendationRequestRow).where(
                    RecommendationRequestRow.recommendation_request_id == request_id
                )
            )

        expired_created_at = now - timedelta(days=2)
        expired_retained_until = now - timedelta(days=1)
        with sessions() as session, session.begin():
            session.execute(text("SET LOCAL session_replication_role = replica"))
            session.execute(
                update(RecommendationTemporalSnapshotRow)
                .where(
                    RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                    == temporal.temporal_snapshot_id
                )
                .values(
                    created_at=expired_created_at,
                    retained_until=expired_retained_until,
                )
            )
            session.execute(
                update(RecommendationInputSnapshotRow)
                .where(
                    RecommendationInputSnapshotRow.recommendation_input_snapshot_id
                    == baseline.reference.snapshot_id
                )
                .values(
                    created_at=expired_created_at,
                    retained_until=expired_retained_until,
                )
            )
            session.execute(text("SET LOCAL session_replication_role = origin"))
        with sessions() as session, session.begin():
            session.execute(
                delete(RecommendationTemporalSnapshotRow).where(
                    RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                    == temporal.temporal_snapshot_id
                )
            )
        with sessions() as session, session.begin():
            session.execute(
                delete(RecommendationInputSnapshotRow).where(
                    RecommendationInputSnapshotRow.recommendation_input_snapshot_id
                    == baseline.reference.snapshot_id
                )
            )

        assert adaptive.load_sona_snapshot(owner_id, temporal.temporal_snapshot_id) is None
        assert repository.load(owner_id, request_id) == evidence
        assert recommendations.exact(owner_id, request_id) == p11_response
        with sessions() as session:
            row = session.get(RecommendationRequestRow, request_id)
            assert row is not None
            assert row.recommendation_temporal_snapshot_id is None
            assert row.recommendation_input_snapshot_id is None
    finally:
        engine.dispose()

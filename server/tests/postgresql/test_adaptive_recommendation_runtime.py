"""Real PostgreSQL evidence for the owner-bound R1B shadow snapshot runtime."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.adaptive_recommendations import (
    SqlAlchemyAdaptiveRecommendationRepository,
)
from autplay.adapters.postgresql.models import (
    DeviceRow,
    RecommendationAdaptiveProfileRow,
    RecommendationInputSnapshotRow,
    RecommendationTemporalEventRow,
    RecommendationTemporalSnapshotRow,
    UserAccountRow,
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
    RecommendationSnapshotRef,
)
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


def _owner(session: Session, name: str) -> tuple[UUID, UUID]:
    user_id, device_id = uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=name,
            platform="ANDROID",
            app_version="r1b-shadow",
        )
    )
    session.flush()
    return user_id, device_id


def _recording(session: Session) -> tuple[UUID, UUID]:
    artist_id, recording_id = uuid4(), uuid4()
    session.execute(
        text(
            "INSERT INTO catalog.artist_credit "
            "(artist_credit_id, display_name, normalized_name) "
            "VALUES (:id, 'R1B artist', 'r1b artist')"
        ),
        {"id": artist_id},
    )
    session.execute(
        text(
            "INSERT INTO catalog.recording "
            "(recording_id, artist_credit_id, title, normalized_title, duration_ms, "
            "identity_status) VALUES (:id, :artist, 'R1B track', 'r1b track', "
            "180000, 'ACTIVE')"
        ),
        {"id": recording_id, "artist": artist_id},
    )
    return artist_id, recording_id


def test_owner_bound_temporal_snapshot_is_atomic_idempotent_and_immutable(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        now = datetime.now(UTC).replace(microsecond=123_000)
        retained_until = now + timedelta(days=30)
        cutoff_at_ms = int(now.timestamp() * 1000)
        baseline_id = uuid4()
        with sessions() as session, session.begin():
            owner_id, device_id = _owner(session, "r1b-owner")
            other_id, _ = _owner(session, "r1b-other")
            artist_id, recording_id = _recording(session)
            session.add(
                RecommendationInputSnapshotRow(
                    recommendation_input_snapshot_id=baseline_id,
                    user_id=owner_id,
                    input_snapshot_sha256=bytes.fromhex("a" * 64),
                    interaction_watermark=7,
                    catalog_snapshot=11,
                    availability_snapshot="b" * 64,
                    policy_snapshot_sha256=bytes.fromhex("c" * 64),
                    snapshot_document={},
                    retained_until=retained_until,
                    created_at=now,
                )
            )

        baseline = RecommendationInputSnapshot(
            reference=RecommendationSnapshotRef(
                snapshot_id=baseline_id,
                input_snapshot_sha256="a" * 64,
                interaction_watermark=7,
                catalog_snapshot=11,
                availability_snapshot="b" * 64,
                policy_snapshot_sha256="c" * 64,
            ),
            tracks=(),
            retained_until=retained_until,
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
            source_request_sha256="d" * 64,
        )
        profile = project_temporal_profile(
            owner_user_id=owner_id,
            cutoff_at_ms=cutoff_at_ms,
            interaction_watermark=7,
            evidence=(event,),
        )
        generated_ids = iter((uuid4(), uuid4()))
        repository = SqlAlchemyAdaptiveRecommendationRepository(
            sessions,
            ids=lambda: next(generated_ids),
            clock=lambda: now,
        )

        first = repository.save_snapshot(
            baseline=baseline,
            profile=profile,
            evidence=(event,),
            retained_until=retained_until,
            policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
        )
        repeated = repository.save_snapshot(
            baseline=baseline,
            profile=profile,
            evidence=(event,),
            retained_until=retained_until,
            policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
        )

        assert repeated == first
        assert repository.load(owner_id, baseline_id) == profile
        assert repository.load(other_id, baseline_id) is None
        with sessions() as session:
            assert (
                session.scalar(select(func.count()).select_from(RecommendationTemporalEventRow))
                == 1
            )
            assert (
                session.scalar(select(func.count()).select_from(RecommendationAdaptiveProfileRow))
                == 1
            )
            assert (
                session.scalar(select(func.count()).select_from(RecommendationTemporalSnapshotRow))
                == 1
            )

        with pytest.raises(ValueError, match="cross-owner"):
            repository.save_snapshot(
                baseline=baseline,
                profile=profile,
                evidence=(replace(event, owner_user_id=other_id),),
                retained_until=retained_until,
                policy=DEFAULT_ADAPTIVE_FEATURE_POLICY,
            )
        with pytest.raises(DBAPIError), sessions() as session, session.begin():
            session.execute(
                update(RecommendationTemporalSnapshotRow)
                .where(
                    RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                    == first.snapshot_id
                )
                .values(catalog_snapshot=12)
            )
    finally:
        engine.dispose()

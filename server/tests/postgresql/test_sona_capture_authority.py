"""Real PostgreSQL fences for non-activating native Sona capture storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid7

import pytest
from sqlalchemy import create_engine, delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    RecommendationRequestRow,
    SonaCaptureBundleRow,
    SonaCaptureLineageCursorRow,
    SonaCaptureTargetDispatchRow,
    SonaShadowAttemptRow,
    SonaShadowEvidenceRow,
    SonaShadowWorkRow,
)
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.adapters.postgresql.sona_capture_retention import SqlAlchemySonaCaptureRetention
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
)
from autplay.domain.recommendations import RecommendationQuery, RecommendationSurface

from .test_recommendation_runtime import _principal, _vault_recording


def _digest(value: bytes) -> bytes:
    return sha256(value).digest()


def test_capture_lineage_fences_and_privacy_cascade(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "sona-capture-owner")
            provider_id = uuid7()
            session.execute(
                text(
                    "INSERT INTO identity.source_provider "
                    "(provider_id,provider_key,display_name,adapter_id,adapter_version) "
                    "VALUES (:id,'sona.capture.fixture','Sona capture fixture','fixture','1')"
                ),
                {"id": provider_id},
            )
            _vault_recording(session, owner.user_id, index=44, provider_id=provider_id)
            session.commit()
        runtime = SqlAlchemyRecommendationRuntime(sessions)
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
            atomic_writer=runtime,
        )
        response = service.recommend(
            RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)
        )
        request_id = response.request.recommendation_request_id
        created_at = response.request.created_at
        target_id, work_id = uuid7(), uuid7()
        model_hash, tokenizer_hash = _digest(b"model"), _digest(b"tokenizer")
        pipeline_hash, profile_hash = _digest(b"pipeline"), _digest(b"profile")
        with sessions() as session:
            with (
                pytest.raises(DBAPIError, match="ck_sona_capture_document_hash"),
                session.begin_nested(),
            ):
                session.execute(
                    insert(SonaCaptureBundleRow).values(
                        recommendation_request_id=request_id,
                        user_id=owner.user_id,
                        baseline_snapshot_sha256=bytes.fromhex(
                            response.request.snapshot.input_snapshot_sha256
                        ),
                        temporal_snapshot_sha256=_digest(b"temporal"),
                        candidate_membership_sha256=_digest(b"candidate-membership"),
                        p11_ranking_sha256=_digest(b"ranking"),
                        bundle_sha256=_digest(b"wrong"),
                        consent_receipt_sha256=_digest(b"consent-fixture"),
                        consent_generation=1,
                        cutoff_at_ms=0,
                        interaction_watermark=response.request.snapshot.interaction_watermark,
                        universe_count=1,
                        eligible_count=1,
                        bundle_document=b"{}",
                        created_at=created_at,
                        expires_at=created_at + timedelta(days=180),
                    )
                )
            session.execute(
                insert(SonaCaptureBundleRow).values(
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    baseline_snapshot_sha256=bytes.fromhex(
                        response.request.snapshot.input_snapshot_sha256
                    ),
                    temporal_snapshot_sha256=_digest(b"temporal"),
                    candidate_membership_sha256=_digest(b"candidate-membership"),
                    p11_ranking_sha256=_digest(b"ranking"),
                    bundle_sha256=_digest(b"{}"),
                    consent_receipt_sha256=_digest(b"consent-fixture"),
                    consent_generation=1,
                    cutoff_at_ms=0,
                    interaction_watermark=response.request.snapshot.interaction_watermark,
                    universe_count=1,
                    eligible_count=1,
                    bundle_document=b"{}",
                    created_at=created_at,
                    expires_at=created_at + timedelta(days=180),
                )
            )
            with (
                pytest.raises(DBAPIError, match="sona_capture_cursor_expiry_mismatch"),
                session.begin_nested(),
            ):
                session.execute(
                    insert(SonaCaptureLineageCursorRow).values(
                        recommendation_request_id=request_id,
                        user_id=owner.user_id,
                        expires_at=created_at + timedelta(days=181),
                    )
                )
            session.execute(
                insert(SonaCaptureLineageCursorRow).values(
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    expires_at=created_at + timedelta(days=180),
                )
            )
            session.execute(
                insert(SonaCaptureTargetDispatchRow).values(
                    target_dispatch_id=target_id,
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    model_manifest_sha256=model_hash,
                    tokenizer_sha256=tokenizer_hash,
                    pipeline_manifest_sha256=pipeline_hash,
                    execution_profile_sha256=profile_hash,
                    registry_generation=1,
                )
            )
            session.commit()

        with sessions() as session:
            with pytest.raises(DBAPIError, match="sona_capture_immutable"), session.begin_nested():
                session.execute(
                    update(SonaCaptureBundleRow)
                    .where(SonaCaptureBundleRow.recommendation_request_id == request_id)
                    .values(bundle_document=b"tampered")
                )
            session.execute(
                update(SonaCaptureLineageCursorRow)
                .where(SonaCaptureLineageCursorRow.recommendation_request_id == request_id)
                .values(last_seen_registry_generation=2, state_generation=2)
            )
            with (
                pytest.raises(DBAPIError, match="sona_cursor_terminal_or_regressed"),
                session.begin_nested(),
            ):
                session.execute(
                    update(SonaCaptureLineageCursorRow)
                    .where(SonaCaptureLineageCursorRow.recommendation_request_id == request_id)
                    .values(state="EXPIRED", state_generation=3)
                )
            session.execute(
                update(SonaCaptureTargetDispatchRow)
                .where(SonaCaptureTargetDispatchRow.target_dispatch_id == target_id)
                .values(state="READY", state_generation=2)
            )
            with (
                pytest.raises(DBAPIError, match="sona_work_target_invalid"),
                session.begin_nested(),
            ):
                session.execute(
                    insert(SonaShadowWorkRow).values(
                        sona_shadow_work_id=work_id,
                        target_dispatch_id=target_id,
                        recommendation_request_id=request_id,
                        user_id=owner.user_id,
                        model_manifest_sha256=_digest(b"wrong-model"),
                        tokenizer_sha256=tokenizer_hash,
                        pipeline_manifest_sha256=pipeline_hash,
                        execution_profile_sha256=profile_hash,
                        lineage_sha256=_digest(b"lineage"),
                    )
                )
            session.execute(
                insert(SonaShadowWorkRow).values(
                    sona_shadow_work_id=work_id,
                    target_dispatch_id=target_id,
                    recommendation_request_id=request_id,
                    user_id=owner.user_id,
                    model_manifest_sha256=model_hash,
                    tokenizer_sha256=tokenizer_hash,
                    pipeline_manifest_sha256=pipeline_hash,
                    execution_profile_sha256=profile_hash,
                    lineage_sha256=_digest(b"lineage"),
                )
            )
            with (
                pytest.raises(DBAPIError, match="sona_work_transition_invalid"),
                session.begin_nested(),
            ):
                session.execute(
                    update(SonaShadowWorkRow)
                    .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                    .values(state="TERMINAL_INELIGIBLE", state_generation=2)
                )
            with pytest.raises(DBAPIError, match="sona_evidence_unbound"), session.begin_nested():
                session.execute(
                    insert(SonaShadowEvidenceRow).values(
                        sona_shadow_work_id=work_id,
                        user_id=owner.user_id,
                        recommendation_request_id=request_id,
                        evidence_sha256=_digest(b"{}"),
                        evidence_document=b"{}",
                    )
                )
            session.execute(
                update(SonaShadowWorkRow)
                .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                .values(
                    state="CLAIMED",
                    state_generation=2,
                    claim_generation=1,
                    attempt_count=1,
                    lease_until=datetime.now(UTC) + timedelta(minutes=1),
                )
            )
            retry_savepoint = session.begin_nested()
            try:
                session.execute(
                    update(SonaShadowWorkRow)
                    .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                    .values(
                        state="RETRY_WAIT",
                        state_generation=3,
                        lease_until=None,
                        next_retry_at=datetime.now(UTC) + timedelta(minutes=1),
                    )
                )
                with (
                    pytest.raises(DBAPIError, match="sona_work_transition_invalid"),
                    session.begin_nested(),
                ):
                    session.execute(
                        update(SonaShadowWorkRow)
                        .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                        .values(state="RETRY_EXHAUSTED", state_generation=4, next_retry_at=None)
                    )
            finally:
                retry_savepoint.rollback()
            with (
                pytest.raises(DBAPIError, match="sona_success_evidence_required"),
                session.begin_nested(),
            ):
                session.execute(
                    update(SonaShadowWorkRow)
                    .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                    .values(state="SUCCEEDED", state_generation=3, lease_until=None)
                )
                session.execute(text("SET CONSTRAINTS ml.z_sona_success_evidence IMMEDIATE"))
            with (
                pytest.raises(DBAPIError, match="sona_claim_attempt_required"),
                session.begin_nested(),
            ):
                session.execute(
                    update(SonaShadowWorkRow)
                    .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                    .values(state="SUCCEEDED", state_generation=3, lease_until=None)
                )
                session.execute(
                    insert(SonaShadowEvidenceRow).values(
                        sona_shadow_work_id=work_id,
                        user_id=owner.user_id,
                        recommendation_request_id=request_id,
                        evidence_sha256=_digest(b"{}"),
                        evidence_document=b"{}",
                    )
                )
                session.execute(text("SET CONSTRAINTS ml.z_sona_success_evidence IMMEDIATE"))
            session.execute(
                update(SonaShadowWorkRow)
                .where(SonaShadowWorkRow.sona_shadow_work_id == work_id)
                .values(state="SUCCEEDED", state_generation=3, lease_until=None)
            )
            session.execute(
                insert(SonaShadowAttemptRow).values(
                    sona_shadow_work_id=work_id,
                    user_id=owner.user_id,
                    recommendation_request_id=request_id,
                    attempt_no=1,
                    claim_generation=1,
                    outcome="SUCCEEDED",
                    attempt_document=b"{}",
                    attempt_sha256=_digest(b"{}"),
                    started_at=created_at,
                    finished_at=datetime.now(UTC),
                )
            )
            session.execute(
                insert(SonaShadowEvidenceRow).values(
                    sona_shadow_work_id=work_id,
                    user_id=owner.user_id,
                    recommendation_request_id=request_id,
                    evidence_sha256=_digest(b"{}"),
                    evidence_document=b"{}",
                )
            )
            session.commit()

        with sessions() as session:
            assert session.get(SonaShadowEvidenceRow, work_id) is not None
            session.execute(
                delete(SonaCaptureBundleRow).where(
                    SonaCaptureBundleRow.recommendation_request_id == request_id
                )
            )
            session.commit()
            assert session.get(SonaShadowEvidenceRow, work_id) is None
            assert (
                session.scalar(
                    select(SonaShadowAttemptRow).where(
                        SonaShadowAttemptRow.sona_shadow_work_id == work_id
                    )
                )
                is None
            )
            assert session.get(RecommendationRequestRow, request_id) is not None
            assert (
                session.scalar(
                    select(SonaCaptureLineageCursorRow).where(
                        SonaCaptureLineageCursorRow.recommendation_request_id == request_id
                    )
                )
                is None
            )
    finally:
        engine.dispose()


def test_expiry_purge_uses_database_time_and_preserves_p11_truth(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions() as session:
            owner = _principal(session, "sona-expiry-owner")
            session.commit()
        runtime = SqlAlchemyRecommendationRuntime(sessions)

        def request_at(at: datetime, retention: timedelta) -> UUID:
            service = RecommendationService(
                snapshots=runtime,
                traces=runtime,
                registry=StaticRecommendationVersionRegistry(),
                ids=uuid7,
                clock=lambda: at,
                atomic_writer=runtime,
                snapshot_retention=retention,
            )
            response = service.recommend(
                RecommendationQuery(owner.user_id, RecommendationSurface.RECOMMENDATIONS)
            )
            request_id = response.request.recommendation_request_id
            digest = _digest(b"retention-fixture")
            with sessions() as session:
                session.execute(
                    insert(SonaCaptureBundleRow).values(
                        recommendation_request_id=request_id,
                        user_id=owner.user_id,
                        baseline_snapshot_sha256=bytes.fromhex(
                            response.request.snapshot.input_snapshot_sha256
                        ),
                        temporal_snapshot_sha256=digest,
                        candidate_membership_sha256=digest,
                        p11_ranking_sha256=digest,
                        bundle_sha256=_digest(b"{}"),
                        consent_receipt_sha256=digest,
                        consent_generation=1,
                        cutoff_at_ms=0,
                        interaction_watermark=response.request.snapshot.interaction_watermark,
                        universe_count=0,
                        eligible_count=0,
                        bundle_document=b"{}",
                        created_at=at,
                        expires_at=at + timedelta(days=180),
                    )
                )
                session.commit()
            return request_id

        expired = request_at(datetime.now(UTC) - timedelta(days=181), timedelta(days=182))
        live = request_at(datetime.now(UTC), timedelta(days=181))
        cleanup = SqlAlchemySonaCaptureRetention(sessions)
        assert cleanup.purge_expired(limit=1) == 1
        assert cleanup.purge_expired(limit=1) == 0
        with sessions() as session:
            assert session.get(SonaCaptureBundleRow, expired) is None
            assert session.get(SonaCaptureBundleRow, live) is not None
            assert session.get(RecommendationRequestRow, expired) is not None
            assert session.get(RecommendationRequestRow, live) is not None
        for bad_limit in (0, 101, True):
            with pytest.raises(ValueError, match="purge limit"):
                cleanup.purge_expired(limit=bad_limit)
    finally:
        engine.dispose()

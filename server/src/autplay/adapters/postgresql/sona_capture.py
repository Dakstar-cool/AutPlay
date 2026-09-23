"""Transactional writer for a fully prepared, model-independent Sona bundle.

This adapter does not obtain consent or gather events. Its caller must create the
cutoff-bound temporal snapshot in the same P11 transaction before calling it.
Production composition intentionally does not wire this writer yet.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models import (
    RecommendationTemporalSnapshotRow,
    SonaCaptureBundleRow,
    SonaCaptureLineageCursorRow,
)
from autplay.application.sona_capture import (
    SonaCaptureBundleV1,
    prepare_sona_capture_bundle,
)
from autplay.domain.recommendations import (
    RecommendationInputSnapshot,
    RecommendationResponse,
)
from autplay.domain.sona import SonaTemporalSnapshot


class SqlAlchemySonaCaptureWriter:
    """Insert bundle and cursor without opening or committing a transaction."""

    def write(
        self,
        session: Session,
        *,
        baseline: RecommendationInputSnapshot,
        response: RecommendationResponse,
        temporal: SonaTemporalSnapshot,
        consent_receipt_sha256: str,
        consent_generation: int,
    ) -> SonaCaptureBundleV1:
        if not session.in_transaction():
            raise ValueError("native Sona capture requires the P11 transaction")
        stored = session.scalar(
            select(RecommendationTemporalSnapshotRow)
            .where(
                RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                == temporal.temporal_snapshot_id,
                RecommendationTemporalSnapshotRow.user_id == response.request.query.user_id,
                RecommendationTemporalSnapshotRow.recommendation_input_snapshot_id
                == baseline.reference.snapshot_id,
            )
            .with_for_update()
        )
        if (
            stored is None
            or stored.snapshot_sha256.hex() != temporal.temporal_snapshot_sha256
            or stored.retained_until != temporal.retained_until
            or stored.source_event_count != len(temporal.evidence)
            or not isinstance(stored.snapshot_document, dict)
        ):
            raise ValueError("exact retained Sona temporal snapshot is unavailable")
        bundle = prepare_sona_capture_bundle(
            baseline=baseline,
            response=response,
            temporal=temporal,
            temporal_document=stored.snapshot_document,
            consent_receipt_sha256=consent_receipt_sha256,
            consent_generation=consent_generation,
        )
        expires_at = response.request.created_at + timedelta(days=180)
        session.execute(
            insert(SonaCaptureBundleRow).values(
                recommendation_request_id=bundle.recommendation_request_id,
                user_id=bundle.owner_user_id,
                baseline_snapshot_sha256=bytes.fromhex(bundle.baseline_snapshot_sha256),
                temporal_snapshot_sha256=bytes.fromhex(bundle.temporal_snapshot_sha256),
                candidate_membership_sha256=bytes.fromhex(bundle.candidate_membership_sha256),
                p11_ranking_sha256=bytes.fromhex(bundle.p11_ranking_sha256),
                bundle_sha256=bytes.fromhex(bundle.bundle_sha256),
                consent_receipt_sha256=bytes.fromhex(bundle.consent_receipt_sha256),
                consent_generation=bundle.consent_generation,
                cutoff_at_ms=bundle.cutoff_at_ms,
                interaction_watermark=bundle.interaction_watermark,
                universe_count=bundle.universe_count,
                eligible_count=bundle.eligible_count,
                ineligibility_reason=bundle.ineligibility_reason,
                bundle_document=bundle.document,
                created_at=response.request.created_at,
                expires_at=expires_at,
            )
        )
        session.execute(
            insert(SonaCaptureLineageCursorRow).values(
                recommendation_request_id=bundle.recommendation_request_id,
                user_id=bundle.owner_user_id,
                expires_at=expires_at,
            )
        )
        return bundle
